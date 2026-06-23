import os
import sys
import json
import zlib
import re
import datetime
import subprocess
import concurrent.futures
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import olefile
import pypdf
import urllib.request

app = Flask(__name__, static_folder='.', static_url_path='')

# Find hwp5txt path in the same venv or fallback to PATH
hwp5txt_path = Path(sys.executable).parent / 'hwp5txt'
if not hwp5txt_path.exists():
    hwp5txt_path = 'hwp5txt'
else:
    hwp5txt_path = str(hwp5txt_path)

def log_error(hwp_path, reason):
    log_path = Path(__file__).parent / 'conversion_errors.log'
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] File: {hwp_path}\nError: {reason}\n{'-'*50}\n")
    except Exception:
        pass

def parse_hwp_records(data):
    records = []
    offset = 0
    n = len(data)
    while offset < n:
        if offset + 4 > n:
            break
        header = int.from_bytes(data[offset:offset+4], 'little')
        offset += 4
        rec_type = header & 0x3FF
        level = (header >> 10) & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if offset + 4 > n:
                break
            size = int.from_bytes(data[offset:offset+4], 'little')
            offset += 4
        if offset + size > n:
            rec_data = data[offset:]
            records.append((rec_type, rec_data))
            break
        rec_data = data[offset:offset+size]
        records.append((rec_type, rec_data))
        offset += size
    return records

def parse_para_text(rec_data):
    # Standard HWP inline controls that are 14 bytes (7 words) long
    INLINE_CONTROLS = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 20, 21, 22, 23}
    chars = []
    i = 0
    n = len(rec_data)
    while i < n - 1:
        code = int.from_bytes(rec_data[i:i+2], 'little')
        i += 2
        if code in (0x0D, 0x0A, 0x2029):
            break
        if code in INLINE_CONTROLS:
            i += 14
            continue
        if code == 4:
            i += 14
            continue
        if code == 9:
            chars.append('\t')
            continue
        if 0x20 <= code <= 0xFFFF:
            chars.append(chr(code))
    return "".join(chars)

def extract_olefile_hwp(hwp_path):
    extracted_text = []
    with olefile.OleFileIO(hwp_path) as ole:
        for i in range(100):
            section_name = f'BodyText/Section{i}'
            if not ole.exists(section_name):
                break
            with ole.openstream(section_name) as stream:
                raw = stream.read()
            try:
                data = zlib.decompress(raw, -15)
            except zlib.error:
                data = raw
            
            records = parse_hwp_records(data)
            for rec_type, rec_data in records:
                if rec_type == 67:  # HWPTAG_PARA_TEXT
                    para_str = parse_para_text(rec_data)
                    if para_str:
                        extracted_text.append(para_str)
    return "\n".join(extracted_text)

def extract_strings_hwp(hwp_path):
    try:
        with open(hwp_path, 'rb') as f:
            data = f.read()
    except Exception as e:
        raise RuntimeError(f"파일을 읽을 수 없습니다: {e}")
        
    korean_pattern = re.compile(r'[가-힣]')
    extracted = []
    chars = []
    
    for i in range(0, len(data) - 1, 2):
        code = int.from_bytes(data[i:i+2], 'little')
        if 0x20 <= code <= 0xFFFF:
            chars.append(chr(code))
        else:
            if chars:
                s = "".join(chars)
                if korean_pattern.search(s):
                    extracted.append(s.strip())
                chars = []
    if chars:
        s = "".join(chars)
        if korean_pattern.search(s):
            extracted.append(s.strip())
            
    return "\n".join(extracted)

def extract_hwp_text(hwp_path):
    # Stage 1: hwp5txt
    try:
        result = subprocess.run([hwp5txt_path, str(hwp_path)], capture_output=True, text=True, timeout=30, encoding='utf-8')
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout, 'hwp5txt'
    except Exception:
        pass
        
    # Stage 2: olefile binary parsing
    try:
        text = extract_olefile_hwp(hwp_path)
        if text.strip():
            return text, 'olefile'
    except Exception:
        pass
        
    # Stage 3: strings
    try:
        text = extract_strings_hwp(hwp_path)
        if text.strip():
            return text, 'strings'
    except Exception:
        pass
        
    raise ValueError("All HWP extraction stages failed or returned empty text.")

