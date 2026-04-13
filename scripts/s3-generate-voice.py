#!/usr/bin/env python3
"""
s3-generate-voice.py — 步骤3：语音播报生成

按 segments 分段调用 CosyVoice 本地推理，生成播报音频。
支持两种模式：
  1. gRPC 模式（通过 CosyVoice gRPC 服务）
  2. 本地直接推理模式（通过 subprocess 调用 conda 环境）

用法: python scripts/s3-generate-voice.py data-input/a0001.txt
"""

import argparse
import json
import os
import subprocess
import sys
import logging
import struct
import wave
from pathlib import Path

import yaml


# ─── 常量 ─────────────────────────────────────────────────
SCRIPT_NAME = "s3-generate-voice"
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


def generate_voice_local(text: str, output_path: Path, cfg: dict,
                         emotion: str, logger: logging.Logger) -> bool:
    """通过 subprocess 调用 CosyVoice conda 环境生成单段音频"""
    tts_cfg = cfg.get("tts", {})
    cosyvoice_cfg = cfg.get("cosyvoice_server", {})
    install_dir = cosyvoice_cfg.get("install_dir", "/opt/CosyVoice")
    conda_env = cosyvoice_cfg.get("conda_env", "cosyvoice")
    # 获取配置参数
    model_name = tts_cfg.get("model", "Fun-CosyVoice3-0.5B")
    speed = tts_cfg.get("speed", 2.0)
    style_suffix = tts_cfg.get("instruct_suffix", "展现出专业且严谨的科学素养风格。")

    # 构造 instruct 提示词
    if emotion:
        instruct_text = f"用流利的中文播报以下文本，语气{{emotion}}，{{style_suffix}}"
    else:
        instruct_text = f"用流利的中文播报以下文本，语气专业，{{style_suffix}}"

    # 构造推理脚本
    inference_script = f"""
import sys
import os
import json
import torchaudio
import torch
import wave
import numpy as np

# ─── Monkey Patch: 绕过损坏的 torchaudio 后端 ──────────────
def mock_torchaudio_load(filepath, backend=None, **kwargs):
    with wave.open(str(filepath), 'rb') as wf:
        params = wf.getparams()
        frames = wf.readframes(params.nframes)
        if params.sampwidth == 2:
            dtype = np.int16
        elif params.sampwidth == 4:
            dtype = np.int32
        else:
            raise ValueError(f"Unsupported sample width: {{params.sampwidth}}")

        data = np.frombuffer(frames, dtype=dtype)
        # 归一化
        data = data.astype(np.float32) / (2**(8 * params.sampwidth - 1))
        # 重塑为 [channels, length]
        tensor = torch.from_numpy(data.copy()).reshape(params.nchannels, -1)
        return tensor, params.framerate

torchaudio.load = mock_torchaudio_load

def mock_torchaudio_save(filepath, tensor, sample_rate, **kwargs):
    # 将 tensor 转为 int16 PCM
    data = tensor.detach().cpu().numpy()
    if data.ndim == 1:
        data = data.reshape(1, -1)

    # 自动增益归一化：将响度显著提升（Peak 1.5 + Clip 处理以达到感官加倍）
    max_val = np.abs(data).max()
    if max_val > 1e-6:
        data = data / max_val * 1.5

    # 转为 int16 并执行安全截断
    data = (data * 32767).clip(-32768, 32767).astype(np.int16)

    with wave.open(str(filepath), 'wb') as wf:
        wf.setnchannels(data.shape[0])
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data.tobytes())

torchaudio.save = mock_torchaudio_save

# ─────────────────────────────────────────────────────────


sys.path.insert(0, '{install_dir}')

if 'CosyVoice3' in '{model_name}':
    from cosyvoice.cli.cosyvoice import CosyVoice3 as ModelClass
elif 'CosyVoice2' in '{model_name}':
    from cosyvoice.cli.cosyvoice import CosyVoice2 as ModelClass
else:
    from cosyvoice.cli.cosyvoice import CosyVoice as ModelClass

kwargs = {{'load_trt': False}}
if 'CosyVoice3' not in '{model_name}':
    kwargs['load_jit'] = False

model = ModelClass('{install_dir}/pretrained_models/{model_name}', **kwargs)

text = json.loads({json.dumps(json.dumps(text))})
instruct = json.loads({json.dumps(json.dumps(instruct_text))})

# 参考音频（用于音色克隆）
project_root = '{PROJECT_ROOT}'
prompt_wav = os.path.join(project_root, 'data-output/male_ref.wav')

# instruct2 格式：System Prompt + 风格指令 + <|endofprompt|>（不含参考文本）
instruct_prompt = f"You are a helpful assistant. {{instruct}}<|endofprompt|>"

output_list = []
# 使用 inference_instruct2 接口：风格控制 + 音色克隆
try:
    generator = model.inference_instruct2(text, instruct_prompt, prompt_wav, stream=False, speed={speed})
except Exception as e:
    print(f"ERROR: Inference failed: {{e}}")
    sys.exit(1)

for chunk in generator:
    output_list.append(chunk['tts_speech'])

if output_list:
    speech = torch.cat(output_list, dim=1)
    torchaudio.save('{output_path}', speech, model.sample_rate)
    print(f'OK: saved {{speech.shape[1]}} samples at {{model.sample_rate}}Hz')
else:
    print('ERROR: no audio generated')
    sys.exit(1)
"""

    # 写入临时脚本
    tmp_script = output_path.parent / f"_tmp_tts_{output_path.stem}.py"
    tmp_script.write_text(inference_script, encoding="utf-8")

    # 过滤掉当前 VIRTUAL_ENV 的 PATH 污染，否则 conda run 里的 python3 会被解析为外层 venv 的 python3
    run_env = os.environ.copy()
    venv_path = run_env.pop("VIRTUAL_ENV", None)
    if venv_path:
        venv_bin = os.path.join(venv_path, "bin")
        paths = run_env.get("PATH", "").split(os.pathsep)
        run_env["PATH"] = os.pathsep.join([p for p in paths if p != venv_bin])

    try:
        # 通过 conda run 调用
        result = subprocess.run(
            ["conda", "run", "-n", conda_env,
             "python3", str(tmp_script)],
            capture_output=True, text=True, timeout=300,
            cwd=install_dir,
            env=run_env
        )

        if result.returncode == 0 and "OK:" in result.stdout:
            logger.info(f"  ✅ {result.stdout.strip()}")
            return True
        else:
            logger.error(f"  ❌ CosyVoice 推理失败")
            if result.stdout:
                logger.error(f"  stdout: {result.stdout}")
            if result.stderr:
                logger.error(f"  stderr: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("  ❌ CosyVoice 推理超时（300s）")
        return False
    except FileNotFoundError:
        logger.error("  ❌ 未找到 conda 命令")
        return False
    finally:
        if tmp_script.exists():
            tmp_script.unlink()


def generate_voice_text(segments: list, article_text: str, output_dir: Path,
                        article_id: str, logger: logging.Logger):
    """生成带情绪标注的播报文本文件"""
    voice_text_path = output_dir / f"{article_id}-voice.txt"
    lines = []
    for seg in segments:
        emotion = seg.get("emotion", "")
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"[{emotion}] {text}")
        else:
            lines.append(f"[{emotion}] （段落 {seg['id']} 文本缺失）")

    voice_text_path.write_text("\n\n".join(lines), encoding="utf-8")
    logger.info(f"  播报文本已保存: {voice_text_path.name}")


