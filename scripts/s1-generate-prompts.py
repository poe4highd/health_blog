#!/usr/bin/env python3
"""
s1-generate-prompts.py — 步骤1：文章分段 + 图片提示词 + 情绪标注

调用 claude CLI 将文章分解为 5-10 个部分，为每部分生成：
  - 一句话总结
  - 情绪标注
  - 针对 gemini-3.1-flash-image-preview 优化的 16:9 图片提示词

用法: python scripts/s1-generate-prompts.py data-input/a0001.txt
"""

import argparse
import json
import os
import subprocess
import sys
import logging
from pathlib import Path
from datetime import datetime

import yaml


# ─── 常量 ─────────────────────────────────────────────────
SCRIPT_NAME = "s1-generate-prompts"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    """加载 config/config.yaml"""
    config_path = PROJECT_ROOT / "config" / "config.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def load_dotenv():
    """加载 .env 文件"""
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
    """配置日志，同时输出到控制台和文件"""
    logger = logging.getLogger(SCRIPT_NAME)
    logger.setLevel(logging.DEBUG)

    # 文件 handler
    fh = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)

    # 控制台 handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def build_prompt(article_text: str, min_seg: int, max_seg: int) -> str:
    """构造发送给 Claude CLI 的提示词"""
    return f"""你是一位专业的视觉内容策划师。请分析以下中文文章，完成以下任务：

1. 将文章分解为 {min_seg} 到 {max_seg} 个部分（按内容逻辑自然划分）。
2. 为每个部分：
   a. 用一句话总结该部分内容
   b. 标注该部分的情绪基调（如：好奇、幽默、严肃、温暖、激动、轻松、讽刺、坚定 等）
   c. 记录该段落的前20个字（text_start）和后20个字（text_end），用于定位原文
   d. 生成一个图片提示词，要求：
      - 针对 gemini-3.1-flash-image-preview 模型优化
      - 图片尺寸 16:9 横屏
      - 紧扣当前段落的核心意思，尽量以视觉画面展现主题
      - 可以包含少量关键词文字辅助表达，但主体必须是图片
      - 提示词用英文撰写，以获得最佳图片效果
3. 判断整篇文章的整体情绪基调。

极其重要的 JSON 格式要求：
- 不要在 JSON 字符串值中包含未转义的双引号，如需引用请用「」
- 不要在字符串值中包含换行符，如需换行请用 \\n
- 输出纯 JSON，不要用 markdown 代码块包裹

请严格按以下 JSON 格式输出：
{{
  "segments": [
    {{
      "id": 1,
      "summary": "一句话总结（中文）",
      "text_start": "该段落前20个字...",
      "text_end": "...该段落后20个字",
      "emotion": "情绪标注（中文）",
      "image_prompt": "English image prompt for gemini-3.1-flash-image-preview, 16:9..."
    }}
  ],
  "overall_emotion": "整体情绪基调（中文）"
}}

以下是需要分析的文章：

---
{article_text}
---"""


def call_claude_cli(prompt: str, timeout: int, logger: logging.Logger) -> str:
    """调用 claude CLI 并返回原始输出"""
    # 构造干净的子进程环境，移除 Claude Code 会话标记以避免嵌套检测
    clean_env = {k: v for k, v in os.environ.items()
                 if not k.startswith("CLAUDE")}

    logger.info("调用 claude CLI...")
    logger.debug(f"超时设置: {timeout}s")

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=clean_env,
        )
    except subprocess.TimeoutExpired:
        logger.error(f"Claude CLI 超时（已等待 {timeout} 秒）")
        sys.exit(1)
    except FileNotFoundError:
        logger.error("未找到 claude 命令，请确保 Claude CLI 已安装并在 PATH 中")
        sys.exit(1)

    if result.returncode != 0:
        logger.error(f"Claude CLI 调用失败 (returncode={result.returncode})")
        logger.error(f"stderr: {result.stderr[:500]}")
        logger.error(f"stdout: {result.stdout[:500]}")
        sys.exit(1)

    return result.stdout.strip()


