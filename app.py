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

def extract_pdf_text(pdf_path):
    try:
        reader = pypdf.PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text_parts.append(extracted)
        return "\n".join(text_parts)
    except Exception as e:
        raise ValueError(f"PDF 텍스트 추출 중 오류가 발생했습니다: {str(e)}")

def format_markdown(text, file_path):
    today_str = datetime.date.today().isoformat()
    title = file_path.stem
    source = file_path.name
    ext = file_path.suffix.lower()
    tag = "pdf-converted" if ext == ".pdf" else "hwp-converted"
    
    frontmatter = (
        "---\n"
        f"title: \"{title}\"\n"
        f"source: \"{source}\"\n"
        f"converted: \"{today_str}\"\n"
        "tags:\n"
        f"  - {tag}\n"
        "---\n"
    )
    
    bible_books = (
        r"창|출|레|민|신|수|삿|룻|삼상|삼하|왕상|왕하|대상|대하|스|느|에|욥|시|잠|전|아|사|렘|애|겔|단|호|욜|암|옵|욘|미|나|하|습|학|슥|말|"
        r"마|막|눅|요|행|롬|고전|고후|갈|엡|빌|골|살전|살후|딤전|딤후|딛|몬|히|야|벧전|벧후|요일|요이|요삼|유|계"
    )
    # Matches book abbreviations followed by numbers and 장/절/:
    bible_ref_regex = re.compile(rf'\b({bible_books})\s*\d+[장:절]')
    
    list_chars = '•·▶▷◆◇○●'
    list_regex = re.compile(rf'^[{re.escape(list_chars)}]\s*(.*)')
    
    processed_lines = []
    lines = text.splitlines()
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            processed_lines.append("")
            continue
            
        # 1. List item rule
        list_match = list_regex.match(stripped)
        if list_match:
            processed_lines.append(f"- {list_match.group(1)}")
            continue
            
        # 2. Bible reference rule
        if bible_ref_regex.search(stripped):
            processed_lines.append(f"> {stripped}")
            continue
            
        # 3. Short lines heading rule (< 30 chars, not ending in ., ,, 다, 요, 니다)
        if (len(stripped) < 30 and
            not stripped.endswith('.') and
            not stripped.endswith(',') and
            not stripped.endswith('다') and
            not stripped.endswith('요') and
            not stripped.endswith('니다')):
            processed_lines.append(f"## {stripped}")
            continue
            
        # 4. Standard text
        processed_lines.append(stripped)
        
    # Collapse consecutive blank lines
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
            
    return frontmatter + "\n".join(final_lines)

def convert_file(file_path, dest_path, overwrite):
    filename = file_path.name
    if not overwrite and dest_path.exists():
        return filename, 'skip', 'skip'
        
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ext = file_path.suffix.lower()
        if ext == '.pdf':
            text = extract_pdf_text(file_path)
            method = 'pypdf'
        else:
            text, method = extract_hwp_text(file_path)
            
        if not text.strip():
            raise ValueError("No text content could be extracted")
        
        markdown_content = format_markdown(text, file_path)
        with open(dest_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
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
        overwrite = True
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
                future = executor.submit(convert_file, file_path, dest_path, overwrite)
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
