#!/usr/bin/env python3
"""
03_asr/batch_transcribe.py

火山引擎「大模型录音文件识别」（豆包录音文件识别模型2.0）批量转写。

- 认证走 Header：X-Api-App-Key / X-Api-Access-Key / X-Api-Resource-Id: volc.seedasr.auc
- 音频以 base64 内嵌提交（单文件 ≤512MB，无时长限制），也可直接传视频 URL
- 任务状态码在响应 Header X-Api-Status-Code：20000000=成功，20000001/20000002=处理中

密钥从环境变量读取（VOLC_APP_ID / VOLC_ACCESS_TOKEN），不落盘。

用法：
    export VOLC_APP_ID=<你的火山appId>
    export VOLC_ACCESS_TOKEN=<你的accessToken>

    # 方式 A：直接吃阶段1的直链 CSV（原版流程，ffmpeg 现场抽音频）
    python batch_transcribe.py --csv media_urls/all_fresh_media_urls.csv --out transcripts

    # 方式 B：吃阶段2抽好的本地音频（wav/mp3 目录）
    python batch_transcribe.py --dir transcripts/audio/临床药理学 --out transcripts
"""
import argparse
import base64
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime

import requests

SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
RESOURCE_ID = "volc.seedasr.auc"
OK = "20000000"
RUNNING = {"20000001", "20000002"}


def parse_args():
    parser = argparse.ArgumentParser(description="火山引擎大模型录音文件识别批量转写")
    parser.add_argument("--csv", default=None, help="直链 CSV（media_urls/all_fresh_media_urls.csv），与 --dir 二选一")
    parser.add_argument("--dir", default=None, help="本地音频/视频目录，与 --csv 二选一")
    parser.add_argument("--out", default="transcripts", help="转写文本输出目录")
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_PATH", "ffmpeg"), help="ffmpeg 可执行文件路径")
    parser.add_argument("--referer", default="https://zbkt.ncu.edu.cn/TeachingCenterStudentWeb/index.html", help="从直链抽音频时的 Referer")
    parser.add_argument("--filter", default=None, help="只处理文件名含该关键词的")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 节（0=全部）")
    parser.add_argument("--skip-existing", action="store_true", help="已有非空 txt 则跳过（断点续传）")
    parser.add_argument("--config", default=None, help="config.json 路径（读取 asr.volc / asr.ffmpegPath）")
    return parser.parse_args()


def load_secrets(args):
    app_id = os.environ.get("VOLC_APP_ID")
    access_token = os.environ.get("VOLC_ACCESS_TOKEN")
    ffmpeg = args.ffmpeg
    referer = args.referer
    if args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        volc = cfg.get("asr", {}).get("volc", {})
        app_id = app_id or volc.get("appId")
        access_token = access_token or volc.get("accessToken")
        ffmpeg = ffmpeg or cfg.get("asr", {}).get("ffmpegPath")
        referer = cfg.get("platform", {}).get("referer", referer)
    if not app_id or not access_token:
        sys.exit("缺少火山引擎密钥，请设置环境变量 VOLC_APP_ID / VOLC_ACCESS_TOKEN")
    return app_id, access_token, ffmpeg, referer


def extract_audio_base64(ffmpeg, media_path, referer, max_retries=3):
    """从视频 URL 或本地文件抽 16kHz 单声道 64k mp3，返回 (base64, 字节数)。"""
    for attempt in range(max_retries):
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            cmd = [
                ffmpeg, "-y", "-loglevel", "error",
                "-headers", f"Referer: {referer}\r\n",
                "-i", media_path,
                "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k",
                tmp_path,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=1200)
            if result.returncode != 0 or not os.path.exists(tmp_path):
                if attempt < max_retries - 1:
                    time.sleep(10)
                    continue
                return None, 0
            with open(tmp_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8"), os.path.getsize(tmp_path)
        except subprocess.TimeoutExpired:
            if attempt < max_retries - 1:
                time.sleep(10)
                continue
            return None, 0
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    return None, 0


def submit_task(app_id, access_token, audio_base64):
    task_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Api-App-Key": app_id,
        "X-Api-Access-Key": access_token,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Request-Id": task_id,
        "X-Api-Sequence": "-1",
    }
    data = {
        "user": {"uid": "course"},
        "audio": {"format": "mp3", "url": "", "data": audio_base64},
        "request": {"model_name": "bigmodel", "enable_itn": True, "enable_punc": True, "show_utterances": True},
    }
    r = requests.post(SUBMIT_URL, headers=headers, json=data, timeout=120)
    return {
        "task_id": task_id,
        "status_code": r.headers.get("X-Api-Status-Code"),
        "message": r.headers.get("X-Api-Message"),
    }