def extract_pdf_text_ocr_vision(pdf_path, api_key=None):
    import fitz
    import Vision
    from Quartz import CGImageSourceCreateWithData, CGImageSourceCreateImageAtIndex
    from Foundation import NSData
    import base64
    import urllib.request
    import json
    import ssl
    import time
    
    doc = fitz.open(pdf_path)
    text_parts = []
    print(f"[OCR] Starting macOS Vision OCR for {pdf_path} (Total {len(doc)} pages)...", file=sys.stderr)
    
    for i, page in enumerate(doc):
        if i % 10 == 0 or i == len(doc) - 1:
            print(f"[OCR] Processing page {i+1}/{len(doc)}...", file=sys.stderr)
            
        page_text = ""
        # Try local Vision OCR first
        try:
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            data = NSData.dataWithBytes_length_(img_bytes, len(img_bytes))
            image_source = CGImageSourceCreateWithData(data, None)
            if image_source:
                cg_image = CGImageSourceCreateImageAtIndex(image_source, 0, None)
                if cg_image:
                    page_texts = []
                    def handler(request, error):
                        if error:
                            return
                        observations = request.results()
                        if observations:
                            for obs in observations:
                                candidates = obs.topCandidates_(1)
                                if candidates:
                                    page_texts.append(candidates[0].string())
                                    
                    request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(handler)
                    request.setRecognitionLanguages_(["ko-KR", "en-US"])
                    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
                    
                    handler_obj = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
                    success, error = handler_obj.performRequests_error_([request], None)
                    
                    if success and page_texts:
                        page_text = "\n".join(page_texts)
        except Exception as ocr_page_e:
            print(f"[OCR] Local Vision OCR failed on page {i+1}: {ocr_page_e}", file=sys.stderr)
            page_text = ""
            
        # Per-page fallback to Gemini API if local Vision fails
        if not page_text.strip() and api_key:
            print(f"[OCR] Local Vision failed on page {i+1}. Querying Gemini OCR fallback...", file=sys.stderr)
            time.sleep(1.5)
            try:
                pix = page.get_pixmap(dpi=150)
                img_bytes = pix.tobytes("png")
                base64_image = base64.b64encode(img_bytes).decode('utf-8')
                
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
                payload = {
                    "contents": [
                        {
                            "parts": [
                                {"text": "이 이미지에 적힌 한글과 영문 텍스트를 보이지 않는 서식이나 누락 없이 그대로 추출해서 텍스트로 반환해주세요. 임의의 부연 설명이나 요약 없이 텍스트 알맹이만 그대로 출력해주세요."},
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": base64_image
                                    }
                                }
                            ]
                        }
                    ],
                    "generationConfig": {"temperature": 0.1}
                }
                
                req_data = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(url, data=req_data, headers={'Content-Type': 'application/json'})
                
                ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=45, context=ctx) as response:
                    res_data = json.loads(response.read().decode('utf-8'))
                    candidates = res_data.get('candidates', [])
                    if candidates:
                        parts = candidates[0].get('content', {}).get('parts', [])
                        page_text = "".join([p.get('text', '') for p in parts]).strip()
            except Exception as gemini_page_e:
                print(f"[OCR] Gemini OCR fallback failed on page {i+1}: {gemini_page_e}", file=sys.stderr)
                
        if page_text:
            text_parts.append(page_text)
            
    print(f"[OCR] Completed OCR for {pdf_path}.", file=sys.stderr)
    return "\n\n".join(text_parts)

def extract_pdf_text_ocr_gemini(pdf_path, api_key):
    import fitz
    import urllib.request
    import json
    import base64
    import ssl
    import time
    
    doc = fitz.open(pdf_path)
    text_parts = []
    print(f"[OCR-Gemini] Starting Gemini OCR for {pdf_path} (Total {len(doc)} pages)...", file=sys.stderr)
    
    for i, page in enumerate(doc):
        if i > 0:
            time.sleep(1.5)
            
        if i % 5 == 0 or i == len(doc) - 1:
            print(f"[OCR-Gemini] Requesting page {i+1}/{len(doc)} to Gemini API...", file=sys.stderr)
            
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        base64_image = base64.b64encode(img_bytes).decode('utf-8')
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": "이 이미지에 적힌 한글과 영문 텍스트를 보이지 않는 서식이나 누락 없이 그대로 추출해서 텍스트로 반환해주세요. 임의의 부연 설명이나 요약 없이 텍스트 알맹이만 그대로 출력해주세요."},
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": base64_image
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1
            }
        }
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        
        try:
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=45, context=ctx) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                candidates = res_data.get('candidates', [])
                if candidates:
                    parts = candidates[0].get('content', {}).get('parts', [])
                    text_out = "".join([p.get('text', '') for p in parts]).strip()
                    if text_out:
                        text_parts.append(text_out)
        except Exception as e:
            print(f"[OCR-Gemini] Error on page {i+1}: {e}", file=sys.stderr)
            continue
            
    print(f"[OCR-Gemini] Completed Gemini OCR for {pdf_path}.", file=sys.stderr)
    return "\n\n".join(text_parts)

