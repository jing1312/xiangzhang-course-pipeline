#!/usr/bin/env python3
"""
02_download/rename_videos.py 【第一遍重命名】

下载下来的视频文件名是平台文件 ID（如 ff8081819c219f26019caceb19b1735f-1.mp4），
本脚本从直链 CSV 的「视频直链」列提取文件 ID，映射回「课程名_上课时间.mp4」。

用法：
    python rename_videos.py --dir ./downloads --csv media_urls/all_fresh_media_urls.csv --dry-run
"""
import argparse
import csv
import os
import re
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="第一遍重命名：文件ID -> 课程名_上课时间")
    parser.add_argument("--dir", required=True, help="视频所在目录")
    parser.add_argument("--csv", default="media_urls/all_fresh_media_urls.csv", help="直链 CSV（含 视频直链 列）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不改名")
    return parser.parse_args()


def main():
    args = parse_args()
    if not os.path.exists(args.dir):
        sys.exit(f"目录不存在: {args.dir}")
    if not os.path.exists(args.csv):
        sys.exit(f"CSV 不存在: {args.csv}")

    # URL 格式: .../ff8081819c219f26019caceb19b1735f-1.mp4
    url_to_name = {}
    with open(args.csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        keys = list(reader.fieldnames)
        for item in reader:
            match = re.search(r"([a-f0-9]{32}-\d+\.mp4)", item[keys[-1]])
            if not match:
                continue
            file_id = match.group(1)
            time_str = item[keys[2]].replace(":", " ").replace("-", " ")
            url_to_name[file_id] = f"{item[keys[0]]}_{time_str}.mp4"
    print(f"CSV 中有 {len(url_to_name)} 个视频映射")

    files = os.listdir(args.dir)
    print(f"下载目录有 {len(files)} 个文件")

    success = failed = skipped = 0
    for filename in files:
        if not filename.endswith(".mp4"):
            continue
        if not filename.startswith("ff80"):
            skipped += 1
            continue
        new_name = url_to_name.get(filename)
        if not new_name:
            print(f"  未找到映射: {filename}")
            failed += 1
            continue
        old_path = os.path.join(args.dir, filename)
        new_path = os.path.join(args.dir, new_name)
        if args.dry_run:
            print(f"  [PREVIEW] {filename} -> {new_name}")
            continue
        try:
            os.rename(old_path, new_path)
            print(f"  [OK] {filename} -> {new_name}")
            success += 1
        except Exception as e:
            print(f"  [FAIL] {filename}: {e}")
            failed += 1

    print(f"\n完成! 成功: {success}, 跳过: {skipped}, 失败: {failed}")


if __name__ == "__main__":
    main()
