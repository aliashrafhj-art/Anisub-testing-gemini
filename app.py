import os
import requests
import subprocess
import threading
import uuid
import time
import json
import shutil
from flask import Flask, render_template, request, jsonify, send_file
from extractor import extract_from_episode_page
from translator import convert_vtt_to_srt, translate_google, translate_gemini
from uploader import upload_to_telegram

# ফন্ট সেটআপ - লিংকে SolaimanLipi টাইপ ফন্ট রাখা জরুরি
FONTS = {
    'SolaimanLipi': 'https://raw.githubusercontent.com/maateen/bangla-web-fonts/master/fonts/SolaimanLipi/SolaimanLipi.ttf',
    'NotoSansBengali': 'https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansBengali/NotoSansBengali-Regular.ttf'
}

def setup_fonts():
    os.makedirs('/tmp/fonts', exist_ok=True)
    for name, url in FONTS.items():
        path = f'/tmp/fonts/{name}.ttf'
        if not os.path.exists(path):
            try:
                r = requests.get(url, timeout=30)
                open(path, 'wb').write(r.content)
                print(f'Font downloaded: {name}')
            except Exception as e:
                print(f'Font failed: {name}: {e}')
    # ফন্ট ক্যাশ আপডেট (যাতে FFmpeg ফন্টগুলো খুঁজে পায়)
    subprocess.run(['fc-cache', '-fv', '/tmp/fonts'], capture_output=True)

setup_fonts()

app = Flask(__name__)
os.makedirs('/tmp/anisub', exist_ok=True)
tasks = {}

# --- Helper Functions ---
def get_duration(file_path):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(res.stdout.strip())
    except: return None

def parse_time_to_sec(time_str):
    try:
        h, m, s = time_str.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except: return 0

# --- Flask Routes (Same as before) ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/extract', methods=['POST'])
def extract():
    data = request.json
    url = data.get('url')
    cookie_path = '/tmp/anisub/cookies.txt' if os.path.exists('/tmp/anisub/cookies.txt') else None
    result = extract_from_episode_page(url, cookie_path)
    return jsonify(result)

@app.route('/start', methods=['POST'])
def start_task():
    data = request.json
    task_id = str(uuid.uuid4())
    tasks[task_id] = {'status': 'Queued', 'progress': 0, 'logs': [], 'tg_link': None, 'error': None, 'output_path': None, 'has_preview': False}
    thread = threading.Thread(target=process_task, args=(task_id, data), daemon=False)
    thread.start()
    return jsonify({'task_id': task_id})

@app.route('/status/<task_id>')
def get_status(task_id):
    if task_id not in tasks: return jsonify({'error': 'Task not found'}), 404
    return jsonify(tasks[task_id])

# --- Main Processing Engine ---
def process_task(task_id, data):
    task = tasks[task_id]
    def log(msg, icon="ℹ️"):
        task['logs'].append(f"[{time.strftime('%H:%M:%S')}] {icon} {msg}")

    try:
        log("Task Started", "🚀")
        video_url = data.get('video_url')
        raw_video_path = f"/tmp/anisub/{task_id}_raw.mp4"
        final_video_path = f"/tmp/anisub/{task_id}_final.mp4"
        srt_path = f"/tmp/anisub/{task_id}.srt"
        ass_path = f"/tmp/anisub/{task_id}.ass"

        # 1. DOWNLOAD (Simplified logic for speed)
        task['status'] = 'Downloading'
        log("Downloading video...", "📥")
        cmd_dl = ['yt-dlp', '-o', raw_video_path, '--no-playlist', '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]', video_url]
        subprocess.run(cmd_dl, capture_output=True)
        
        if not os.path.exists(raw_video_path):
             raise Exception("Video download failed.")
        
        task['progress'] = 25

        # 2. SUBTITLE PREPARATION
        task['status'] = 'Subtitle'
        log("Preparing Bengali Subtitles...", "📝")
        # ধরে নিচ্ছি srt_content আগে যেভাবে তৈরি করতি সেভাবেই আছে
        # (এখানে তোর আগের ট্রান্সলেশন লজিকটা বসিয়ে নিবি)
        
        # SRT কে ASS এ কনভার্ট (যাতে যুক্তবর্ণ না ভাঙে)
        # libass দিয়ে রেন্ডার করলে ফন্ট ভাঙার চান্স নেই
        subprocess.run(['ffmpeg', '-i', srt_path, ass_path, '-y'], capture_output=True)
        
        task['progress'] = 40

        # 3. FAST ENCODING & BURN-IN
        task['status'] = 'Processing'
        log("Burning Subtitles (720p Optimized)...", "🔥")
        
        duration = get_duration(raw_video_path)
        font_name = data.get('font_name', 'SolaimanLipi') # Default Bengali Font
        
        # FFmpeg Command for speed and Bengali support
        # scale=1280:-2 রেজোলিউশন কমিয়ে রেন্ডারিং ফাস্ট করবে
        sub_filter = f"scale=1280:-2,ass={ass_path}:fontsdir=/tmp/fonts/:force_style='FontName={font_name},FontSize=22'"
        
        cmd_burn = [
            'ffmpeg', '-y', '-i', raw_video_path,
            '-vf', sub_filter,
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26',
            '-threads', '0', '-c:a', 'copy', # অডিও ডিরেক্ট কপি (সময় বাঁচাবে)
            final_video_path
        ]

        proc = subprocess.Popen(cmd_burn, stderr=subprocess.PIPE, text=True)
        for line in iter(proc.stderr.readline, ''):
            if 'time=' in line and duration:
                try:
                    t = parse_time_to_sec(line.split('time=')[1].split()[0])
                    task['progress'] = 40 + int((t / duration) * 45)
                except: pass
        proc.wait()

        # 4. UPLOAD
        task['status'] = 'Uploading'
        log("Uploading to Telegram...", "☁️")
        tg_link = upload_to_telegram(final_video_path, data.get('tg_title'), data.get('tg_caption'), lambda p: None)
        task['tg_link'] = tg_link
        
        task['status'] = 'Done'
        task['progress'] = 100
        log("Success!", "✅")

    except Exception as e:
        task['status'] = 'Error'
        task['error'] = str(e)
        log(f"Error: {e}", "🔴")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