def _try_parse_json(json_str: str) -> dict | None:
    """尝试解析 JSON，如果失败则尝试修复常见问题后重试"""
    # 直接解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # 修复：替换文本字段中未转义的双引号为「」
    # 这是 Claude 常见的 JSON 生成缺陷
    import re
    # 在 JSON 值的字符串内部，找到不属于 JSON 结构的双引号并替换
    # 策略：将 ": " 后面的字符串值中的裸引号替换
    fixed = json_str
    # 替换中文文本中常见的引号模式
    # 匹配引号内的引号对，如 ...文本"引用内容"文本...
    fixed = re.sub(
        r'(?<=[一-鿿\w,，。！？；：])"(?=[一-鿿])',
        '「', fixed
    )
    fixed = re.sub(
        r'(?<=[一-鿿])"(?=[一-鿿\w,，。！？；：])',
        '」', fixed
    )
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    return None


def parse_claude_output(raw_output: str, logger: logging.Logger) -> dict:
    """从 Claude CLI 的纯文本输出中提取结构化 JSON 数据

    Claude 纯文本输出可能是纯 JSON 或 markdown 代码块包裹的 JSON。
    包含 JSON 修复逻辑，处理 Claude 常见的引号未转义问题。
    """
    import re

    text = raw_output

    # 策略1：尝试直接解析（Claude 有时直接输出纯 JSON）
    data = _try_parse_json(text)
    if data and "segments" in data:
        logger.info("解析成功（纯 JSON）")
        return data

    # 策略2：提取 markdown 代码块内的 JSON
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        json_str = match.group(1).strip()
        data = _try_parse_json(json_str)
        if data and "segments" in data:
            logger.info("解析成功（从 markdown 代码块提取）")
            return data
        else:
            logger.warning("代码块内 JSON 解析失败（含修复尝试）")

    # 策略3：找到第一个 { 和最后一个 } 之间的内容
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        data = _try_parse_json(text[first_brace:last_brace + 1])
        if data and "segments" in data:
            logger.info("解析成功（截取 JSON 对象 + 修复）")
            return data

    logger.error("无法解析 Claude 输出为有效 JSON")
    logger.error(f"原始输出前 500 字: {text[:500]}")

    # 保存原始输出用于调试
    debug_path = PROJECT_ROOT / "data-output" / "_debug_claude_raw.txt"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(raw_output, encoding="utf-8")
    logger.error(f"原始输出已保存到: {debug_path}")
    sys.exit(1)


def _fill_segment_text(segments: list, article_text: str, logger: logging.Logger):
    """根据 text_start / text_end 从原文中提取完整段落文本，回填到 segment['text']"""
    # 去掉空行，保留原始文本用于搜索
    for seg in segments:
        text_start = seg.get("text_start", "").strip()
        text_end = seg.get("text_end", "").strip()

        if not text_start:
            logger.warning(f"段落 {seg['id']} 缺少 text_start")
            seg["text"] = ""
            continue

        # 在原文中查找 text_start 的位置
        start_idx = article_text.find(text_start)
        if start_idx == -1:
            # 尝试模糊匹配（取前10字）
            start_idx = article_text.find(text_start[:10])

        if start_idx == -1:
            logger.warning(f"段落 {seg['id']} 的 text_start 未在原文中找到: {text_start[:20]}")
            seg["text"] = ""
            continue

        # 在原文中查找 text_end 的位置
        if text_end:
            end_idx = article_text.find(text_end, start_idx)
            if end_idx != -1:
                end_idx += len(text_end)
            else:
                # 找下一个段落的开始位置
                end_idx = _find_next_segment_start(segments, seg["id"], article_text, start_idx)
        else:
            end_idx = _find_next_segment_start(segments, seg["id"], article_text, start_idx)

        seg["text"] = article_text[start_idx:end_idx].strip()
        logger.debug(f"段落 {seg['id']} 提取了 {len(seg['text'])} 字符")


