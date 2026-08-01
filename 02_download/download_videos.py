#!/usr/bin/env python3
"""
02_download/download_videos.py

按 CSV（来自 01_links 的 all_fresh_media_urls.csv）并行下载课程视频，
按「课程」字段分文件夹保存，支持断点续传（已存在且 > 最小大小则跳过）。

用法：
    python download_videos.py --csv media_urls/all_fresh_media_urls.csv \
        --out downloads --workers 3 --min-size 1048576
"""
import argparse
import csv
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def parse_args():
    parser = argparse.ArgumentParser(description="批量下载课程视频")
    parser.add_argument("--csv", default="media_urls/all_fresh_media_urls.csv", help="视频清单 CSV（含 课程/文件名/视频直链 列）")
    parser.add_argument("--out", default="downloads", help="下载输出目录")
    parser.add_argument("--workers", type=int, default=3, help="并行下载数")
    parser.add_argument("--min-size", type=int, default=1024 * 1024, help="判定下载成功的最小字节数")
    parser.add_argument("--course", default=None, help="只下载指定课程（默认全部）")
    return parser.parse_args()


def download_video(url, output_path, min_size, max_retries=3):
    if os.path.exists(output_path):
        if os.path.getsize(output_path) > min_size:
            return True, "已存在"

    for attempt in range(max_retries):
        try:
            cmd = [
                "powershell", "-Command",
                f"Invoke-WebRequest -Uri '{url}' -OutFile '{output_path}' -TimeoutSec 300",
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=600)
            if result.returncode == 0 and os.path.exists(output_path):
                size = os.path.getsize(output_path)
                if size > min_size:
                    return True, f"{size / 1024 / 1024:.1f}MB"
            if attempt < max_retries - 1:
                time.sleep(5)
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(5)
    return False, "下载失败"


def main():
    args = parse_args()
    with open(args.csv, "r", encoding="utf-8-sig") as f:
        items = list(csv.DictReader(f))
    if args.course:
        items = [item for item in items if item["课程"] == args.course]

    print(f"共 {len(items)} 个视频")
    courses = {}
    for item in items:
        courses.setdefault(item["课程"], []).append(item)
    print(f"课程: {len(courses)} 门")
    for course, rows in courses.items():
        print(f"  - {course}: {len(rows)} 节")

    os.makedirs(args.out, exist_ok=True)
    for course in courses:
        os.makedirs(os.path.join(args.out, course), exist_ok=True)

    tasks = []
    for item in items:
        filename = item["文件名"] + ".mp4"
        url = item["视频直链"] or item.get("视频URL", "")
        if not url:
            continue
        tasks.append((url, os.path.join(args.out, item["课程"], filename), filename))

    print(f"\n开始下载 (并行数: {args.workers})...")
    success = failed = skipped = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(download_video, url, output_path, args.min_size): name for url, output_path, name in tasks}
        for i, future in enumerate(as_completed(futures), 1):
            filename = futures[future]
            ok, msg = future.result()
            if ok:
                if msg == "已存在":
                    skipped += 1
                else:
                    success += 1
                    print(f"[{i}/{len(tasks)}] OK {filename}: {msg}")
            else:
                failed += 1
                print(f"[{i}/{len(tasks)}] FAIL {filename}: {msg}")

    print(f"\n下载完成! 成功: {success}, 跳过: {skipped}, 失败: {failed}")
    print(f"保存位置: {args.out}")


if __name__ == "__main__":
    main()
