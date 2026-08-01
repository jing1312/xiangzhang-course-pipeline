#!/usr/bin/env python3
"""
02_download/extract_audio.py

把视频批量抽取为 16kHz 单声道 WAV 音频（供 ASR 使用），
通过 Referer 头携带来源页面地址（视频直链需携带 Referer 才可下载）。

用法：
    python extract_audio.py --dir "D:\\文档\\课程视频\\临床药理学" \
        --out transcripts/audio --ffmpeg ffmpeg \
        --referer "https://zbkt.ncu.edu.cn/TeachingCenterStudentWeb/index.html"
    # 只处理部分文件：
    python extract_audio.py --dir "D:\\文档\\课程视频" --filter "临床药理学"
"""
import argparse
import os
import subprocess
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="批量抽取视频音频为 WAV")
    parser.add_argument("--dir", required=True, help="视频所在目录（含子目录）")
    parser.add_argument("--out", default="transcripts/audio", help="WAV 输出目录")
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_PATH", "ffmpeg"), help="ffmpeg 可执行文件路径")
    parser.add_argument("--referer", default="https://zbkt.ncu.edu.cn/TeachingCenterStudentWeb/index.html", help="下载 Referer 头")
    parser.add_argument("--filter", default=None, help="只处理文件名包含该关键词的视频（如课程名）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 个文件（0=全部）")
    parser.add_argument("--overwrite", action="store_true", help="已存在的 WAV 也重新抽取")
    parser.add_argument("--output-format", choices=["wav", "mp3"], default="wav", help="音频格式，mp3 为 64k 单声道")
    return parser.parse_args()


def main():
    args = parse_args()
    if not os.path.exists(args.dir):
        sys.exit(f"目录不存在: {args.dir}")

    videos = []
    for root, _, files in os.walk(args.dir):
        for f in files:
            if f.lower().endswith((".mp4", ".mkv", ".flv")):
                path = os.path.join(root, f)
                if args.filter and args.filter not in f:
                    continue
                videos.append(path)
    videos.sort()
    if args.limit > 0:
        videos = videos[: args.limit]

    print(f"共 {len(videos)} 个视频待处理")
    os.makedirs(args.out, exist_ok=True)

    success = skipped = failed = 0
    for i, video in enumerate(videos, 1):
        name = os.path.splitext(os.path.basename(video))[0]
        ext = args.output_format
        out_path = os.path.join(args.out, name + "." + ext)

        if os.path.exists(out_path) and not args.overwrite:
            skipped += 1
            print(f"[{i}/{len(videos)}] 跳过（已存在）: {name}.{ext}")
            continue

        cmd = [
            args.ffmpeg, "-y", "-headers", f"Referer: {args.referer}\r\n",
            "-i", video,
            "-vn", "-ac", "1", "-ar", "16000",
        ]
        if ext == "mp3":
            cmd += ["-b:a", "64k", out_path]
        else:
            cmd += [out_path]

        print(f"[{i}/{len(videos)}] 抽取中: {name} -> {name}.{ext}")
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=1800)
            if result.returncode != 0 or not os.path.exists(out_path):
                raise RuntimeError(result.stderr.decode("utf-8", "ignore")[-500:])
            success += 1
        except Exception as e:
            failed += 1
            print(f"  失败: {e}")

    print(f"\n完成! 成功: {success}, 跳过: {skipped}, 失败: {failed}")


if __name__ == "__main__":
    main()
