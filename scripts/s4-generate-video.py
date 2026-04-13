#!/usr/bin/env python3
"""
s4-generate-video.py — 步骤4：视频合成

将配图 + 音频合成为 MP4 视频。
每张图片的展示时长 = 对应段落音频时长。

用法: python scripts/s4-generate-video.py data-input/a0001.txt
"""

import argparse
import json
import os
import subprocess
import sys
import logging
from pathlib import Path

import yaml


# ─── 常量 ─────────────────────────────────────────────────
SCRIPT_NAME = "s4-generate-video"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config" / "config.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def load_dotenv():
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger(SCRIPT_NAME)
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def get_audio_duration(audio_path: Path) -> float:
    """使用 ffprobe 获取音频时长（秒）"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(audio_path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired):
        return 0.0


def write_concat_list(files: list[Path], list_path: Path):
    """写入 ffmpeg concat demuxer 所需的文件列表"""
    with open(list_path, "w", encoding="utf-8") as f:
        for file_path in files:
            escaped = str(file_path).replace("\\", "\\\\").replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")


def create_silence_audio(output_path: Path, duration: float, logger: logging.Logger,
                         sample_rate: int = 24000) -> bool:
    """生成指定时长的静音 WAV，用于补齐缺失段落的音频"""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", f"{duration:.2f}",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        return True

    logger.error(f"  ❌ 静音片段生成失败: {result.stderr[:200]}")
    return False


def build_slideshow_video(images_with_duration: list, output_path: Path,
                          cfg: dict, logger: logging.Logger) -> Path:
    """使用 ffmpeg 将图片序列合成为无声视频（按各图时长展示）"""
    video_cfg = cfg.get("video", {})
    width = video_cfg.get("width", 1920)
    height = video_cfg.get("height", 1080)
    fps = video_cfg.get("fps", 30)
    transition = video_cfg.get("transition", "fade")
    transition_dur = video_cfg.get("transition_duration", 0.5)

    # 使用 ffmpeg concat demuxer
    # 先为每张图创建一个独立的短视频片段，然后拼接
    tmp_dir = output_path.parent / "_tmp_video"
    tmp_dir.mkdir(exist_ok=True)
    segment_files = []

    for i, (img_path, duration) in enumerate(images_with_duration):
        seg_path = tmp_dir / f"seg_{i:03d}.mp4"
        segment_files.append(seg_path)

        if duration <= 0:
            duration = 5.0  # 默认5秒

        # 用 ffmpeg 将单张图片转为指定时长的视频片段
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(img_path),
            "-t", f"{duration:.2f}",
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
            "-c:v", "libx264",
            "-tune", "stillimage",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            str(seg_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.error(f"  ❌ 片段 {i} 生成失败: {result.stderr[:200]}")

    # 用 concat demuxer 拼接所有片段
    concat_list = tmp_dir / "concat.txt"
    write_concat_list(segment_files, concat_list)

    silent_video = tmp_dir / "silent.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(silent_video),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error(f"  ❌ 视频拼接失败: {result.stderr[:200]}")

    return silent_video


def build_segment_audio(audio_segments: list, output_dir: Path,
                        logger: logging.Logger) -> Path | None:
    """按段落顺序拼接分段音频；缺失段落补静音，避免画音错位"""
    if not audio_segments:
        return None

    tmp_dir = output_dir / "_tmp_video"
    tmp_dir.mkdir(exist_ok=True)

    concat_inputs = []
    real_audio_count = 0

    for seg_id, audio_path, duration in audio_segments:
        if audio_path is not None:
            concat_inputs.append(audio_path)
            real_audio_count += 1
            continue

        silence_path = tmp_dir / f"silence_{seg_id:03d}.wav"
        if not create_silence_audio(silence_path, duration, logger):
            return None
        logger.warning(f"  [{seg_id}] 缺少分段音频，已补 {duration:.1f}s 静音")
        concat_inputs.append(silence_path)

    if real_audio_count == 0:
        return None

    merged_audio = tmp_dir / "narration.wav"
    concat_list = tmp_dir / "audio_concat.txt"
    write_concat_list(concat_inputs, concat_list)

    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_list), "-c", "copy", str(merged_audio)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode == 0:
        logger.info(f"已按段落顺序拼接 {len(concat_inputs)} 段音频")
        return merged_audio

    logger.error(f"  ❌ 分段音频拼接失败: {result.stderr[:200]}")
    return None


def merge_audio_video(video_path: Path, audio_path: Path,
                      output_path: Path, cfg: dict, logger: logging.Logger) -> bool:
    """将视频和音频合并为最终 MP4"""
    video_cfg = cfg.get("video", {})
    audio_bitrate = video_cfg.get("audio_bitrate", "192k")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-shortest",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        logger.info(f"  ✅ 最终视频: {output_path.name}")
        return True
    else:
        logger.error(f"  ❌ 合并失败: {result.stderr[:200]}")
        return False


def cleanup_tmp(output_dir: Path, logger: logging.Logger):
    """清理临时文件"""
    tmp_dir = output_dir / "_tmp_video"
    if tmp_dir.exists():
        import shutil
        shutil.rmtree(tmp_dir)
        logger.debug("临时文件已清理")


def main():
    parser = argparse.ArgumentParser(
        description="步骤4：视频合成"
    )
    parser.add_argument("article_path", help="文章文件路径")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config()

    article_path = Path(args.article_path)
    if not article_path.is_absolute():
        article_path = PROJECT_ROOT / article_path
    article_path = article_path.resolve()

    try:
        article_path.relative_to(PROJECT_ROOT)
    except ValueError:
        print(f"❌ 路径必须在项目目录内: {article_path}")
        sys.exit(1)

    article_id = article_path.stem

    output_dir = PROJECT_ROOT / cfg.get("paths", {}).get("output_dir", "data-output") / article_id
    prompts_path = output_dir / f"{article_id}-prompts.json"

    if not prompts_path.exists():
        print(f"❌ 提示词文件不存在: {prompts_path}")
        sys.exit(1)

    # 设置日志
    log_path = output_dir / f"{SCRIPT_NAME}.log"
    logger = setup_logger(log_path)

    logger.info(f"{'='*50}")
    logger.info(f"步骤 4：视频合成")
    logger.info(f"文章 ID: {article_id}")
    logger.info(f"{'='*50}")

    # 读取提示词
    data = json.loads(prompts_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])

    # 收集图片和对应分段音频
    images_with_duration = []
    audio_segments = []
    total_duration = 0

    for seg in segments:
        seg_id = seg["id"]
        img_path = output_dir / f"{article_id}-{seg_id}.png"
        audio_path = output_dir / f"{article_id}-voice-{seg_id}.wav"

        if not img_path.exists():
            logger.warning(f"  ⚠️ 图片不存在: {img_path.name}，跳过")
            continue

        # 获取对应音频时长
        if audio_path.exists():
            duration = get_audio_duration(audio_path)
            if duration > 0:
                logger.info(f"  [{seg_id}] {img_path.name} → {duration:.1f}s")
                audio_segments.append((seg_id, audio_path, duration))
            else:
                duration = 5.0
                logger.warning(f"  [{seg_id}] {audio_path.name} 时长探测失败，改用 {duration:.1f}s 静音补齐")
                audio_segments.append((seg_id, None, duration))
        else:
            # 无音频时使用默认时长
            duration = 5.0
            logger.warning(f"  [{seg_id}] {img_path.name} → {duration:.1f}s（无音频，使用默认）")
            audio_segments.append((seg_id, None, duration))

        images_with_duration.append((img_path, duration))
        total_duration += duration

    if not images_with_duration:
        logger.error("没有可用的图片")
        sys.exit(1)

    logger.info(f"共 {len(images_with_duration)} 张图片，总时长 {total_duration:.1f}s")

    # 生成幻灯片视频
    logger.info("生成幻灯片视频...")
    silent_video = build_slideshow_video(images_with_duration, output_dir, cfg, logger)

    # 合并音频
    final_output = output_dir / f"{article_id}.mp4"
    merged_audio = build_segment_audio(audio_segments, output_dir, logger)

    if merged_audio is not None and merged_audio.exists():
        logger.info("合并音频和视频...")
        success = merge_audio_video(silent_video, merged_audio, final_output, cfg, logger)
    else:
        # 无音频，直接使用静音视频
        logger.warning("未找到合并音频，生成静音视频")
        import shutil
        shutil.copy2(silent_video, final_output)
        success = True

    # 清理临时文件
    cleanup_tmp(output_dir, logger)

    if success:
        # 获取最终视频信息
        duration = get_audio_duration(final_output)
        size_mb = final_output.stat().st_size / (1024 * 1024)
        logger.info(f"{'='*50}")
        logger.info(f"✅ 视频合成完成!")
        logger.info(f"   文件: {final_output}")
        logger.info(f"   时长: {duration:.1f}s")
        logger.info(f"   大小: {size_mb:.1f}MB")
        logger.info(f"{'='*50}")

        print(f"\n✅ 完成！视频已生成")
        print(f"   {final_output}")
        print(f"   时长: {duration:.1f}s, 大小: {size_mb:.1f}MB")
    else:
        logger.error("视频合成失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