def clean_frontmatter_and_callouts(content):
    # 1) frontmatter 제거
    cleaned = re.sub(r'^---\n[\s\S]*?\n---\n*', '', content)
    
    # 2) callout 제거 (> [!...)
    lines = cleaned.splitlines()
    body_lines = []
    in_callout = False
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('>') and '[!' in stripped:
            in_callout = True
            continue
        if in_callout:
            if stripped.startswith('>'):
                continue
            else:
                in_callout = False
        body_lines.append(line)
        
    return "\n".join(body_lines).strip()

def is_corrupted_korean(text):
    if not text:
        return True
        
    # 1. PUA (Private Use Area) chars - common in corrupted font mapping conversion
    pua_chars = len(re.findall(r'[\ue000-\uf8ff]', text))
    
    # 2. Typical gibberish/corrupted letters generated during PDF font decode
    corrupt_signals = len(re.findall(r'[☜☞☎巒藕靂欠]', text))
    
    # 3. Unicode replacement characters (question marks)
    replacement_chars = len(re.findall(r'\uFFFD', text))
    
    # 4. Korean words containing weird inline English letters or punctuation
    # (e.g. "최 足오- 혀(오☜")
    weird_patterns = len(re.findall(r'[가-힣][^가-힣\s]{1,5}[가-힣]', text))
    
    text_len = len(text)
    weird_density = (weird_patterns / text_len) if text_len > 0 else 0
    
    if pua_chars > 0 or corrupt_signals > 0 or replacement_chars > 0 or (weird_patterns > 5 and weird_density > 0.005):
        print(f"[Corrupt-Detection] Length={text_len}, PUA={pua_chars}, CorruptSignals={corrupt_signals}, ReplacementChars={replacement_chars}, WeirdKoreanPatterns={weird_patterns} (density={weird_density:.4f})", file=sys.stderr)
        
    # Switch to OCR if PUA chars, corrupt symbols, replacement chars are present,
    # or if the density of weird patterns is high (over 1.5%) and happens at least 15 times.
    if pua_chars > 3 or corrupt_signals > 2 or replacement_chars > 3 or (weird_patterns > 15 and weird_density > 0.015):
        return True
    return False

def extract_pdf_text(pdf_path, api_key=None, force_ocr=False):
    combined_text = ""
    method = 'failed'
    
    if not force_ocr:
        try:
            reader = pypdf.PdfReader(pdf_path)
            text_parts = []
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text_parts.append(extracted)
            combined_text = "\n".join(text_parts)
            method = 'pypdf'
        except Exception as e:
            combined_text = ""
            method = 'failed'
            print(f"pypdf extraction failed: {e}", file=sys.stderr)
        
    is_corrupt = False
    if not force_ocr and combined_text.strip():
        is_corrupt = is_corrupted_korean(combined_text)
        if is_corrupt:
            print(f"Detected corrupted/gibberish text in {pdf_path} (length={len(combined_text)}). Switching to OCR automatically...", file=sys.stderr)
            
    if force_ocr or is_corrupt or len(combined_text.strip()) < 100:
        print(f"Running OCR for {pdf_path} (force_ocr={force_ocr}, is_corrupt={is_corrupt}, text_len={len(combined_text)})...", file=sys.stderr)
        
        import platform
        is_mac = platform.system() == "Darwin"
        
        if is_mac:
            try:
                ocr_text = extract_pdf_text_ocr_vision(pdf_path, api_key)
                if ocr_text.strip():
                    return ocr_text, 'Vision OCR'
            except Exception as ocr_e:
                print(f"macOS Vision OCR failed: {ocr_e}", file=sys.stderr)
                pass
                
        if api_key:
            try:
                ocr_text = extract_pdf_text_ocr_gemini(pdf_path, api_key)
                if ocr_text.strip():
                    return ocr_text, 'Gemini OCR'
            except Exception as gemini_e:
                print(f"Gemini OCR failed: {gemini_e}", file=sys.stderr)
                pass
                
        if not is_mac and not api_key:
            raise ValueError(
                "스캔된(이미지 형식) PDF 파일입니다. 텍스트를 추출하려면 OCR 기능이 필요하지만, "
                "현재 macOS 환경이 아니거나 Gemini API Key가 입력되지 않아 OCR을 진행할 수 없습니다."
            )
        else:
            raise ValueError(
                "스캔된(이미지 형식) PDF 파일의 OCR 텍스트 추출에 실패했습니다. "
                "PDF 파일이 손상되었거나 이미지 해상도가 너무 낮을 수 있습니다."
            )
            
    return combined_text, method

