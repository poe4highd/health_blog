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
    dummy_wav_path = output_path.parent / "dummy.wav"
    
    inference_script = f"""
import sys
import os
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

# ─── Monkey Patch 3 & 4: 解决 CosyVoice3 协议兼容与幻觉复读问题 ──────
from cosyvoice.cli.frontend import CosyVoiceFrontEnd
original_frontend_zero_shot = CosyVoiceFrontEnd.frontend_zero_shot

def patched_frontend_zero_shot(self, tts_text, prompt_text, *args, **kwargs):
    model_input = original_frontend_zero_shot(self, tts_text, prompt_text, *args, **kwargs)
    # CosyVoice3 (Qwen) 协议要求：
    # 1. prompt_text 结尾必须有 151646 分隔符
    # 2. text 头部必须有 151646 分隔符
    
    # 修改 prompt_text (指令部分)
    if 'prompt_text' in model_input:
        prompt_list = model_input['prompt_text'].tolist()[0]
        if 151646 not in prompt_list:
            sep = torch.tensor([[151646]], dtype=model_input['prompt_text'].dtype).to(model_input['prompt_text'].device)
            model_input['prompt_text'] = torch.cat([model_input['prompt_text'], sep], dim=1)
            model_input['prompt_text_len'] = model_input['prompt_text_len'] + 1
        
    # 修改 text (正文部分)
    if 'text' in model_input:
        text_list = model_input['text'].tolist()[0]
        if 151646 not in text_list:
            sep = torch.tensor([[151646]], dtype=model_input['text'].dtype).to(model_input['text'].device)
            model_input['text'] = torch.cat([sep, model_input['text']], dim=1)
            model_input['text_len'] = model_input['text_len'] + 1
    
    return model_input

CosyVoiceFrontEnd.frontend_zero_shot = patched_frontend_zero_shot
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────

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

text = '''{text.replace("'", "\\'")}'''
instruct = '''{instruct_text.replace("'", "\\'")}'''

# 锁定音色素材与其配套文本 (解决幻觉复读的关键)
project_root = '{PROJECT_ROOT}'
official_wav = os.path.join(project_root, 'data-output/male_ref.wav')
official_text = "And then later on, fully acquiring that company. So keeping management in line, interest in line with the asset that's coming into the family is a reason why sometimes we don't buy the whole thing."

# 构造复合 Prompt (System Prompt + style + Ref Text)
full_prompt = f"You are a helpful assistant. {{instruct}}<|endofprompt|>{{official_text}}"

output_list = []
# 使用更稳定的 inference_zero_shot 接口实现 CosyVoice3 推理
try:
    generator = model.inference_zero_shot(text, full_prompt, official_wav, stream=False, speed={speed})
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


def concat_wav_files(wav_files: list, output_path: Path, logger: logging.Logger):
    """使用 ffmpeg 拼接多个 wav 文件为一个"""
    if len(wav_files) == 1:
        # 只有一个文件，直接复制
        import shutil
        shutil.copy2(wav_files[0], output_path)
        return

    # 创建 ffmpeg concat 列表
    list_path = output_path.parent / "_concat_list.txt"
    with open(list_path, "w") as f:
        for wav in wav_files:
            f.write(f"file '{wav}'\n")

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_path), "-c", "copy", str(output_path)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            logger.info(f"  ✅ 音频拼接完成: {output_path.name}")
        else:
            logger.error(f"  ❌ 拼接失败: {result.stderr[:200]}")
    finally:
        if list_path.exists():
            list_path.unlink()


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
    wav_files = []
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
            wav_files.append(wav_path)
            success_count += 1
            continue

        logger.info(f"  [{seg_id}/{len(segments)}] {emotion} — {text[:30]}...")

        if generate_voice_local(text, wav_path, cfg, emotion, logger):
            wav_files.append(wav_path)
            success_count += 1
        else:
            fail_count += 1

    # 拼接所有音频，只有当全段落跑完或者没有指定 segment 时才进行汇总拼接
    if wav_files and args.segment is None:
        merged_path = output_dir / f"{article_id}-voice.wav"
        logger.info(f"拼接 {len(wav_files)} 段音频...")
        concat_wav_files([str(f) for f in wav_files], merged_path, logger)

    logger.info(f"{'='*50}")
    logger.info(f"✅ 语音生成完成: 成功 {success_count}, 失败 {fail_count}")
    logger.info(f"{'='*50}")

    print(f"\n✅ 完成！成功 {success_count}/{len(segments)} 段音频")


if __name__ == "__main__":
    main()
