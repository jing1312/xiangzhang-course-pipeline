#!/usr/bin/env python3
"""
03_asr/mimo_asr_batch.py

小米 MiMo ASR 批量转写（备选方案，火山引擎为主）。

- 模型：mimo-v2.5-asr（OpenAI 兼容接口 /v1/chat/completions）
- 认证：Header `api-key: <MIMO_API_KEY>`
- 音频：16kHz 单声道 mp3，base64 以 input_audio 数组形式传
- 限制：单次提交 ≤7MB 或 ≤20 分钟，超长自动切片（ffmpeg segment）
- 附带静音检测（音量 < 阈值判定为静音课，跳过）

密钥：环境变量 MIMO_API_KEY，或 config.json 的 asr.mimo.apiKey。

用法：
    export MIMO_API_KEY=<你的key>
    python mimo_asr_batch.py --csv media_urls/all_fresh_media_urls.csv --out transcripts_mimo
    # 或吃本地音频：
    python mimo_asr_batch.py --dir transcripts/audio/临床药理学 --out transcripts_mimo
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

import requests

API_URL = "https://token-plan-cn.xiaomimimo.com/v1/chat/completions"
MODEL = "mimo-v2.5-asr"
MAX_MP3_SIZE = 7 * 1024 * 1024  # 7MB
SEGMENT_TIME = 1200  # 20 分钟切片


def parse_args():
    parser = argparse.ArgumentParser(description="MiMo ASR 批量转写（备选）")
    parser.add_argument("--csv", default=None, help="直链 CSV，与 --dir 二选一")
    parser.add_argument("--dir", default=None, help="本地音频/视频目录，与 --csv 二选一")
    parser.add_argument("--out", default="transcripts_mimo", help="输出目录")
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_PATH", "ffmpeg"), help="ffmpeg 可执行文件路径")
    parser.add_argument("--referer", default="https://zbkt.ncu.edu.cn/TeachingCenterStudentWeb/index.html", help="从直链抽音频时的 Referer")
    parser.add_argument("--filter", default=None, help="只处理文件名含该关键词的")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 节（0=全部）")
    parser.add_argument("--config", default=None, help="config.json 路径")
    return parser.parse_args()


def load_key(args):
    api_key = os.environ.get("MIMO_API_KEY")
    if not api_key and args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            api_key = json.load(f).get("asr", {}).get("mimo", {}).get("apiKey")
    if not api_key:
        sys.exit("缺少 MIMO_API_KEY 环境变量")
    return api_key


def extract_audio_small(ffmpeg, media_path, referer, output_path, max_retries=3):
    """抽 16kHz 单声道 16kbps mp3（尽量小，满足 7MB 限制）。"""
    for attempt in range(max_retries):
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-headers", f"Referer: {referer}\r\n",
            "-i", media_path,
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "16k",
            output_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=1200)
            if result.returncode == 0 and os.path.exists(output_path):
                return True
            if attempt < max_retries - 1:
                time.sleep(10)
        except subprocess.TimeoutExpired:
            if attempt < max_retries - 1:
                print(f"    超时，重试 {attempt + 2}/{max_retries}...")
                time.sleep(10)
    return False


def split_audio(ffmpeg, media_path, output_dir, segment_time=SEGMENT_TIME):
    """ffmpeg segment 切片，返回切片文件路径列表。"""
    pattern = os.path.join(output_dir, "part_%03d.mp3")
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", media_path,
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "16k",
        "-f", "segment", "-segment_time", str(segment_time),
        pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=1200)
    if result.returncode != 0:
        return []
    return sorted(
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.startswith("part_") and f.endswith(".mp3")
    )


def is_silent_audio(ffmpeg, audio_path, threshold=100):
    """粗略静音检测：转 wav 求最大采样值。"""
    import wave
    import numpy as np
    try:
        wav_path = audio_path.replace(".mp3", ".wav")
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", audio_path, "-acodec", "pcm_s16le", wav_path]
        subprocess.run(cmd, capture_output=True, timeout=30)
        if not os.path.exists(wav_path):
            return False
        with wave.open(wav_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            data = np.frombuffer(frames, dtype=np.int16)
            max_val = np.max(np.abs(data))
        os.unlink(wav_path)
        return max_val < threshold
    except Exception:
        return False


def call_mimo_asr(api_key, audio_base64):
    headers = {"Content-Type": "application/json", "api-key": api_key}
    data = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": f"data:audio/mpeg;base64,{audio_base64}"}}
                ],
            }
        ],
        "asr_options": {"language": "zh"},
    }
    try:
        response = requests.post(API_URL, headers=headers, json=data, timeout=300)
        result = response.json()
        if "choices" in result and len(result["choices"]) > 0:
            msg = result["choices"][0].get("message", {})
            content = msg.get("content", "")
            reasoning = msg.get("reasoning_content", "")
            return content or reasoning
        return None
    except Exception as e:
        print(f"    API错误: {e}")
        return None


def process_one(api_key, ffmpeg, referer, item, out_dir, index, total):
    filename = item["filename"]
    output_path = os.path.join(out_dir, f"{filename}.txt")
    if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
        print(f"[{index}/{total}] 已存在，跳过: {filename[:30]}")
        return "skipped_exists"

    with tempfile.TemporaryDirectory() as tmp_dir:
        mp3_path = os.path.join(tmp_dir, "full.mp3")
        print(f"[{index}/{total}] {filename[:30]}... 提取音频")
        if not extract_audio_small(ffmpeg, item["media"], referer, mp3_path):
            print("  音频提取失败")
            return "failed_extract"
        mp3_size = os.path.getsize(mp3_path)
        print(f"  音频: {mp3_size / 1024 / 1024:.1f}MB")

        if is_silent_audio(ffmpeg, mp3_path):
            print("  静音课，跳过")
            return "skipped_silent"

        os.makedirs(out_dir, exist_ok=True)

        if mp3_size <= MAX_MP3_SIZE:
            with open(mp3_path, "rb") as f:
                audio_base64 = base64.b64encode(f.read()).decode("utf-8")
            transcript = call_mimo_asr(api_key, audio_base64)
            if not transcript:
                print("  ASR 返回空")
                return "failed_asr"
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(transcript)
            print(f"  完成: {len(transcript)}字")
            return "ok"

        parts = split_audio(ffmpeg, item["media"], tmp_dir, SEGMENT_TIME)
        if not parts:
            print("  切片失败")
            return "failed_split"
        print(f"  切片处理（每{SEGMENT_TIME // 60}分钟），共{len(parts)}个切片")

        all_texts = []
        for j, part_path in enumerate(parts, 1):
            print(f"    切片{j}/{len(parts)}...")
            with open(part_path, "rb") as f:
                audio_base64 = base64.b64encode(f.read()).decode("utf-8")
            text = call_mimo_asr(api_key, audio_base64)
            if text:
                all_texts.append(text)
                print(f"      完成: {len(text)}字")
            else:
                print("      失败")
            time.sleep(2)

        if not all_texts:
            print("  所有切片 ASR 失败")
            return "failed_asr"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(all_texts))
        print(f"  完成: {sum(len(t) for t in all_texts)}字")
        return "ok"


def main():
    args = parse_args()
    if not args.csv and not args.dir:
        sys.exit("需要 --csv 或 --dir")
    api_key = load_key(args)

    if args.csv:
        items = []
        with open(args.csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            keys = list(reader.fieldnames)
            for row in reader:
                if args.filter and row[keys[0]] != args.filter:
                    continue
                items.append({"course": row[keys[0]], "filename": row[keys[1]], "media": row[keys[-1]]})
    else:
        items = []
        for f in sorted(os.listdir(args.dir)):
            if not f.lower().endswith((".wav", ".mp3", ".mp4", ".mkv", ".flv")):
                continue
            if args.filter and args.filter not in f:
                continue
            items.append({"course": os.path.basename(args.dir), "filename": os.path.splitext(f)[0], "media": os.path.join(args.dir, f)})
    if args.limit > 0:
        items = items[:args.limit]
    print(f"共 {len(items)} 节待转写")

    counters = {}
    for index, item in enumerate(items, 1):
        out_dir = os.path.join(args.out, item["course"])
        os.makedirs(out_dir, exist_ok=True)
        status = process_one(api_key, args.ffmpeg, args.referer, item, out_dir, index, len(items))
        counters[status] = counters.get(status, 0) + 1

    print(f"\n完成! {dict(counters)}")
    print(f"输出目录: {args.out}")


if __name__ == "__main__":
    main()