def merge_broken_lines(text):
    bible_books = (
        r"창|출|레|민|신|수|삿|룻|삼상|삼하|왕상|왕하|대상|대하|스|느|에|욥|시|잠|전|아|사|렘|애|겔|단|호|욜|암|옵|욘|미|나|하|습|학|슥|말|"
        r"마|막|눅|요|행|롬|고전|고후|갈|엡|빌|골|살전|살후|딤전|딤후|딛|몬|히|야|벧전|벧후|요일|요이|요삼|유|계"
    )
    bible_ref_regex = re.compile(rf'\b({bible_books})\s*\d+[장:절]')
    
    def join_two_lines(line1, line2):
        if not line1:
            return line2
        if not line2:
            return line1
        last_char = line1[-1]
        first_char = line2[0]
        
        josa_list = ('은', '는', '이', '가', '을', '를', '에', '의', '와', '과', '로', '도', '만', '며', '고', '서', '나', '든', '라', '요', '서', '야', '지')
        josa_words = ('으로', '에서', '에게', '하며', '하고', '하여', '했다', '한다', '이다', '이라')
        
        def is_korean(char):
            return '\uac00' <= char <= '\ud7a3'
            
        if is_korean(last_char) and is_korean(first_char):
            is_josa = first_char in josa_list or any(line2.startswith(w) for w in josa_words)
            if is_josa:
                return line1 + line2
            else:
                return line1 + " " + line2
        return line1 + " " + line2

    def merge_paragraph(lines_list):
        if not lines_list:
            return ""
        res = lines_list[0]
        for line in lines_list[1:]:
            res = join_two_lines(res, line)
        return res

    lines = text.splitlines()
    merged_lines = []
    title_prefixes = ('chapter', 'part', '제 ', '제', '장', '절', '부록', '서론', '결론', '목차', 'content', 'index')
    current_paragraph = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_paragraph:
                merged_lines.append(merge_paragraph(current_paragraph))
                current_paragraph = []
            merged_lines.append("")
            continue
            
        is_list = stripped.startswith(('-', '*', '•', '·', '▶', '▷', '◆', '◇', '○', '●')) or \
                  re.match(r'^\d+\.', stripped)
        is_instruction = stripped.startswith(('(', '[', '{', '<'))
        is_title_prefix = any(stripped.lower().startswith(p) for p in title_prefixes)
        is_bible_ref = bool(bible_ref_regex.search(stripped))
        
        if is_list or is_instruction or is_title_prefix or is_bible_ref:
            if current_paragraph:
                merged_lines.append(merge_paragraph(current_paragraph))
                current_paragraph = []
            current_paragraph.append(stripped)
            if is_list or is_title_prefix or is_bible_ref:
                merged_lines.append(current_paragraph[0])
                current_paragraph = []
            continue
            
        if current_paragraph:
            prev_line = current_paragraph[-1]
            is_sentence_end = prev_line.endswith(('.', '?', '!', '"', "'", ')', '}', ']')) or \
                               prev_line.endswith(('다', '요', '오', '죠', '냐', '디', '음', '임', '기', '코'))
            
            if is_sentence_end:
                merged_lines.append(merge_paragraph(current_paragraph))
                current_paragraph = [stripped]
            else:
                current_paragraph.append(stripped)
        else:
            current_paragraph.append(stripped)
            
    if current_paragraph:
        merged_lines.append(merge_paragraph(current_paragraph))
        
    final_output = []
    prev_was_blank = False
    for line in merged_lines:
        if not line:
            if not prev_was_blank:
                final_output.append("")
                prev_was_blank = True
        else:
            final_output.append(line)
            prev_was_blank = False
            
    return "\n".join(final_output)

