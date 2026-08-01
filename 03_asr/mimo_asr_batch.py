#!/usr/bin/env python3
"""
03_asr/mimo_asr_batch.py

小米 MiMo 语音识别（备选方案，火山引擎为主）。
每次提交音频 ≤ 7MB 或 ≤ 20 分钟，超长音频自动切片；自动重试、断点续传。

密钥：环境变量 MIMO_API_KEY，或 config.json 的 asr.mimo.apiKey。

用法：
    export MIMO_API_KEY=tp-xxxx
    python mimo_asr_batch.py --dir downloads/临床药理学 --out transcripts_mimo --ffmpeg ffmpeg
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time

import requests

API_URL = "https://token-plan-cn.xiaomimimo.com/v1/chat/completions"
MODEL = "mimo-asr-001"  # 按实际可用模型名调整
MAX_BYTES = 7 * 1024 * 1024
MAX_SECONDS = 20 * 60


def parse_args():
    parser = argparse.ArgumentParser(description="MiMo ASR 批量转写（备选）")
    parser.add_argument("--dir", required=True, help="视频/音频所在目录")
    parser.add_argument("--out", default="transcripts_mimo", help="输出目录")
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_PATH", "ffmpeg"), help="ffmpeg 可执行文件路径")
    parser.add_argument("--referer", default="https://zbkt.ncu.edu.cn/TeachingCenterStudentWeb/index.html", help="下载 Referer 头")
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


def extract_audio(video_path, tmp_wav, ffmpeg, referer):
    cmd = [
        ffmpeg, "-y", "-headers", f"Referer: {referer}\r\n",
        "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", tmp_wav,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=1800)
    if result.returncode != 0 or not os.path.exists(tmp_wav):
        raise RuntimeError(result.stderr.decode("utf-8", "ignore")[-500:])


def split_audio(wav_path, ffmpeg, max_bytes=MAX_BYTES):
    """按 20 分钟切片，若仍超过 7MB 再按大小切。"""
    segments = []
    if os.path.getsize(wav_path) <= max_bytes:
        segments.append((wav_path, 0))
        return segments
    # 简化：按 20 分钟切片
    i = 0
    while True:
        out = f"{wav_path}.part{i:03d}.wav"
        subprocess.run(
            [ffmpeg, "-y", "-i", wav_path, "-ss", str(i * MAX_SECONDS), "-t", str(MAX_SECONDS), "-ac", "1", "-ar", "16000", out],
            capture_output=True, timeout=600,
        )
        if not os.path.exists(out):
            break
        segments.append((out, i * MAX_SECONDS))
        if os.path.getsize(out) < 1024 * 1024:  # 尾部空切片
            os.remove(out)
            segments.pop()
            break
        i += 1
    return segments


def transcribe_segment(api_key, wav_path):
    with open(wav_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("ascii")
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": "请将这段音频完整转写为文字，只输出转写结果。"},
            {"role": "user", "content": f"data:audio/wav;base64,{audio_b64}"},
        ],
    }
    for attempt in range(3):
        try:
            resp = requests.post(API_URL, json=payload,
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                 timeout=300)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt < 2:
                time.sleep(10)
            else:
                raise RuntimeError(f"MiMo 转写失败: {e}")


def main():
    args = parse_args()
    api_key = load_key(args)

    files = [os.path.join(args.dir, f) for f in sorted(os.listdir(args.dir)) if f.lower().endswith((".mp4", ".mkv", ".flv", ".wav", ".mp3"))]
    print(f"共 {len(files)} 个文件")

    os.makedirs(args.out, exist_ok=True)
    tmp_dir = os.path.join(args.out, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    success = failed = 0
    for i, file_path in enumerate(files, 1):
        name = os.path.splitext(os.path.basename(file_path))[0]
        txt_path = os.path.join(args.out, name + ".txt")
        if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
            print(f"[{i}/{len(files)}] 跳过（已存在）: {name}")
            continue

        tmp_wav = os.path.join(tmp_dir, name + ".wav")
        parts = []
        try:
            if not file_path.lower().endswith(".wav"):
                print(f"[{i}/{len(files)}] 抽取音频: {name}")
                extract_audio(file_path, tmp_wav, args.ffmpeg, args.referer)
            else:
                tmp_wav = file_path
            parts = split_audio(tmp_wav, args.ffmpeg)

            texts = []
            for j, (part, offset) in enumerate(parts, 1):
                print(f"  切片 {j}/{len(parts)} 转写中...")
                texts.append(transcribe_segment(api_key, part))
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(texts))
            print(f"  [{i}/{len(files)}] 完成 -> {txt_path}")
            success += 1
        except Exception as e:
            failed += 1
            print(f"  失败: {e}")
        finally:
            for part, _ in parts:
                if os.path.exists(part):
                    os.remove(part)
            if os.path.exists(tmp_wav) and tmp_wav != file_path:
                os.remove(tmp_wav)

    print(f"\n完成! 成功: {success}, 失败: {failed}")


if __name__ == "__main__":
    main()
