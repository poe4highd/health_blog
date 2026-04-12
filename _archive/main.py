#!/usr/bin/env python3
"""
新闻简报视频生成器 MVP
流程：Claude CLI 获取新闻 → Gemini API 生成插图 → Edge TTS 语音合成 → ffmpeg 合成视频
支持断点续跑：已有结果的步骤自动跳过，使用 --force 强制重新生成
"""

import argparse
import subprocess
import asyncio
import os
import sys
import json
import re
import socket
from pathlib import Path
from datetime import datetime

import yaml


# ─── 加载 .env ──────────────────────────────────────────
def _load_dotenv():
    """从 .env 文件加载环境变量"""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:  # 不覆盖已有环境变量
            os.environ[key] = value

_load_dotenv()

# ─── 加载 YAML 配置 ──────────────────────────────────────
def _load_config() -> dict:
    """从 config/config.yaml 加载配置"""
    config_path = Path(__file__).parent / "config" / "config.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

CFG = _load_config()

# ─── 配置 ───────────────────────────────────────────────
OUTPUT_DIR = Path(CFG.get("output", {}).get("dir", "output"))
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H")
OUT_PREFIX = CFG.get("output", {}).get("prefix", "News")
PREFIX = f"{OUT_PREFIX}_{TIMESTAMP}"
NEWS_FILE = OUTPUT_DIR / f"{PREFIX}.json"
IMAGE_FILE = OUTPUT_DIR / f"{PREFIX}.png"  # 备用（fallback 时使用）
VIDEO_FILE = OUTPUT_DIR / f"{PREFIX}.mp4"
HTML_FILE = OUTPUT_DIR / f"{PREFIX}.html"

# 从配置读取参数
TTS_VOICE = CFG.get("tts", {}).get("voice", "zh-CN-XiaoxiaoNeural")
TTS_RATE = CFG.get("tts", {}).get("rate", "+10%")
TTS_OPENING = CFG.get("tts", {}).get("opening", "各位听众好，以下是今天的新闻简报。")
TTS_CLOSING = CFG.get("tts", {}).get("closing", "以上就是今天的新闻简报，感谢收听。")

IMAGE_WIDTH = CFG.get("image", {}).get("width", 1080)
IMAGE_HEIGHT = CFG.get("image", {}).get("height", 1920)
IMAGE_MODEL = CFG.get("image", {}).get("model", "gemini-2.5-flash-image")

NEWS_PROMPT = CFG.get("news", {}).get("prompt", "").strip()
NEWS_TIMEOUT = CFG.get("news", {}).get("timeout", 180)
NEWS_COUNT = CFG.get("news", {}).get("count", 5)

IMAGE_PROMPT_TPL = CFG.get("image", {}).get("prompt", "").strip()

VIDEO_AUDIO_BITRATE = CFG.get("video", {}).get("audio_bitrate", "192k")
VIDEO_WIDTH = CFG.get("video", {}).get("width", 1080)
VIDEO_HEIGHT = CFG.get("video", {}).get("height", 1920)

# 全局强制重新生成标志
FORCE = False


