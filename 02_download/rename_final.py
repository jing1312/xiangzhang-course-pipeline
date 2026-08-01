#!/usr/bin/env python3
"""
02_download/rename_final.py 【第二遍重命名】

把「课程名_上课时间.mp4」整理成「简称_序号_课程名_上课时间.mp4」，
序号按课程内上课时间排序。上传百度网盘前用这套命名，方便网盘里按简称分组。

用法：
    python rename_final.py --dir ./downloads \
        --short-names '{"临床药理学":"临床药理","生物药剂与药物动力学":"生物药剂","天然药物化学":"天然药化"}' \
        --dry-run
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict


def parse_args():
    parser = argparse.ArgumentParser(description="第二遍重命名：课程名_时间 -> 简称_序号_课程名_时间")
    parser.add_argument("--dir", required=True, help="视频所在目录")
    parser.add_argument("--short-names", default="{}", help="课程名->简称映射 JSON")
    parser.add_argument("--dry-run", action="store_true", help="只打印不改名")
    return parser.parse_args()


def main():
    args = parse_args()
    if not os.path.exists(args.dir):
        sys.exit(f"目录不存在: {args.dir}")
    short_names = json.loads(args.short_names)

    files = [f for f in os.listdir(args.dir) if f.endswith(".mp4")]
    print(f"共 {len(files)} 个文件")

    courses = defaultdict(list)
    for f in files:
        match = re.match(r"^(.+?)_\d{4}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\.mp4$", f)
        if match:
            courses[match.group(1)].append(f)
        else:
            match = re.match(r"^.+?_\d+_(.+?)_\d{4}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\.mp4$", f)
            if match:
                courses[match.group(1)].append(f)

    print(f"找到 {len(courses)} 门课程:")
    for course, file_list in courses.items():
        short = short_names.get(course, course)
        print(f"  - {course} ({short}): {len(file_list)} 个文件")

    success = 0
    for course, file_list in courses.items():
        short_name = short_names.get(course, course)
        file_list.sort()
        print(f"\n{course} ({short_name}):")
        for i, filename in enumerate(file_list, 1):
            match = re.search(r"(\d{4}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2})", filename)
            if not match:
                continue
            new_name = f"{short_name}_{i:02d}_{course}_{match.group(1)}.mp4"
            if filename == new_name:
                continue
            old_path = os.path.join(args.dir, filename)
            new_path = os.path.join(args.dir, new_name)
            if args.dry_run:
                print(f"  [{i:02d}] {filename} -> {new_name}")
                continue
            try:
                os.rename(old_path, new_path)
                print(f"  [{i:02d}] {new_name}")
                success += 1
            except Exception as e:
                print(f"  [{i:02d}] 失败: {e}")

    print(f"\n完成! 重命名 {success} 个文件")


if __name__ == "__main__":
    main()
