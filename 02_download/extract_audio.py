#!/usr/bin/env python3
"""
02_download/extract_audio.py

【主脚本】从视频直链抽离音频（不下载视频，省本地空间）。

输入 media_manifests/<课程>/media-manifest.json（阶段 1 产物），
用 ffmpeg 直接读取直链（selected.url，带 Referer 头），
抽取 16kHz 单声道音频，按课程存到输出目录。

用法：
    python extract_audio.py --config config.json --courses=全部
    python extract_audio.py --config config.json --courses=药物分析 --limit=3 --overwrite
"""
import argparse
import json
import os
import subprocess
import sys


def load_config(config_path):
    if not config_path:
        config_path = os.environ.get("PIPELINE_CONFIG", "config.json")
    if not os.path.exists(config_path):
        sys.exit(f"找不到配置文件: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_args():
    parser = argparse.ArgumentParser(description="从视频直链抽离音频（不下载视频）")
    parser.add_argument("--config", default=None, help="config.json 路径")
    parser.add_argument("--courses", default=None, help="逗号分隔课程名，全部=所有课程")
    parser.add_argument("--limit", type=int, default=0, help="每门课最多处理 N 节（0=全部）")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的音频")
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_PATH", "ffmpeg"), help="ffmpeg 可执行文件路径")
    parser.add_argument("--referer", default=None, help="下载 Referer 头（默认取 config.platform.referer）")
    parser.add_argument("--out", default="transcripts/audio", help="音频输出目录")
    parser.add_argument("--output-format", choices=["wav", "mp3"], default="wav", help="音频格式，mp3 为 64k 单声道")
    return parser.parse_args()


def main():
    args = parse_args()
    cfg = load_config(args.config)

    courses = args.courses
    if not courses or courses == "全部":
        courses = cfg.get("courses", [])
    else:
        courses = [item.strip() for item in courses.split(",") if item.strip()]
    if not courses:
        sys.exit("没有课程可处理")

    referer = args.referer or cfg.get("platform", {}).get("referer", "")
    manifests_root = cfg.get("paths", {}).get("manifestsDir", "media_manifests")

    summary = []
    for course in courses:
        manifest_path = os.path.join(manifests_root, course, "media-manifest.json")
        if not os.path.exists(manifest_path):
            print(f"[{course}] 缺少媒体清单: {manifest_path}")
            summary.append({"course": course, "done": 0, "failed": 1, "skipped": 0})
            continue

        with open(manifest_path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        out_dir = os.path.join(args.out, course)
        os.makedirs(out_dir, exist_ok=True)

        done = failed = skipped = 0
        for index, row in enumerate(rows, 1):
            if args.limit and done >= args.limit:
                break
            url = row.get("selected", {}).get("url", "")
            if row.get("status") != "ok" or not url:
                skipped += 1
                continue

            base = row.get("base") or f"lesson_{index:02d}"
            ext = args.output_format
            audio_file = os.path.join(out_dir, base + "." + ext)
            if os.path.exists(audio_file) and os.path.getsize(audio_file) > 1024 and not args.overwrite:
                skipped += 1
                continue

            cmd = [
                args.ffmpeg, "-y",
                "-headers", f"Referer: {referer}\r\n",
                "-i", url,
                "-vn", "-ac", "1", "-ar", "16000",
            ]
            if ext == "mp3":
                cmd += ["-b:a", "64k", audio_file]
            else:
                cmd += ["-f", "wav", audio_file]

            try:
                print(f"[{course}] [{index}/{len(rows)}] 抽音频: {base}.{ext}")
                result = subprocess.run(cmd, capture_output=True, timeout=3600)
                if result.returncode != 0 or not os.path.exists(audio_file):
                    raise RuntimeError(result.stderr.decode("utf-8", "ignore")[-500:])
                done += 1
            except Exception as exc:
                failed += 1
                log_path = os.path.join(args.out, "extract-audio-errors.log")
                with open(log_path, "a", encoding="utf-8") as handle:
                    handle.write(f"{course}\t{base}\t{type(exc).__name__}: {exc}\n")
                print(f"[{course}] 失败: {base}: {exc}")

        summary.append({"course": course, "done": done, "failed": failed, "skipped": skipped})

    for row in summary:
        print(f"{row['course']}: 抽取 {row['done']}, 失败 {row['failed']}, 跳过 {row['skipped']}")
    print(f"输出目录: {args.out}")


if __name__ == "__main__":
    main()