# ─── 工具函数 ────────────────────────────────────────────
def _is_proxy_reachable(url: str, timeout: float = 1.0) -> bool:
    """快速检测代理端口是否可连接（最多等待 timeout 秒）"""
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        with socket.create_connection((p.hostname, p.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def _should_skip(filepath: Path, step_name: str) -> bool:
    """检查是否应跳过当前步骤（文件已存在且非强制模式）"""
    if FORCE:
        return False
    if filepath.exists() and filepath.stat().st_size > 0:
        print(f"⏭️  跳过{step_name}（已存在：{filepath}）")
        return True
    return False


# ─── 步骤 1：获取新闻 ──────────────────────────────────
def fetch_news():
    """调用 Claude Code CLI 获取 5 条新闻简报（JSON 格式）
    返回 (news_list, news_text)：list of dicts 和拼接好的纯文本
    """
    if _should_skip(NEWS_FILE, "步骤 1/4：获取新闻"):
        data = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
        news_text = "\n\n".join(item["content"] for item in data["news"])
        print(f"   新闻内容：{len(news_text)} 字")
        return data["news"], news_text

    print("\n📰 步骤 1/4：获取新闻简报...")

    if NEWS_PROMPT:
        prompt = NEWS_PROMPT
    else:
        prompt = (
            '整理最近24小时的新闻简报，用于1分钟播报新闻稿。'
            f'请用中文输出{NEWS_COUNT}条新闻简报。'
            '必须严格按以下JSON格式输出，不要输出任何其他内容：\n'
            '{\n'
            '  "news": [\n'
            '    {"content": "新闻内容...", "source": "来源名称和链接"},\n'
            '    ...\n'
            '  ]\n'
            '}\n'
            '重要：每条新闻简洁明了，适合播报。source字段填写新闻来源名称和原文链接。'
            '新闻内容和来源中不要使用双引号，如需引用请用「」或单引号代替。'
        )

    # 构造干净的子进程环境，移除所有 Claude Code 会话标记以避免嵌套检测
    clean_env = {k: v for k, v in os.environ.items()
                 if not k.startswith("CLAUDE")}

    # 若代理配置了但端口不可达，自动移除以便直连（支持 VPN 开/关两种状态）
    for _proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        _proxy_val = clean_env.get(_proxy_key, "")
        if _proxy_val and not _is_proxy_reachable(_proxy_val):
            print(f"⚠️  代理 {_proxy_val} 不可达，已自动跳过（直连模式）")
            clean_env.pop(_proxy_key, None)

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--allowedTools", "WebSearch", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=NEWS_TIMEOUT,
            env=clean_env,
        )
    except subprocess.TimeoutExpired:
        print(f"❌ Claude CLI 获取新闻超时（已等待 {NEWS_TIMEOUT} 秒）")
        print("   如果代理网络较慢或拉取新闻时间较长，请在 config.yaml 中进一步调大 `news.timeout`。")
        sys.exit(1)

    if result.returncode != 0:
        print(f"❌ Claude CLI 调用失败 (returncode={result.returncode})")
        print(f"   stderr: {result.stderr[:500]}")
        print(f"   stdout: {result.stdout[:500]}")
        sys.exit(1)

    raw = result.stdout.strip()

    # 从 Claude JSON 输出中提取 result 文本
    text_content = raw
    try:
        claude_output = json.loads(raw)
        if isinstance(claude_output, dict) and "result" in claude_output:
            text_content = claude_output["result"]
    except json.JSONDecodeError:
        pass

    # 多层解析尝试
    data = None

    # 尝试 1：直接解析
    try:
        data = json.loads(text_content)
    except (json.JSONDecodeError, TypeError):
        pass

    # 尝试 2：提取 markdown 代码块内的 JSON
    if data is None:
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text_content)
        json_str = match.group(1).strip() if match else text_content
        # 修复 content 中可能的未转义双引号
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # 尝试修复：替换非结构性的双引号
            fixed = re.sub(
                r'(?<=[^\\{}\[\]:,])"(?=[^:,\]\}\n])',
                '「',
                json_str
            )
            try:
                data = json.loads(fixed)
            except json.JSONDecodeError:
                pass

    # 尝试 3：逐行提取新闻内容（终极兜底）
    if data is None or "news" not in data:
        items = []
        for m in re.finditer(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', text_content):
            content = m.group(1).replace('\\"', '"')
            # 在同一行或下一行寻找 source
            src_match = re.search(r'"source"\s*:\s*"((?:[^"\\]|\\.)*)"', text_content[m.end():m.end()+500])
            source = src_match.group(1).replace('\\"', '"') if src_match else ""
            items.append({"content": content, "source": source})
        if items:
            data = {"news": items}

    # 最终兜底
    if data is None or "news" not in data:
        debug_file = OUTPUT_DIR / f"{PREFIX}_raw_debug.txt"
        debug_file.write_text(raw, encoding="utf-8")
        print(f"❌ 无法解析 Claude 返回的 JSON")
        print(f"   原始输出已保存到 {debug_file}")
        print(f"   前 300 字：{text_content[:300]}")
        sys.exit(1)

    # 保存完整 JSON
    NEWS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    news_text = "\n\n".join(item["content"] for item in data["news"])
    print(f"✅ 新闻已保存到 {NEWS_FILE}")
    print(f"   共 {len(data['news'])} 条，{len(news_text)} 字")
    return data["news"], news_text


# ─── 步骤 2：生成插图 ──────────────────────────────────
def generate_image(news_items: list) -> list:
    """为每条新闻分别调用 Gemini API 生成插图，保留为独立文件（不合成）"""
    n = len(news_items)
    panel_files = [OUTPUT_DIR / f"{PREFIX}_panel_{i}.jpg" for i in range(n)]

    if not FORCE and all(p.exists() and p.stat().st_size > 0 for p in panel_files):
        print(f"⏭️  跳过步骤 2/4：生成插图（已存在 {n} 张）")
        return panel_files

    print("\n🎨 步骤 2/4：生成新闻插图...")

    from google import genai

    news_text = "\n\n".join(item["content"] for item in news_items)

    # ── 初始化 Gemini client ──
    # Imagen 模型需要 Vertex AI；其他 Gemini 模型优先用 API Key（可用性更广）
    use_vertex = "imagen" in IMAGE_MODEL.lower()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    cred_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

    if use_vertex and cred_file and os.path.exists(cred_file):
        try:
            with open(cred_file, 'r') as f:
                cred_data = json.load(f)
            project_id = cred_data.get("project_id")
            client = genai.Client(vertexai=True, project=project_id, location="us-central1")
            print("🔗 已连接到 Google Cloud Vertex AI")
        except Exception as e:
            print(f"❌ Vertex AI Client 初始化失败：{e}")
            _generate_image_via_cli(news_text)
            return
    elif api_key:
        client = genai.Client(api_key=api_key)
        print(f"🔗 已连接到 Google AI Studio（模型：{IMAGE_MODEL}）")
    else:
        print("⚠️  未设置 GEMINI_API_KEY 或 GOOGLE_APPLICATION_CREDENTIALS")
        _generate_image_via_cli(news_text)
        return

    # ── 逐条生成插图面板 ──
    panel_files = []
    for i, item in enumerate(news_items):
        panel_path = OUTPUT_DIR / f"{PREFIX}_panel_{i}.jpg"
        content = item["content"]
        print(f"   生成第 {i+1}/{len(news_items)} 张插图：{content[:30]}...")

        if IMAGE_PROMPT_TPL:
            prompt = IMAGE_PROMPT_TPL + f"\n{content}"
        else:
            prompt = (
                f"根据以下新闻，生成一张视觉冲击力强的新闻插画。"
                f"风格：次世代 3D 渲染，电影级光影，色彩鲜明，无任何文字。\n"
                f"新闻：{content}"
            )

        success = False
        try:
            if "imagen" in IMAGE_MODEL.lower():
                response = client.models.generate_images(
                    model=IMAGE_MODEL,
                    prompt=prompt,
                    config=genai.types.GenerateImagesConfig(
                        number_of_images=1,
                        output_mime_type="image/jpeg",
                        aspect_ratio="1:1",
                    ),
                )
                if response.generated_images:
                    panel_path.write_bytes(response.generated_images[0].image.image_bytes)
                    success = True
            else:
                response = client.models.generate_content(
                    model=IMAGE_MODEL,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                    ),
                )
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        panel_path.write_bytes(part.inline_data.data)
                        success = True
                        break
        except Exception as e:
            print(f"⚠️  第 {i+1} 张插图生成失败：{e}")

        if success:
            panel_files.append(panel_path)
        else:
            print(f"⚠️  第 {i+1} 张未返回图片，跳过")

    if not panel_files:
        print("⚠️  所有插图生成失败，使用备选方案...")
        _generate_fallback_image(news_text)
        return []

    print(f"✅ 已生成 {len(panel_files)} 张插图")
    return panel_files

