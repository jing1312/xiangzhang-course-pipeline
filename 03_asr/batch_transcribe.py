#!/usr/bin/env python3
"""
03_asr/batch_transcribe.py

火山引擎（豆包）大模型语音识别（AUC BigModel ASR）批量转写。

流程：视频/音频 -> ffmpeg 抽取 16kHz 单声道 64kbps mp3 -> base64 上传
      -> /submit 提交 -> /query 轮询 -> 结果保存为 txt。

密钥从环境变量读取（VOCL_APP_ID / VOLC_ACCESS_TOKEN），
也可在 config.json 的 asr.volc 字段中提供（不推荐提交进仓库）。

用法：
    export VOLC_APP_ID=<你的火山引擎appId>
    export VOLC_ACCESS_TOKEN=<你的访问token>
    python batch_transcribe.py --dir downloads/临床药理学 --out transcripts \
        --ffmpeg ffmpeg --referer "https://zbkt.ncu.edu.cn/TeachingCenterStudentWeb/index.html"
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time

import requests

SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
SAMPLE_RATE = 16000
MAX_AUDIO_SECONDS = 600  # 单次提交最长 10 分钟，更长的视频先切片（外部切好或按节提交）


def parse_args():
    parser = argparse.ArgumentParser(description="火山引擎豆包 ASR 批量转写")
    parser.add_argument("--dir", required=True, help="视频/音频所在目录")
    parser.add_argument("--out", default="transcripts", help="转写文本输出目录")
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_PATH", "ffmpeg"), help="ffmpeg 可执行文件路径")
    parser.add_argument("--referer", default="https://zbkt.ncu.edu.cn/TeachingCenterStudentWeb/index.html", help="下载 Referer 头")
    parser.add_argument("--filter", default=None, help="只处理文件名包含该关键词的文件")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 个文件（0=全部）")
    parser.add_argument("--skip-existing", action="store_true", help="已存在 txt 则跳过")
    parser.add_argument("--config", default=None, help="config.json 路径（读取 asr.volc / asr.ffmpegPath）")
    return parser.parse_args()


def load_secrets(args):
    app_id = os.environ.get("VOLC_APP_ID")
    access_token = os.environ.get("VOLC_ACCESS_TOKEN")
    ffmpeg = args.ffmpeg
    if args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        volc = cfg.get("asr", {}).get("volc", {})
        app_id = app_id or volc.get("appId")
        access_token = access_token or volc.get("accessToken")
        ffmpeg = ffmpeg or cfg.get("asr", {}).get("ffmpegPath")
    if not app_id or not access_token:
        sys.exit("缺少火山引擎密钥，请设置环境变量 VOLC_APP_ID / VOLC_ACCESS_TOKEN")
    return app_id, access_token, ffmpeg


def extract_audio(video_path, tmp_mp3, ffmpeg, referer):
    cmd = [
        ffmpeg, "-y", "-headers", f"Referer: {referer}\r\n",
        "-i", video_path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-b:a", "64k", tmp_mp3,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=1800)
    if result.returncode != 0 or not os.path.exists(tmp_mp3):
        raise RuntimeError(result.stderr.decode("utf-8", "ignore")[-500:])


def submit_audio(app_id, access_token, audio_path):
    with open(audio_path, "rb") as f:
        audio_data = base64.b64encode(f.read()).decode("ascii")
    payload = {
        "app": {"appid": app_id, "token": access_token, "cluster": "volcengine_streaming_common"},
        "user": {"uid": "batch_transcribe"},
        "request": {
            "reqid": f"req_{int(time.time() * 1000)}",
            "workflow": "audio_in,resample,partition,vad,fe,decode,itn,nlu_punctuate",
            "res_type": "result",
            "audio": {"format": "mp3", "sample_rate": SAMPLE_RATE, "bits": 16, "channel": 1},
            "model": {"app_name": "bigmodel_tts_v2"},
        },
        "audio_data": audio_data,
    }
    resp = requests.post(SUBMIT_URL, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"submit 失败: {data.get('code')} {data.get('message')}")
    return data["data"]["id"]


def query_result(app_id, access_token, submit_id, timeout=1800):
    payload = {
        "app": {"appid": app_id, "token": access_token, "cluster": "volcengine_streaming_common"},
        "request": {"reqid": f"req_{int(time.time() * 1000)}", "id": submit_id},
    }
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.post(QUERY_URL, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("data", {}).get("status")
        if status == 2:
            result = data["data"].get("result") or data.get("result") or ""
            if isinstance(result, str):
                return result
            if isinstance(result, dict):
                return result.get("text", "")
            return json.dumps(result, ensure_ascii=False)
        if status == 3:
            raise RuntimeError(f"任务失败: {data.get('message')}")
        time.sleep(3)
    raise TimeoutError("查询超时")


def main():
    args = parse_args()
    app_id, access_token, ffmpeg = load_secrets(args)

    files = [
        os.path.join(args.dir, f)
        for f in sorted(os.listdir(args.dir))
        if f.lower().endswith((".mp4", ".mkv", ".flv", ".wav", ".mp3"))
        and (args.filter is None or args.filter in f)
    ]
    if args.limit > 0:
        files = files[: args.limit]
    print(f"共 {len(files)} 个文件待转写")

    os.makedirs(args.out, exist_ok=True)
    tmp_dir = os.path.join(args.out, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    success = failed = 0
    for i, file_path in enumerate(files, 1):
        name = os.path.splitext(os.path.basename(file_path))[0]
        txt_path = os.path.join(args.out, name + ".txt")
        if args.skip_existing and os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
            print(f"[{i}/{len(files)}] 跳过（已存在）: {name}")
            continue

        tmp_mp3 = os.path.join(tmp_dir, name + ".mp3")
        try:
            if not file_path.lower().endswith((".mp3", ".wav")):
                print(f"[{i}/{len(files)}] 抽取音频: {name}")
                extract_audio(file_path, tmp_mp3, ffmpeg, args.referer)
                audio_path = tmp_mp3
            else:
                audio_path = file_path

            print(f"[{i}/{len(files)}] 提交转写: {name}")
            submit_id = submit_audio(app_id, access_token, audio_path)
            text = query_result(app_id, access_token, submit_id)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"  完成，{len(text)} 字 -> {txt_path}")
            success += 1
        except Exception as e:
            failed += 1
            print(f"  失败: {e}")
        finally:
            if os.path.exists(tmp_mp3):
                os.remove(tmp_mp3)

    print(f"\n完成! 成功: {success}, 失败: {failed}")
    print(f"输出目录: {args.out}")


if __name__ == "__main__":
    main()