BIBLE_MAP = {
    "창": "창세기", "출": "출애굽기", "레": "레위기", "민": "민수기", "신": "신명기",
    "수": "여호수아", "삿": "사사기", "룻": "룻기", "삼상": "사무엘상", "삼하": "사무엘하",
    "왕상": "열왕기상", "왕하": "열왕기하", "대상": "역대기상", "대하": "역대기하",
    "스": "에스라", "느": "느헤미야", "에": "에스더", "욥": "욥기", "시": "시편",
    "잠": "잠언", "전": "전도서", "아": "아가", "사": "이사야", "렘": "예레미야",
    "애": "예레미야애가", "겔": "에스겔", "단": "다니엘", "호": "호세아", "욜": "요엘",
    "암": "아모스", "옵": "오바댜", "욘": "요나", "미": "미가", "나": "나훔",
    "하": "하박국", "습": "스바냐", "학": "학개", "슥": "스가랴", "말": "말라기",
    "마": "마태복음", "막": "마가복음", "눅": "누가복음", "요": "요한복음", "행": "사도행전",
    "롬": "로마서", "고전": "고린도전서", "고후": "고린도후서", "갈": "갈라디아서",
    "엡": "에베소서", "빌": "빌립보서", "골": "골로새서", "살전": "데살로니가전서",
    "살후": "데살로니가후서", "딤전": "디모데전서", "딤후": "디모데후서", "딛": "디도서",
    "몬": "빌레몬서", "히": "히브리서", "야": "야고보서", "벧전": "베드로전서",
    "벧후": "베드로후서", "요일": "요한일서", "요이": "요한이서", "요삼": "요한삼서",
    "유": "유다서", "계": "요한계시록"
}

def extract_bible_tag(bible_text, title="", body_text=""):
    if bible_text:
        sorted_keys = sorted(BIBLE_MAP.keys(), key=len, reverse=True)
        sorted_vals = sorted(BIBLE_MAP.values(), key=len, reverse=True)
        
        for val in sorted_vals:
            if val in bible_text:
                return val
        
        for key in sorted_keys:
            pattern = rf'(?:^|[\s\d:,-]){re.escape(key)}(?:$|[\s\d:,-])'
            if re.search(pattern, bible_text):
                return BIBLE_MAP[key]
                
    for text in [title, body_text]:
        if not text:
            continue
        for val in sorted(BIBLE_MAP.values(), key=len, reverse=True):
            if val in text:
                return val
                
    return None

SYSTEM_PROMPT = """당신은 목회자 설교문/성경 묵상 문서를 분석하는 전문가입니다. 
한국 교회의 복음주의-성결 신학 전통을 이해하며, 웨슬리안 신학의 맥락에서 설교를 분석합니다.

반드시 다음 형식으로 정확히 출력하세요 (특히 TAGS 세션은 성경 본문 태그를 제외하고 설교 주제와 관련된 핵심 단어 10개를 샵(#)을 붙여서 쉼표로 구분해 반드시 작성해야 합니다):

TITLE: 설교 제목
BIBLE: 성경 구절 (예: 로마서 8:28)
THEME: 설교의 핵심 주제
KEYWORDS: 키워드1, 키워드2, 키워드3

SUMMARY_THREE:
- 첫 번째 핵심 내용
- 두 번째 핵심 내용
- 세 번째 핵심 내용

SUMMARY_FULL:
2-3 문장으로 전체 내용 요약

TAGS:
#태그1, #태그2, #태그3, #태그4, #태그5, #태그6, #태그7, #태그8, #태그9, #태그10"""

def get_gemini_analysis(text, api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    truncated_text = text[:30000]
    
    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "다음 설교문/묵상을 분석해주세요:\n\n" + truncated_text}]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 2048,
            "temperature": 0.4
        }
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            'Content-Type': 'application/json'
        }
    )
    
    try:
        import ssl
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=45, context=ctx) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            candidates = res_data.get('candidates', [])
            if not candidates:
                raise ValueError("No candidates returned from Gemini API")
            
            parts = candidates[0].get('content', {}).get('parts', [])
            text_out = "".join([p.get('text', '') for p in parts]).strip()
            if not text_out:
                raise ValueError("Empty response text from Gemini candidates")
            return text_out
    except Exception as e:
        raise RuntimeError(f"Gemini API 호출 에러: {str(e)}")