def _find_next_segment_start(segments: list, current_id: int, article_text: str, after: int) -> int:
    """找到下一个段落在原文中的起始位置"""
    for seg in segments:
        if seg["id"] == current_id + 1:
            next_start = seg.get("text_start", "").strip()
            if next_start:
                idx = article_text.find(next_start, after)
                if idx != -1:
                    return idx
    return len(article_text)


def main():
    parser = argparse.ArgumentParser(
        description="步骤1：文章分段 + 图片提示词 + 情绪标注"
    )
    parser.add_argument("article_path", help="文章文件路径，如 data-input/a0001.txt")
    args = parser.parse_args()

    # 加载配置
    load_dotenv()
    cfg = load_config()

    # 解析文章路径和 article_id
    article_path = Path(args.article_path)
    if not article_path.is_absolute():
        article_path = PROJECT_ROOT / article_path
    article_path = article_path.resolve()

    try:
        article_path.relative_to(PROJECT_ROOT)
    except ValueError:
        print(f"❌ 路径必须在项目目录内: {article_path}")
        sys.exit(1)

    if not article_path.exists():
        print(f"❌ 文章文件不存在: {article_path}")
        sys.exit(1)

    article_id = article_path.stem  # 例如 a0001

    # 创建输出目录
    output_dir = PROJECT_ROOT / cfg.get("paths", {}).get("output_dir", "data-output") / article_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # 设置日志
    log_path = output_dir / f"{SCRIPT_NAME}.log"
    logger = setup_logger(log_path)

    logger.info(f"{'='*50}")
    logger.info(f"步骤 1：文章分段 + 提示词生成")
    logger.info(f"文章路径: {article_path}")
    logger.info(f"文章 ID: {article_id}")
    logger.info(f"输出目录: {output_dir}")
    logger.info(f"{'='*50}")

    # 读取文章
    article_text = article_path.read_text(encoding="utf-8").strip()
    logger.info(f"文章长度: {len(article_text)} 字符")

    # 配置参数
    seg_cfg = cfg.get("segmentation", {})
    min_seg = seg_cfg.get("min_segments", 5)
    max_seg = seg_cfg.get("max_segments", 10)
    timeout = seg_cfg.get("claude_timeout", 300)

    # 构造提示词
    prompt = build_prompt(article_text, min_seg, max_seg)
    logger.debug(f"提示词长度: {len(prompt)} 字符")

    # 调用 Claude CLI
    raw_output = call_claude_cli(prompt, timeout, logger)
    logger.info(f"Claude 返回 {len(raw_output)} 字符")

    # 解析输出
    data = parse_claude_output(raw_output, logger)

    # 补充 article_id
    data["article_id"] = article_id

    # 验证结构
    segments = data.get("segments", [])
    if not segments:
        logger.error("解析结果中 segments 为空")
        sys.exit(1)

    if len(segments) < min_seg or len(segments) > max_seg:
        logger.warning(f"段落数 {len(segments)} 不在预期范围 [{min_seg}, {max_seg}]")

    # 根据 text_start / text_end 从原文中提取完整段落文本
    _fill_segment_text(segments, article_text, logger)

    # 保存结果
    output_path = output_dir / f"{article_id}-prompts.json"
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    logger.info(f"✅ 分段完成，共 {len(segments)} 个部分")
    logger.info(f"   整体情绪: {data.get('overall_emotion', '未标注')}")
    for seg in segments:
        logger.info(f"   [{seg['id']}] {seg.get('emotion', '?')} — {seg.get('summary', '')[:50]}")
    logger.info(f"   输出文件: {output_path}")

    print(f"\n✅ 完成！共 {len(segments)} 个段落")
    print(f"   输出: {output_path}")


if __name__ == "__main__":
    main()
