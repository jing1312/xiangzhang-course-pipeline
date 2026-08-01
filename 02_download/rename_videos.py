#!/usr/bin/env python3
"""
02_download/rename_videos.py

把下载目录里的视频重命名为「简称_序号_课程名_时间.mp4」统一格式，
序号按课程内上课时间排序。

原文件名格式支持：
    课程名_2026 03 02 13 48 00.mp4
    简称_01_课程名_2026 03 02 13 48 00.mp4

用法：
    python rename_videos.py --dir "D:\\文档\\课程视频" \
        --short-names '{"临床药理学":"临床药理","生物药剂与药物动力学":"生物药剂","天然药物化学":"天然药化"}'
"""
import argparse
import json
import os
import re
from collections import defaultdict


def parse_args():
    parser = argparse.ArgumentParser(description="批量重命名课程视频")
    parser.add_argument("--dir", required=True, help="视频所在目录")
    parser.add_argument("--short-names", default="{}", help="课程名->简称映射 JSON，如 {'临床药理学':'临床药理'}")
    parser.add_argument("--dry-run", action="store_true", help="只打印不改名")
    return parser.parse_args()


def main():
    args = parse_args()
    short_names = json.loads(args.short_names)
    files = [f for f in os.listdir(args.dir) if f.lower().endswith(".mp4")]
    print(f"共 {len(files)} 个文件")

    courses = defaultdict(list)
    for f in files:
        m = re.match(r"^.+?_\d+_(.+?)_\d{4}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\.mp4$", f)
        if m:
            courses[m.group(1)].append(f)
            continue
        m = re.match(r"^(.+?)_\d{4}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\.mp4$", f)
        if m:
            courses[m.group(1)].append(f)

    print(f"找到 {len(courses)} 门课程:")
    for course, file_list in courses.items():
        print(f"  - {course} ({short_names.get(course, course)}): {len(file_list)} 个文件")

    success = 0
    for course, file_list in courses.items():
        short_name = short_names.get(course, course)
        file_list.sort()
        for i, filename in enumerate(file_list, 1):
            m = re.search(r"(\d{4}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2})", filename)
            if not m:
                continue
            new_name = f"{short_name}_{i:02d}_{course}_{m.group(1)}.mp4"
            if filename == new_name:
                continue
            old_path = os.path.join(args.dir, filename)
            new_path = os.path.join(args.dir, new_name)
            print(f"  [{i:02d}] {filename} -> {new_name}")
            if not args.dry_run:
                try:
                    os.rename(old_path, new_path)
                    success += 1
                except Exception as e:
                    print(f"  失败: {e}")

    print(f"\n完成! 重命名 {success} 个文件")


if __name__ == "__main__":
    main()