def parse_gemini_response(response_text):
    result = {
        "title": "",
        "bible_text": "",
        "theme": "",
        "keywords": "",
        "threePoint": "",
        "summary": "",
        "tags": []
    }
    
    def safe_str(s):
        return s.strip() if s else ""
        
    title_match = re.search(r'(?:TITLE|Title|제목)\s*:\s*(.+)', response_text, re.IGNORECASE)
    bible_match = re.search(r'(?:BIBLE|Bible|성경|본문)\s*:\s*(.+)', response_text, re.IGNORECASE)
    theme_match = re.search(r'(?:THEME|Theme|주제)\s*:\s*(.+)', response_text, re.IGNORECASE)
    keywords_match = re.search(r'(?:KEYWORDS|Keywords|핵심어|키워드)\s*:\s*(.+)', response_text, re.IGNORECASE)
    
    if title_match: result["title"] = safe_str(title_match.group(1))
    if bible_match: result["bible_text"] = safe_str(bible_match.group(1))
    if theme_match: result["theme"] = safe_str(theme_match.group(1))
    if keywords_match: result["keywords"] = safe_str(keywords_match.group(1))
    
    three_match = re.search(
        r'(?:SUMMARY_THREE|Summary_Three|세줄요약|3가지 핵심 요약)\s*:\s*\n?([\s\S]+?)(?=\n(?:SUMMARY_FULL|Summary_Full|전체요약|요약|TAGS|Tags|태그):|$)',
        response_text,
        re.IGNORECASE
    )
    if three_match:
        raw_three = safe_str(three_match.group(1))
        formatted_lines = []
        for line in raw_three.splitlines():
            line_str = line.strip().lstrip('-').lstrip('*').strip()
            if line_str:
                formatted_lines.append(f"- {line_str}")
        result["threePoint"] = "\n".join(formatted_lines)
        
    summary_match = re.search(
        r'(?:SUMMARY_FULL|Summary_Full|전체요약|요약)\s*:\s*\n?([\s\S]+?)(?=\n(?:TAGS|Tags|태그|핵심\s*태그):|$)',
        response_text,
        re.IGNORECASE
    )
    if summary_match: 
        result["summary"] = safe_str(summary_match.group(1))
    
    tags_match = re.search(
        r'(?:TAGS|Tags|태그|핵심\s*태그)\s*:\s*\n?([\s\S]+)',
        response_text,
        re.IGNORECASE
    )
    if tags_match:
        raw_tags = safe_str(tags_match.group(1))
        tags_list = []
        
        # Method 1: Find all terms starting with #
        hash_tags = re.findall(r'#([가-힣a-zA-Z0-9_]+)', raw_tags)
        if hash_tags:
            tags_list = [t.strip() for t in hash_tags if t.strip()]
            
        # Method 2: Fallback to comma/whitespace/hyphen splits
        if not tags_list:
            for t in re.split(r'[,\s\n\-\*•·]+', raw_tags):
                t_clean = t.replace("#", "").strip()
                if t_clean and not t_clean.startswith('`') and len(t_clean) > 1:
                    tags_list.append(t_clean)
                    
        result["tags"] = tags_list[:10]
        
    return result

def build_frontmatter(parsed):
    def esc(s):
        return s.replace('"', '\\"')
        
    fm = "---\n"
    fm += f'title: "{esc(parsed.get("title") or "제목 없음")}"\n'
    fm += f'bible_text: "{esc(parsed.get("bible_text") or "")}"\n'
    fm += f'theme: "{esc(parsed.get("theme") or "")}"\n'
    fm += f'keywords: "{esc(parsed.get("keywords") or "")}"\n'
    
    # 세 줄 요약 추가
    three = parsed.get("threePoint") or ""
    three_lines = []
    if three.strip():
        for line in three.splitlines():
            cleaned_line = line.strip().lstrip('-').strip()
            if cleaned_line:
                three_lines.append(cleaned_line)
    if three_lines:
        fm += "summary_three:\n"
        for line in three_lines:
            fm += f'  - "{esc(line)}"\n'
            
    # 전체 요약 추가 (YAML literal block scalar 형식)
    summary = parsed.get("summary") or ""
    if summary.strip():
        fm += "summary_full: |\n"
        for line in summary.splitlines():
            if line.strip():
                fm += f"  {line.strip()}\n"
                
    tags = parsed.get("tags") or []
    if tags:
        fm += "tags:\n"
        for t in tags:
            fm += f"  - {t}\n"
    fm += "---\n\n"
    return fm

def build_callouts(parsed):
    out = ""
    three = parsed.get("threePoint") or ""
    if three.strip():
        out += "> [!note] 🔑 3가지 핵심 요약\n"
        for line in three.splitlines():
            if line.strip():
                out += f"> {line.strip()}\n"
        out += "\n"
        
    summary = parsed.get("summary") or ""
    if summary.strip():
        out += "> [!summary] 📖 전체 요약\n"
        for line in summary.splitlines():
            if line.strip():
                out += f"> {line.strip()}\n"
        out += "\n"
        
    return out

