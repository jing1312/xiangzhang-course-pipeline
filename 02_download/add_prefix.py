#!/usr/bin/env python3
"""
02_download/add_prefix.py

给无序号的视频文件按课程加「简称_序号_」前缀（如 天然药化_01_），
序号按课程内上课时间排序。与 rename_videos.py 二选一：
  - add_prefix.py：保留原文件名，只在前面加前缀
  - rename_videos.py：直接改成「简称_序号_课程名_时间」标准格式

用法：
    python add_prefix.py --dir "D:\\文档\\课程视频" \
        --short-names '{"临床药理学":"临床药理"}'
"""
import argparse
import json
import os
import re
from collections import defaultdict


def parse_args():
    parser = argparse.ArgumentParser(description="批量添加序号前缀")
    parser.add_argument("--dir", required=True, help="视频所在目录")
    parser.add_argument("--short-names", default="{}", help="课程名->简称映射 JSON")
    parser.add_argument("--dry-run", action="store_true", help="只打印不改名")
    return parser.parse_args()


def main():
    args = parse_args()
    short_names = json.loads(args.short_names)
    files = [f for f in os.listdir(args.dir) if f.lower().endswith(".mp4")]

    courses = defaultdict(list)
    for f in files:
        m = re.match(r"^(.+?)_\d{4}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\.mp4$", f)
        if m:
            courses[m.group(1)].append(f)

    print(f"找到 {len(courses)} 门课程:")
    for course, file_list in courses.items():
        print(f"  - {course}: {len(file_list)} 个文件")

    success = failed = skipped = 0
    for course, file_list in courses.items():
        short_name = short_names.get(course, course)
        file_list.sort()
        print(f"\n{course} ({short_name}):")
        for i, filename in enumerate(file_list, 1):
            if filename.startswith(short_name + "_"):
                skipped += 1
                continue
            new_name = f"{short_name}_{i:02d}_{filename}"
            print(f"  [{i:02d}] {filename} -> {new_name}")
            if not args.dry_run:
                try:
                    os.rename(os.path.join(args.dir, filename), os.path.join(args.dir, new_name))
                    success += 1
                except Exception as e:
                    print(f"  失败: {e}")
                    failed += 1

    print(f"\n完成! 成功: {success}, 失败: {failed}, 跳过(已有前缀): {skipped}")


if __name__ == "__main__":
    main()
