#!/usr/bin/env python3
"""
run.py — 总控脚本：逐步执行管线，检查日志

按顺序执行 s1 → s2 → s3 → s4，每步完成后检查日志确保无错误。

用法:
  python scripts/run.py data-input/a0001.txt
  python scripts/run.py data-input/a0001.txt --emotion 轻松幽默
  python scripts/run.py data-input/a0001.txt --skip s1 s2   # 跳过已完成步骤
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

STEPS = [
    {
        "id": "s1",
        "name": "s1-generate-prompts",
        "script": "s1-generate-prompts.py",
        "description": "文章分段 + 提示词生成",
    },
    {
        "id": "s2",
        "name": "s2-generate-image",
        "script": "s2-generate-image.py",
        "description": "图片生成",
    },
    {
        "id": "s3",
        "name": "s3-generate-voice",
        "script": "s3-generate-voice.py",
        "description": "语音播报生成",
    },
    {
        "id": "s4",
        "name": "s4-generate-video",
        "script": "s4-generate-video.py",
        "description": "视频合成",
    },
]


def get_article_id(article_path: Path) -> str:
    return article_path.stem


def check_log_for_errors(log_path: Path) -> list:
    """检查日志文件中的错误"""
    errors = []
    if not log_path.exists():
        errors.append(f"日志文件不存在: {log_path}")
        return errors

    content = log_path.read_text(encoding="utf-8")
    for line in content.splitlines():
        if "[ERROR]" in line:
            errors.append(line.strip())

    return errors


def run_step(step: dict, article_path: str, venv_python: str) -> tuple:
    """执行单个步骤，返回 (success, duration_seconds)"""
    script_path = SCRIPTS_DIR / step["script"]

    if not script_path.exists():
        print(f"  ❌ 脚本不存在: {script_path}")
        return False, 0

    start = time.time()

    result = subprocess.run(
        [venv_python, str(script_path), article_path],
        text=True,
        timeout=600,  # 10 分钟超时
        cwd=str(PROJECT_ROOT),
    )

    duration = time.time() - start

    return result.returncode == 0, duration


def main():
    parser = argparse.ArgumentParser(
        description="文章插图视频生成管线 — 总控脚本"
    )
    parser.add_argument("article_path", help="文章文件路径，如 data-input/a0001.txt")
    parser.add_argument("--emotion", default=None,
                        help="总体情绪（可选，默认从 prompts.json 提取）")
    parser.add_argument("--skip", nargs="*", default=[],
                        help="跳过的步骤 ID，如 --skip s1 s2")
    args = parser.parse_args()

    # 解析路径
    article_path = Path(args.article_path)
    if not article_path.is_absolute():
        article_path = PROJECT_ROOT / article_path

    if not article_path.exists():
        print(f"❌ 文章文件不存在: {article_path}")
        sys.exit(1)

    article_id = get_article_id(article_path)

    # 使用 venv Python
    venv_python = str(PROJECT_ROOT / "venv" / "bin" / "python")
    if not Path(venv_python).exists():
        venv_python = sys.executable  # fallback

    # 输出目录
    output_dir = PROJECT_ROOT / "data-output" / article_id

    print(f"\n{'='*60}")
    print(f"  文章插图视频生成管线")
    print(f"  文章: {article_path.name}  ID: {article_id}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.skip:
        print(f"  跳过: {', '.join(args.skip)}")
    print(f"{'='*60}\n")

    total_start = time.time()
    results = []

    for step in STEPS:
        step_id = step["id"]
        step_name = step["name"]
        step_desc = step["description"]

        # 检查是否跳过
        if step_id in args.skip:
            print(f"  ⏭️  [{step_id}] {step_desc} — 已跳过")
            results.append((step_id, "SKIPPED", 0))
            continue

        print(f"\n  ▶ [{step_id}] {step_desc}...")
        print(f"  {'─'*50}")

        success, duration = run_step(step, str(article_path), venv_python)

        # 检查日志
        log_path = output_dir / f"{step_name}.log"
        errors = check_log_for_errors(log_path)

        if success and not errors:
            status = "OK"
            print(f"\n  ✅ [{step_id}] 完成 ({duration:.1f}s)")
            results.append((step_id, "OK", duration))
        elif success and errors:
            # 有警告但未失败
            status = "WARN"
            print(f"\n  ⚠️ [{step_id}] 完成但有警告 ({duration:.1f}s)")
            for err in errors[:3]:
                print(f"     {err}")
            results.append((step_id, "WARN", duration))
        else:
            status = "FAIL"
            print(f"\n  ❌ [{step_id}] 失败 ({duration:.1f}s)")
            if errors:
                for err in errors[:5]:
                    print(f"     {err}")
            print(f"\n  管线在 [{step_id}] 步骤中止。")
            results.append((step_id, "FAIL", duration))
            break

    total_duration = time.time() - total_start

    # 汇总
    print(f"\n{'='*60}")
    print(f"  执行汇总")
    print(f"{'='*60}")
    for step_id, status, duration in results:
        icon = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌", "SKIPPED": "⏭️"}[status]
        print(f"  {icon} {step_id}: {status} ({duration:.1f}s)")
    print(f"\n  总耗时: {total_duration:.1f}s")

    # 检查最终输出
    final_video = output_dir / f"{article_id}.mp4"
    if final_video.exists():
        size_mb = final_video.stat().st_size / (1024 * 1024)
        print(f"\n  🎬 最终视频: {final_video}")
        print(f"     大小: {size_mb:.1f}MB")
    print(f"{'='*60}\n")

    # 如果有失败步骤，返回非零退出码
    if any(s == "FAIL" for _, s, _ in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