def query_task(app_id, access_token, task_id):
    headers = {
        "Content-Type": "application/json",
        "X-Api-App-Key": app_id,
        "X-Api-Access-Key": access_token,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Request-Id": task_id,
    }
    r = requests.post(QUERY_URL, headers=headers, json={}, timeout=30)
    result = None
    if r.text:
        try:
            result = r.json()
        except Exception:
            pass
    return {"status_code": r.headers.get("X-Api-Status-Code"), "result": result}


def wait_for_result(app_id, access_token, task_id, max_wait=600):
    for _ in range(max_wait // 5):
        time.sleep(5)
        qr = query_task(app_id, access_token, task_id)
        sc = qr["status_code"]
        if sc == OK:
            return qr
        if sc not in RUNNING:
            return None
    return None


def load_from_csv(csv_path, course_filter, limit):
    items = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        keys = list(reader.fieldnames)
        for row in reader:
            if course_filter and row[keys[0]] != course_filter:
                continue
            items.append({
                "course": row[keys[0]],
                "filename": row[keys[1]],
                "duration": row[keys[6]],
                "media": row[keys[-1]],
            })
    if limit > 0:
        items = items[:limit]
    return items


def load_from_dir(dir_path, file_filter, limit):
    items = []
    for f in sorted(os.listdir(dir_path)):
        if not f.lower().endswith((".wav", ".mp3", ".mp4", ".mkv", ".flv")):
            continue
        if file_filter and file_filter not in f:
            continue
        items.append({"course": os.path.basename(dir_path), "filename": os.path.splitext(f)[0], "duration": "", "media": os.path.join(dir_path, f)})
    if limit > 0:
        items = items[:limit]
    return items


def process_one(app_id, access_token, ffmpeg, referer, item, out_dir, args, index, total):
    filename = item["filename"]
    output_path = os.path.join(out_dir, f"{filename}.txt")
    if args.skip_existing and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
        print(f"[{index}/{total}] 已存在，跳过: {filename[:30]}")
        return True

    duration = item.get("duration") or ""
    print(f"[{index}/{total}] {filename[:30]}... ({int(duration) // 60 if str(duration).isdigit() else '?'}分钟)")

    audio_base64, audio_size = extract_audio_base64(ffmpeg, item["media"], referer)
    if not audio_base64:
        print("  音频提取失败")
        return False
    print(f"  音频: {audio_size / 1024 / 1024:.1f}MB")

    result = submit_task(app_id, access_token, audio_base64)
    if result["status_code"] != OK:
        print(f"  提交失败: {result['message']}")
        return False

    task_id = result["task_id"]
    print(f"  任务: {task_id[:8]}...")

    qr = wait_for_result(app_id, access_token, task_id)
    if not qr:
        print("  转写失败/超时")
        return False

    rd = qr.get("result", {}).get("result", {})
    text = rd.get("text", "")
    utterances = rd.get("utterances", [])
    if not text:
        print("  结果为空")
        return False

    os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# {filename}\n# 课程: {item['course']}\n# 转写时间: {datetime.now()}\n\n{text}")
        if utterances:
            f.write("\n\n--- 分句详情 ---\n")
            for u in utterances:
                f.write(f"[{u.get('start_time', 0) / 1000:.1f}s - {u.get('end_time', 0) / 1000:.1f}s] {u.get('text', '')}\n")
    print(f"  完成: {len(text)}字")
    return True


def main():
    args = parse_args()
    if not args.csv and not args.dir:
        sys.exit("需要 --csv 或 --dir")
    app_id, access_token, ffmpeg, referer = load_secrets(args)

    if args.csv:
        items = load_from_csv(args.csv, args.filter, args.limit)
    else:
        items = load_from_dir(args.dir, args.filter, args.limit)
    print(f"共 {len(items)} 节待转写")

    courses = {}
    for item in items:
        courses.setdefault(item["course"], []).append(item)
    print(f"课程: {', '.join(courses.keys()) or '-'}")

    success = failed = 0
    for index, item in enumerate(items, 1):
        out_dir = os.path.join(args.out, item["course"])
        if process_one(app_id, access_token, ffmpeg, referer, item, out_dir, args, index, len(items)):
            success += 1
        else:
            failed += 1
        time.sleep(2)

    print(f"\n完成! 成功: {success}, 失败: {failed}")
    print(f"输出目录: {args.out}")


if __name__ == "__main__":
    main()