def format_markdown(text, file_path, api_key=None):
    bible_books = (
        r"창|출|레|민|신|수|삿|룻|삼상|삼하|왕상|왕하|대상|대하|스|느|에|욥|시|잠|전|아|사|렘|애|겔|단|호|욜|암|옵|욘|미|나|하|습|학|슥|말|"
        r"마|막|눅|요|행|롬|고전|고후|갈|엡|빌|골|살전|살후|딤전|딤후|딛|몬|히|야|벧전|벧후|요일|요이|요삼|유|계"
    )
    bible_ref_regex = re.compile(rf'\b({bible_books})\s*\d+[장:절]')
    
    list_chars = '•·▶▷◆◇○●'
    list_regex = re.compile(rf'^[{re.escape(list_chars)}]\s*(.*)')
    
    # 문장 병합 수행 (줄바꿈 결합)
    merged_text = merge_broken_lines(text)
    
    processed_lines = []
    lines = merged_text.splitlines()
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            processed_lines.append("")
            continue
            
        list_match = list_regex.match(stripped)
        if list_match:
            processed_lines.append(f"- {list_match.group(1)}")
            continue
            
        if bible_ref_regex.search(stripped):
            processed_lines.append(f"> {stripped}")
            continue
            
        # 헤더 자동 감지 (오탐 방지 강화)
        if (len(stripped) < 30 and
            not stripped.startswith(('(', '[', '{', '<', '>', '-')) and
            not stripped.endswith(('.', ',', '다', '요', '오', '죠', '냐', '디', '음', '임', '기', '코', ')', '}', ']', '>', '-'))):
            processed_lines.append(f"## {stripped}")
            continue
            
        processed_lines.append(stripped)
        
    final_lines = []
    prev_was_blank = False
    for line in processed_lines:
        if not line:
            if not prev_was_blank:
                final_lines.append("")
                prev_was_blank = True
        else:
            final_lines.append(line)
            prev_was_blank = False
            
    markdown_body = "\n".join(final_lines)
    
    if api_key:
        try:
            response_text = get_gemini_analysis(markdown_body, api_key)
            try:
                with open(Path(__file__).parent / 'gemini_response_debug.txt', 'w', encoding='utf-8') as f:
                    f.write(response_text)
            except Exception:
                pass
            parsed = parse_gemini_response(response_text)
            
            # 성경책 이름 태그 추출 및 추가
            bible_tag = extract_bible_tag(parsed.get("bible_text"), parsed.get("title"), markdown_body)
            
            unique_tags = []
            for t in parsed.get("tags") or []:
                if t not in unique_tags:
                    unique_tags.append(t)
                    
            if bible_tag:
                if bible_tag in unique_tags:
                    unique_tags.remove(bible_tag)
                unique_tags.insert(0, bible_tag)
            
            # 최종 태그 리스트를 정확히 10개로 제한
            parsed["tags"] = unique_tags[:10]
                
            frontmatter = build_frontmatter(parsed)
            callouts = build_callouts(parsed)
            return frontmatter + callouts + markdown_body
        except Exception as e:
            log_error(file_path, f"Gemini 요약 생성 실패: {str(e)}")
            # Fall back to standard frontmatter
            pass
            
    today_str = datetime.date.today().isoformat()
    title = file_path.stem
    source = file_path.name
    ext = file_path.suffix.lower()
    tag = "pdf-converted" if ext == ".pdf" else "hwp-converted"
    
    frontmatter = (
        "---\n"
        f'title: "{title}"\n'
        f'source: "{source}"\n'
        f'converted: "{today_str}"\n'
        "tags:\n"
        f"  - {tag}\n"
        "---\n\n"
    )
    return frontmatter + markdown_body