def _force_aspect_ratio(image_path: Path):
    """强制裁切/缩放并用模糊背景处理画布，匹配配置给定的宽和高，彻底根治视频黑边问题"""
    from PIL import Image, ImageFilter
    
    try:
        with Image.open(image_path) as img:
            target_w, target_h = VIDEO_WIDTH, VIDEO_HEIGHT
            img = img.convert("RGBA")
            
            # 使用模糊的本体作为全屏背板
            bg_ratio = max(target_w / img.width, target_h / img.height)
            bg_w, bg_h = int(img.width * bg_ratio), int(img.height * bg_ratio)
            
            # 兼容低版本 Pillow (Resampling) 验证
            resample_mode = getattr(Image, "Resampling", Image).LANCZOS
            bg = img.resize((bg_w, bg_h), resample_mode)
            
            # 居中裁切底板
            left = (bg.width - target_w) / 2
            top = (bg.height - target_h) / 2
            bg = bg.crop((left, top, left + target_w, top + target_h))
            bg = bg.filter(ImageFilter.GaussianBlur(25))  # 严重模糊
            
            # 2. 将原图等比例缩放完整放入
            fg_ratio = min(target_w / img.width, target_h / img.height)
            fg_w, fg_h = int(img.width * fg_ratio), int(img.height * fg_ratio)
            fg = img.resize((fg_w, fg_h), resample_mode)
            
            # 复合
            bg.paste(fg, ((target_w - fg_w) // 2, (target_h - fg_h) // 2), fg)
            
            # 转储并覆盖回写
            result = bg.convert("RGB")
            result.save(image_path, "PNG")
            print(f"   已自动剪裁适配视频画幅: {target_w}x{target_h}")
    except Exception as e:
        print(f"⚠️  通过 Pillow 动态格式化图片失败: {e}")

def _paste_fit(canvas, img, x: int, y: int, w: int, h: int, resample):
    """将 img 等比例缩放到 (w, h) 框内并居中粘贴到 canvas 的 (x, y) 位置"""
    ratio = min(w / img.width, h / img.height)
    new_w, new_h = max(1, int(img.width * ratio)), max(1, int(img.height * ratio))
    img_resized = img.resize((new_w, new_h), resample)
    canvas.paste(img_resized, (x + (w - new_w) // 2, y + (h - new_h) // 2))


def _composite_panels(panel_files: list):
    """将多张面板图按布局合成为一张 9:16 竖版图：头条居中大图 + 四角小图"""
    from PIL import Image, ImageFilter

    target_w, target_h = VIDEO_WIDTH, VIDEO_HEIGHT
    resample = getattr(Image, "Resampling", Image).LANCZOS

    # 加载所有面板
    panels = []
    for p in panel_files:
        try:
            panels.append(Image.open(p).convert("RGB"))
        except Exception as e:
            print(f"⚠️  加载面板失败 {p.name}: {e}")

    if not panels:
        print("⚠️  无可用面板，跳过合成")
        return

    # 以头条图做模糊背景
    bg = panels[0]
    bg_ratio = max(target_w / bg.width, target_h / bg.height)
    bg = bg.resize((int(bg.width * bg_ratio), int(bg.height * bg_ratio)), resample)
    lx = (bg.width - target_w) // 2
    ly = (bg.height - target_h) // 2
    bg = bg.crop((lx, ly, lx + target_w, ly + target_h))
    bg = bg.filter(ImageFilter.GaussianBlur(30))

    # 半透明遮罩增强对比度
    overlay = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 100))
    canvas = bg.convert("RGBA")
    canvas.alpha_composite(overlay)
    canvas = canvas.convert("RGB")

    n = len(panels)
    if n == 1:
        _paste_fit(canvas, panels[0], 0, 0, target_w, target_h, resample)
    elif n <= 4:
        slot_h = target_h // n
        for i, panel in enumerate(panels):
            _paste_fit(canvas, panel, 0, i * slot_h, target_w, slot_h, resample)
    else:
        # 标准5格：中心大图（占竖直中间1/2）+ 四角小图
        center_h = target_h // 2
        center_y = (target_h - center_h) // 2
        _paste_fit(canvas, panels[0], 0, center_y, target_w, center_h, resample)

        corner_w = target_w // 2
        corner_h = center_y  # 上下各占剩余高度的一半
        corners = [
            (0,        0),                    # 左上
            (corner_w, 0),                    # 右上
            (0,        center_y + center_h),  # 左下
            (corner_w, center_y + center_h),  # 右下
        ]
        for i, (cx, cy) in enumerate(corners):
            if i + 1 < len(panels):
                _paste_fit(canvas, panels[i + 1], cx, cy, corner_w, corner_h, resample)

    canvas.save(str(IMAGE_FILE), "PNG")
    print(f"✅ 插图合成完成（{n} 格）已保存到 {IMAGE_FILE}")


def _generate_image_via_cli(news_text: str):
    """使用 Gemini CLI 生成图片（备选方案）"""
    try:
        prompt = (
            f"根据以下新闻内容，生成一张包含5个新闻插图的图片，"
            f"扁平设计风格，不包含文字：\n\n{news_text}"
        )
        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and IMAGE_FILE.exists():
            print(f"✅ 插图已通过 Gemini CLI 生成")
            return
    except Exception:
        pass

    print("⚠️  Gemini CLI 也不可用，使用本地占位图...")
    _generate_fallback_image(news_text)


def _generate_fallback_image(news_text: str):
    """生成一张包含新闻标题摘要的占位图片"""
    from PIL import Image, ImageDraw, ImageFont

    width, height = IMAGE_WIDTH, IMAGE_HEIGHT
    img = Image.new("RGB", (width, height), color="#1a1a2e")
    draw = ImageDraw.Draw(img)

    # 尝试加载中文字体
    font_paths = [
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    font_title = None
    font_body = None
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                font_title = ImageFont.truetype(fp, 48)
                font_body = ImageFont.truetype(fp, 30)
                break
            except Exception:
                continue
    if font_title is None:
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()

    # 绘制标题
    title = f"📰 新闻简报"
    subtitle = datetime.now().strftime('%Y年%m月%d日')
    draw.text((40, 60), title, fill="#e94560", font=font_title)
    draw.text((40, 120), subtitle, fill="#888888", font=font_body)

    # 绘制分隔线
    draw.line([(40, 170), (width - 40, 170)], fill="#333355", width=2)

    # 绘制新闻内容摘要（竖屏布局，每条新闻更大的展示空间）
    lines = news_text.split("\n")
    y = 200
    news_idx = 0
    colors = ["#0f3460", "#533483", "#e94560", "#16213e", "#1a1a4e"]
    block_height = 300
    for line in lines:
        line = line.strip()
        if not line:
            continue
        news_idx += 1
        if news_idx > 5:
            break

        # 绘制色块背景
        block_color = colors[(news_idx - 1) % len(colors)]
        draw.rounded_rectangle(
            [(30, y), (width - 30, y + block_height)],
            radius=15,
            fill=block_color,
        )

        # 自动换行绘制文字
        max_chars_per_line = 22  # 竖屏每行字数
        text_lines = [line[i:i+max_chars_per_line] for i in range(0, len(line), max_chars_per_line)]
        ty = y + 30
        for idx, tl in enumerate(text_lines[:5]):  # 最多显示5行
            prefix = "▶ " if idx == 0 else "  "
            draw.text((50, ty), f"{prefix}{tl}", fill="white", font=font_body)
            ty += 50

        y += block_height + 20

    img.save(str(IMAGE_FILE))
    print(f"✅ 占位插图已生成：{IMAGE_FILE}")


# ─── 步骤 3：语音合成 ──────────────────────────────────
def generate_audio(news_items: list):
    """为开场、每条新闻、结尾分别生成 TTS 音频片段"""
    n = len(news_items)
    open_file = OUTPUT_DIR / f"{PREFIX}_audio_open.mp3"
    close_file = OUTPUT_DIR / f"{PREFIX}_audio_close.mp3"
    audio_files = [OUTPUT_DIR / f"{PREFIX}_audio_{i}.mp3" for i in range(n)]
    all_files = [open_file, close_file] + audio_files

    if not FORCE and all(f.exists() and f.stat().st_size > 0 for f in all_files):
        print(f"⏭️  跳过步骤 3/4：语音合成（已存在 {n+2} 个片段）")
        return audio_files, open_file, close_file

    print("\n🎙️ 步骤 3/4：合成语音播报...")

    async def _gen_all():
        import edge_tts
        session = _get_session()
        opening = TTS_OPENING.replace("{session}", session)
        closing = TTS_CLOSING.replace("{session}", session)
        clips = [opening] + [item["content"] for item in news_items] + [closing]
        files = [open_file] + audio_files + [close_file]
        for text, path in zip(clips, files):
            comm = edge_tts.Communicate(text, voice=TTS_VOICE, rate=TTS_RATE)
            await comm.save(str(path))
            print(f"   ✓ {path.name}")

    asyncio.run(_gen_all())
    print(f"✅ 语音合成完成（共 {n+2} 个片段）")
    return audio_files, open_file, close_file


# ─── 步骤 4：合成视频 ──────────────────────────────────
def _get_duration(path: Path) -> float:
    """用 ffprobe 获取媒体文件时长（秒）"""
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    if probe.returncode == 0:
        return float(json.loads(probe.stdout).get("format", {}).get("duration", 0))
    return 0.0


def _get_bgm_file() -> Path | None:
    """根据当前时段返回对应的背景音乐文件"""
    session_to_file = {
        "清晨快报": "Morning News.wav",
        "午间快报": "Noon News.wav",
        "下午快报": "Afternoon News.wav",
        "晚间快报": "Night News.wav",
    }
    bgm = Path(__file__).parent / "data" / session_to_file[_get_session()]
    return bgm if bgm.exists() else None


def _mix_bgm(video_path: Path):
    """将时段背景音乐混入视频：开头/结尾 4 秒满音量，其余 30% 音量"""
    bgm = _get_bgm_file()
    if bgm is None:
        print("⚠️  未找到背景音乐文件，跳过混音")
        return

    duration = _get_duration(video_path)
    if duration <= 0:
        print("⚠️  无法获取视频时长，跳过混音")
        return

    # 视频过短时缩小 fade 窗口，确保不重叠
    fade_s = min(4.0, duration / 3)
    fade_end = max(fade_s, duration - fade_s)
    vol_expr = f"if(lt(t,{fade_s:.3f}),1,if(gt(t,{fade_end:.3f}),1,0.1))"

    print(f"🎵 混入背景音乐：{bgm.name}")
    tmp_out = video_path.with_suffix(".bgm_tmp.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", "-1", "-i", str(bgm),
        "-filter_complex",
        f"[1:a]volume=volume='{vol_expr}':eval=frame[bgm];"
        f"[0:a][bgm]amix=inputs=2:duration=first:normalize=0[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", VIDEO_AUDIO_BITRATE,
        "-t", f"{duration:.3f}",
        str(tmp_out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"⚠️  背景音乐混音失败：{result.stderr[-400:]}")
        try: tmp_out.unlink()
        except: pass
        return

    video_path.unlink()
    tmp_out.rename(video_path)
    print(f"✅ 背景音乐已混入（时长 {duration:.1f}s，淡入淡出 {fade_s:.0f}s）")


def _get_session() -> str:
    """根据当前小时返回时段名称（与 cron 4个时间点对应）"""
    hour = datetime.now().hour
    if hour < 12:
        return "清晨快报"
    elif hour < 16:
        return "午间快报"
    elif hour < 20:
        return "下午快报"
    else:
        return "晚间快报"


def _concat_audio_files(files: list, output: Path):
    """用 ffmpeg concat demuxer 拼接 MP3（正确处理 VBR 帧计数头，避免 closing 被截断）"""
    tmp_list = output.parent / f"_aconcat_{output.stem}.txt"
    tmp_list.write_text("\n".join(f"file '{Path(f).resolve()}'" for f in files))
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp_list), "-c", "copy", str(output)],
        capture_output=True, text=True, timeout=60,
    )
    try: tmp_list.unlink()
    except: pass
    if result.returncode != 0:
        raise RuntimeError(f"音频拼接失败: {result.stderr[-200:]}")


def generate_video(news_items: list, audio_files: list, open_file: Path, close_file: Path):
    """用 ffmpeg 为每张插图配对对应音频片段，逐段合成后拼接为最终 MP4"""
    if _should_skip(VIDEO_FILE, "步骤 4/4：合成视频"):
        return

    print("\n🎬 步骤 4/4：合成视频...")

    n = len(news_items)
    panel_files = [OUTPUT_DIR / f"{PREFIX}_panel_{i}.jpg" for i in range(n)]
    segment_paths = []
    tmp_files = []

    for i in range(n):
        panel = panel_files[i] if i < len(panel_files) and panel_files[i].exists() else None
        audio = audio_files[i] if i < len(audio_files) and audio_files[i].exists() else None
        if panel is None or audio is None:
            print(f"⚠️  第 {i+1} 段缺少素材（panel={panel}, audio={audio}），跳过")
            continue

        # 第一段前置开场，最后一段追加结尾
        if i == 0 and open_file.exists():
            combined = OUTPUT_DIR / f"{PREFIX}_tmp_audio_0.mp3"
            _concat_audio_files([open_file, audio], combined)
            audio_for_seg = combined
            tmp_files.append(combined)
        elif i == n - 1 and close_file.exists():
            combined = OUTPUT_DIR / f"{PREFIX}_tmp_audio_last.mp3"
            _concat_audio_files([audio, close_file], combined)
            audio_for_seg = combined
            tmp_files.append(combined)
        else:
            audio_for_seg = audio

        seg_path = OUTPUT_DIR / f"{PREFIX}_seg_{i}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(panel),
            "-i", str(audio_for_seg),
            "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
                   f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-tune", "stillimage",
            "-c:a", "aac", "-b:a", VIDEO_AUDIO_BITRATE,
            "-pix_fmt", "yuv420p", "-shortest",
            str(seg_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"❌ 第 {i+1} 段视频失败：{result.stderr[-300:]}")
            sys.exit(1)
        segment_paths.append(seg_path)
        print(f"   第 {i+1}/{n} 段完成")

    if not segment_paths:
        print("❌ 无视频片段可拼接")
        sys.exit(1)

    # 拼接所有片段
    concat_list = OUTPUT_DIR / f"{PREFIX}_concat.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in segment_paths))
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(VIDEO_FILE),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    # 清理临时文件
    for p in segment_paths + tmp_files:
        try: p.unlink()
        except: pass
    try: concat_list.unlink()
    except: pass

    if result.returncode != 0:
        print(f"❌ 视频拼接失败：{result.stderr[-300:]}")
        sys.exit(1)

    # 混入背景音乐
    _mix_bgm(VIDEO_FILE)

    # 打印最终统计
    duration = _get_duration(VIDEO_FILE)
    size_mb = VIDEO_FILE.stat().st_size / 1024 / 1024
    print(f"✅ 视频已生成：{VIDEO_FILE}")
    if duration > 0:
        print(f"   时长：{duration:.1f} 秒 | 大小：{size_mb:.1f} MB")


# ─── 步骤 5：生成 HTML 报道 ──────────────────────────────
def generate_html(news_items: list, youtube_id: str = ""):
    """生成单页 HTML 新闻报道（左图右文 + 顶部 YouTube 播放器）"""
    if not FORCE and HTML_FILE.exists() and HTML_FILE.stat().st_size > 0:
        print(f"⏭️  跳过 HTML 生成（已存在：{HTML_FILE}）")
        return

    print("\n📄 生成 HTML 新闻报道...")

    date_str = datetime.now().strftime("%Y年%m月%d日")
    session = _get_session()

    # YouTube 区块：缩略图 + 点击跳转（避免 file:// 协议下 iframe Error 153）
    if youtube_id:
        yt_url = f"https://youtu.be/{youtube_id}"
        thumb_url = f"https://img.youtube.com/vi/{youtube_id}/maxresdefault.jpg"
        video_section = f"""
<section class="video-wrap">
  <a class="yt-thumb" href="{yt_url}" target="_blank" rel="noopener"
     title="在 YouTube 观看：{date_str} {session}">
    <img src="{thumb_url}" alt="YouTube 封面">
    <span class="play-btn">▶</span>
  </a>
</section>"""
    else:
        video_section = ""

    # 新闻条目（左图右文）
    items_html = ""
    for i, item in enumerate(news_items):
        panel_name = f"{PREFIX}_panel_{i}.jpg"
        panel_path = OUTPUT_DIR / panel_name
        content = item.get("content", "")
        source = item.get("source", "")
        img_block = (
            f'<div class="img-col"><img src="{panel_name}" alt="插图 {i+1}"></div>'
            if panel_path.exists() else ""
        )
        source_tag = f'<p class="source">{source}</p>' if source else ""
        items_html += f"""
  <article>
    {img_block}
    <div class="body">
      <p>{content}</p>
      {source_tag}
    </div>
  </article>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{date_str} {session}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f2f2f2; color: #222; }}
  header {{ background: #111; color: #fff; padding: 1.6rem 1rem; text-align: center; }}
  header h1 {{ font-size: 1.5rem; font-weight: 600; letter-spacing: .04em; }}
  header time {{ display: block; color: #aaa; font-size: .85rem; margin-top: .35rem; }}
  .video-wrap {{ max-width: 860px; margin: 1.4rem auto; padding: 0 1rem; }}
  .yt-thumb {{ display: block; position: relative; border-radius: 10px; overflow: hidden;
               box-shadow: 0 2px 12px rgba(0,0,0,.2); text-decoration: none; }}
  .yt-thumb img {{ width: 100%; display: block; aspect-ratio: 16/9; object-fit: cover; }}
  .yt-thumb:hover img {{ filter: brightness(.85); transition: filter .2s; }}
  .play-btn {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
               width: 72px; height: 72px; background: rgba(255,0,0,.88); border-radius: 50%;
               display: flex; align-items: center; justify-content: center;
               font-size: 28px; color: #fff; pointer-events: none; }}
  main {{ max-width: 860px; margin: 0 auto 2rem; padding: 0 1rem; }}
  article {{ display: flex; gap: 1rem; align-items: flex-start;
             background: #fff; border-radius: 10px; margin-bottom: 1.2rem;
             overflow: hidden; box-shadow: 0 1px 5px rgba(0,0,0,.08); }}
  .img-col {{ flex: 0 0 38%; }}
  .img-col img {{ width: 100%; height: 100%; object-fit: cover; display: block;
                  min-height: 200px; max-height: 380px; }}
  .body {{ flex: 1; padding: 1.1rem 1.2rem 1.1rem .2rem; }}
  .body p {{ line-height: 1.8; font-size: .94rem; }}
  .source {{ color: #888; font-size: .76rem; margin-top: .8rem;
             padding-top: .7rem; border-top: 1px solid #eee; word-break: break-all; }}
  footer {{ text-align: center; padding: 1.4rem; color: #bbb; font-size: .78rem; }}
  @media (max-width: 540px) {{
    article {{ flex-direction: column; }}
    .img-col {{ flex: none; width: 100%; }}
    .img-col img {{ max-height: 240px; }}
    .body {{ padding: .9rem 1rem; }}
  }}
</style>
</head>
<body>
<header>
  <h1>📰 新闻简报</h1>
  <time>{date_str} · {session}</time>
</header>
{video_section}
<main>
{items_html}
</main>
<footer>Brief News · {date_str}</footer>
</body>
</html>"""

    HTML_FILE.write_text(html, encoding="utf-8")
    print(f"✅ HTML 报道已生成：{HTML_FILE}")


# ─── 主流程 ────────────────────────────────────────────
def main():
    global FORCE

    parser = argparse.ArgumentParser(description="新闻简报视频生成器")
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="强制重新生成所有步骤，覆盖已有文件"
    )
    parser.add_argument(
        "--upload", "-u", action="store_true",
        help="生成完成后自动上传至 YouTube"
    )
    args = parser.parse_args()
    FORCE = args.force

    print("=" * 50)
    print("  📺 新闻简报视频生成器 MVP")
    print(f"  🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  📁 输出前缀：{PREFIX}")
    if FORCE:
        print("  🔄 强制模式：覆盖已有文件")
    print("=" * 50)

    # 创建输出目录
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 执行流程（断点续跑）
    news_list, news_text = fetch_news()
    generate_image(news_list)
    audio_files, open_file, close_file = generate_audio(news_list)
    generate_video(news_list, audio_files, open_file, close_file)

    # 自动上传（受配置控制），上传后再生成 HTML 以嵌入视频链接
    youtube_id = ""
    if args.upload:
        from youtube_uploader import upload as yt_upload
        youtube_id = yt_upload(VIDEO_FILE, NEWS_FILE) or ""
    else:
        print("\n⏭️  未指定 --upload 参数，本次运行不执行自动上传。")

    generate_html(news_list, youtube_id=youtube_id)

    print("\n" + "=" * 50)
    print("  🎉 全部完成！")
    print(f"  📄 新闻文稿：{NEWS_FILE}")
    print(f"  🖼️  新闻插图：output/{PREFIX}_panel_0~{len(news_list)-1}.jpg")
    print(f"  🌐 HTML 报道：{HTML_FILE}")
    print(f"  🎬 最终视频：{VIDEO_FILE}")
    print("=" * 50)


if __name__ == "__main__":
    main()