def main():
    parser = argparse.ArgumentParser(
        description="步骤3：语音播报生成"
    )
    parser.add_argument("article_path", help="文章文件路径")
    parser.add_argument("-s", "--segment", type=int, help="只生成指定段落 ID 的音频")
    parser.add_argument("--speed", type=float, help="强制覆盖语速 (speed)")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config()
    if args.speed is not None:
        if "tts" not in cfg: cfg["tts"] = {}
        cfg["tts"]["speed"] = args.speed
        print(f"DEBUG: 命令行强制覆盖语速为: {args.speed}")

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
    logger.info(f"步骤 3：语音播报生成")
    logger.info(f"文章 ID: {article_id}")
    logger.info(f"{'='*50}")

    # 读取提示词和原文
    data = json.loads(prompts_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    
    if args.segment is not None:
        segments = [seg for seg in segments if seg["id"] == args.segment]
        if not segments:
            logger.error(f"❌ 未找到段落 ID: {args.segment}")
            sys.exit(1)
            
    article_text = article_path.read_text(encoding="utf-8").strip() if article_path.exists() else ""

    logger.info(f"共 {len(segments)} 个段落需要生成音频")

    # 生成播报文本
    generate_voice_text(segments, article_text, output_dir, article_id, logger)

    # 逐段生成音频
    success_count = 0
    fail_count = 0

    for seg in segments:
        seg_id = seg["id"]
        text = seg.get("text", "").strip()
        emotion = seg.get("emotion", "")

        if not text:
            logger.warning(f"  段落 {seg_id} 文本为空，跳过")
            fail_count += 1
            continue

        wav_path = output_dir / f"{article_id}-voice-{seg_id}.wav"

        # 跳过已存在的音频
        if wav_path.exists() and wav_path.stat().st_size > 0:
            logger.info(f"  ⏭️ 跳过段落 {seg_id}（音频已存在）")
            success_count += 1
            continue

        logger.info(f"  [{seg_id}/{len(segments)}] {emotion} — {text[:30]}...")

        if generate_voice_local(text, wav_path, cfg, emotion, logger):
            success_count += 1
        else:
            fail_count += 1

    logger.info(f"{'='*50}")
    logger.info(f"✅ 语音生成完成: 成功 {success_count}, 失败 {fail_count}")
    logger.info(f"{'='*50}")

    print(f"\n✅ 完成！成功 {success_count}/{len(segments)} 段音频")


if __name__ == "__main__":
    main()