def convert_file(file_path, dest_path, overwrite, api_key=None, force_ocr=False):
    filename = file_path.name
    if not overwrite and dest_path.exists():
        return filename, 'skip', 'skip'
        
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Intelligent Text Cache Reuse
        reused_text = None
        if overwrite and dest_path.exists():
            try:
                with open(dest_path, 'r', encoding='utf-8') as f:
                    existing_content = f.read()
                
                body_text = clean_frontmatter_and_callouts(existing_content)
                if body_text.strip() and len(body_text) > 100 and not is_corrupted_korean(body_text):
                    reused_text = body_text
                    print(f"Reusing clean extracted text from existing markdown for {filename} to refresh Gemini metadata...", file=sys.stderr)
            except Exception as reuse_e:
                print(f"Failed to reuse existing text for {filename}: {reuse_e}", file=sys.stderr)

        if reused_text:
            text = reused_text
            method = 'Cached Text'
        else:
            ext = file_path.suffix.lower()
            if ext == '.pdf':
                text, method = extract_pdf_text(file_path, api_key, force_ocr)
            else:
                text, method = extract_hwp_text(file_path)
            
        if not text.strip():
            raise ValueError("No text content could be extracted")
        
        markdown_content = format_markdown(text, file_path, api_key)
        with open(dest_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
            
        if api_key:
            method += ' + Gemini'
        return filename, 'ok', method
    except Exception as e:
        log_error(file_path, str(e))
        return filename, 'fail', '실패'

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/files', methods=['GET'])
def get_files():
    folder = request.args.get('folder', '').strip()
    recursive = request.args.get('recursive', 'false').lower() == 'true'
    if not folder:
        return jsonify({"error": "folder parameter is required"}), 400
        
    folder_path = Path(folder).expanduser().resolve()
    if not folder_path.exists() or not folder_path.is_dir():
        return jsonify({"files": [], "count": 0, "error": "폴더가 존재하지 않거나 디렉토리가 아닙니다."}), 200
        
    detected_files = []
    try:
        valid_extensions = {'.hwp', '.pdf'}
        if recursive:
            for p in folder_path.rglob('*'):
                if p.is_file() and p.suffix.lower() in valid_extensions:
                    detected_files.append(p.name)
        else:
            for p in folder_path.glob('*'):
                if p.is_file() and p.suffix.lower() in valid_extensions:
                    detected_files.append(p.name)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
        
    return jsonify({
        "files": sorted(detected_files),
        "count": len(detected_files)
    })

@app.route('/convert', methods=['POST'])
def convert():
    data = request.json or {}
    input_folder = data.get('input_folder', '').strip()
    vault_path = data.get('vault_path', '').strip()
    overwrite = data.get('overwrite', False)
    workers = int(data.get('workers', 4))
    recursive = data.get('recursive', False)
    api_key = data.get('api_key', '').strip()
    force_ocr = data.get('force_ocr', False)
    
    if not input_folder or not vault_path:
        return jsonify({"error": "input_folder and vault_path are required"}), 400
        
    input_path = Path(input_folder).expanduser().resolve()
    vault_path_resolved = Path(vault_path).expanduser().resolve()
    
    if not input_path.exists() or not input_path.is_dir():
        return jsonify({"error": "입력 폴더가 존재하지 않습니다."}), 400
        
    files_to_convert = []
    valid_extensions = {'.hwp', '.pdf'}
    
    request_files = data.get('files', [])
    if request_files:
        for f in request_files:
            file_path = input_path / f
            if file_path.exists() and file_path.is_file():
                files_to_convert.append(file_path)
    else:
        if recursive:
            for p in input_path.rglob('*'):
                if p.is_file() and p.suffix.lower() in valid_extensions:
                    files_to_convert.append(p)
        else:
            for p in input_path.glob('*'):
                if p.is_file() and p.suffix.lower() in valid_extensions:
                    files_to_convert.append(p)
                
    total_files = len(files_to_convert)
    
    def generate():
        ok_count = 0
        fail_count = 0
        skip_count = 0
        
        yield json.dumps({"type": "start", "total": total_files}, ensure_ascii=False) + "\n"
        
        if total_files == 0:
            yield json.dumps({"type": "done", "total": 0, "ok": 0, "fail": 0, "skip": 0}, ensure_ascii=False) + "\n"
            return
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_file = {}
            for file_path in files_to_convert:
                relative_path = file_path.relative_to(input_path)
                dest_path = vault_path_resolved / relative_path.with_suffix('.md')
                future = executor.submit(convert_file, file_path, dest_path, overwrite, api_key, force_ocr)
                future_to_file[future] = file_path
                
            for future in concurrent.futures.as_completed(future_to_file):
                file_path = future_to_file[future]
                relative_file_path = str(file_path.relative_to(input_path))
                try:
                    filename, status, method = future.result()
                except Exception as e:
                    status = 'fail'
                    method = '실패'
                    log_error(file_path, f"Thread execution error: {str(e)}")
                    
                if status == 'ok':
                    ok_count += 1
                elif status == 'skip':
                    skip_count += 1
                else:
                    fail_count += 1
                    
                yield json.dumps({
                    "type": "progress",
                    "file": relative_file_path,
                    "status": status,
                    "method": method
                }, ensure_ascii=False) + "\n"
                
        yield json.dumps({
            "type": "done",
            "total": total_files,
            "ok": ok_count,
            "fail": fail_count,
            "skip": skip_count
        }, ensure_ascii=False) + "\n"
        
    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')


@app.route('/open-vault', methods=['POST'])
def open_vault():
    data = request.json or {}
    vault_path = data.get('vault_path', '').strip()
    if not vault_path:
        return jsonify({"error": "vault_path is required"}), 400
        
    resolved_path = Path(vault_path).expanduser().resolve()
    if not resolved_path.exists():
        resolved_path.mkdir(parents=True, exist_ok=True)
        
    try:
        subprocess.run(["open", str(resolved_path)], check=True)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5001, debug=True)
