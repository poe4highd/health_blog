#!/usr/bin/env python3
"""
s2-generate-image.py — 步骤2：根据提示词生成配图

读取 a0001-prompts.json 中的 image_prompt，调用 Gemini API 生成图片。

用法: python scripts/s2-generate-image.py data-input/a0001.txt
"""

import argparse
import json
import os
import sys
import logging
from pathlib import Path
from datetime import datetime

import yaml


# ─── 常量 ─────────────────────────────────────────────────
SCRIPT_NAME = "s2-generate-image"
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


def generate_image(prompt: str, output_path: Path, cfg: dict, logger: logging.Logger) -> bool:
    """调用 Gemini API 生成单张图片"""
    from google import genai
    from google.genai import types
    from PIL import Image
    import io

    model = cfg.get("image", {}).get("model", "gemini-3.1-flash-image-preview")
    aspect_ratio = cfg.get("image", {}).get("aspect_ratio", "16:9")
    style_prompt = cfg.get("image", {}).get("style_prompt", "").strip()

    # 合成完整提示词
    full_prompt = f"{style_prompt}\n\n{prompt}" if style_prompt else prompt

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        logger.error("未设置 GEMINI_API_KEY 环境变量")
        return False

    client = genai.Client(api_key=api_key)

    try:
        # 使用 generate_images API
        response = client.models.generate_images(
            model=model,
            prompt=full_prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect_ratio,
            ),
        )

        if response.generated_images:
            image_bytes = response.generated_images[0].image.image_bytes
            image = Image.open(io.BytesIO(image_bytes))
            image.save(str(output_path), "PNG")
            logger.info(f"  ✅ 图片已保存: {output_path.name} ({image.width}x{image.height})")
            return True
        else:
            logger.warning(f"  ⚠️ API 未返回图片")
            return False

    except Exception as e:
        error_msg = str(e)
        logger.warning(f"  ⚠️ generate_images 失败: {error_msg[:200]}")

        # 备选方案：使用 generate_content 带 IMAGE modality
        try:
            logger.info("  尝试备选方案: generate_content...")
            response = client.models.generate_content(
                model=model,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    image = Image.open(io.BytesIO(part.inline_data.data))
                    image.save(str(output_path), "PNG")
                    logger.info(f"  ✅ 图片已保存（备选方案）: {output_path.name}")
                    return True
        except Exception as e2:
            logger.error(f"  ❌ 备选方案也失败: {str(e2)[:200]}")

        return False


def main():
    parser = argparse.ArgumentParser(
        description="步骤2：根据提示词生成配图"
    )
    parser.add_argument("article_path", help="文章文件路径，如 data-input/a0001.txt")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config()

    # 解析
    article_path = Path(args.article_path)
    if not article_path.is_absolute():
        article_path = PROJECT_ROOT / article_path
    article_id = article_path.stem

    output_dir = PROJECT_ROOT / cfg.get("paths", {}).get("output_dir", "data-output") / article_id
    prompts_path = output_dir / f"{article_id}-prompts.json"

    if not prompts_path.exists():
        print(f"❌ 提示词文件不存在: {prompts_path}")
        print(f"   请先运行: python scripts/s1-generate-prompts.py {args.article_path}")
        sys.exit(1)

    # 设置日志
    log_path = output_dir / f"{SCRIPT_NAME}.log"
    logger = setup_logger(log_path)

    logger.info(f"{'='*50}")
    logger.info(f"步骤 2：图片生成")
    logger.info(f"文章 ID: {article_id}")
    logger.info(f"提示词文件: {prompts_path}")
    logger.info(f"{'='*50}")

    # 读取提示词
    data = json.loads(prompts_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    logger.info(f"共 {len(segments)} 个段落需要生成图片")

    # 逐段生成
    success_count = 0
    fail_count = 0
    for seg in segments:
        seg_id = seg["id"]
        prompt = seg.get("image_prompt", "")
        if not prompt:
            logger.warning(f"段落 {seg_id} 没有 image_prompt，跳过")
            fail_count += 1
            continue

        output_path = output_dir / f"{article_id}-{seg_id}.png"

        # 跳过已存在的图片
        if output_path.exists() and output_path.stat().st_size > 0:
            logger.info(f"  ⏭️ 跳过段落 {seg_id}（图片已存在）")
            success_count += 1
            continue

        logger.info(f"  [{seg_id}/{len(segments)}] {seg.get('summary', '')[:40]}...")
        logger.debug(f"  prompt: {prompt[:100]}...")

        if generate_image(prompt, output_path, cfg, logger):
            success_count += 1
        else:
            fail_count += 1

    logger.info(f"{'='*50}")
    logger.info(f"✅ 图片生成完成: 成功 {success_count}, 失败 {fail_count}")
    logger.info(f"{'='*50}")

    if fail_count > 0:
        logger.warning(f"有 {fail_count} 张图片生成失败，可重新运行此脚本重试")

    print(f"\n✅ 完成！成功 {success_count}/{len(segments)} 张图片")


if __name__ == "__main__":
    main()
