#!/usr/bin/env python3
"""
s0-intro-outro-voice.py — 频道片头/片尾语音生成

从 config.yaml 的 channel 区块读取文案和情绪，调用 CosyVoice 本地推理，
生成片头和片尾音频文件。

输出：
  data-input/Channel Intro Voice.wav
  data-input/Channel Outro Voice.wav

用法:
  python scripts/s0-intro-outro-voice.py           # 生成两个文件（已存在则跳过）
  python scripts/s0-intro-outro-voice.py --force   # 强制覆盖已有文件
  python scripts/s0-intro-outro-voice.py --intro   # 只生成片头
  python scripts/s0-intro-outro-voice.py --outro   # 只生成片尾
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import yaml


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


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("s0-intro-outro-voice")
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


def generate_voice_local(text: str, output_path: Path, cfg: dict,
                         emotion: str, logger: logging.Logger) -> bool:
    """通过 subprocess 调用 CosyVoice conda 环境生成单段音频"""
    tts_cfg = cfg.get("tts", {})
    cosyvoice_cfg = cfg.get("cosyvoice_server", {})
    install_dir = cosyvoice_cfg.get("install_dir", "/opt/CosyVoice")
    conda_env = cosyvoice_cfg.get("conda_env", "cosyvoice")

    model_name = tts_cfg.get("model", "Fun-CosyVoice3-0.5B")
    speed = tts_cfg.get("speed", 1.0)
    gain = tts_cfg.get("gain", 1.5)
    style_suffix = tts_cfg.get("instruct_suffix", "展现出专业且严谨的科学素养风格。")

    if emotion:
        instruct_text = f"用流利的中文播报以下文本，语气{emotion}，{style_suffix}"
    else:
        instruct_text = f"用流利的中文播报以下文本，语气专业，{style_suffix}"

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
        data = data.astype(np.float32) / (2**(8 * params.sampwidth - 1))
        tensor = torch.from_numpy(data.copy()).reshape(params.nchannels, -1)
        return tensor, params.framerate

torchaudio.load = mock_torchaudio_load

def mock_torchaudio_save(filepath, tensor, sample_rate, **kwargs):
    data = tensor.detach().cpu().numpy()
    if data.ndim == 1:
        data = data.reshape(1, -1)
    # 峰值增益归一化（gain 由 config.yaml tts.gain 控制）
    max_val = np.abs(data).max()
    if max_val > 1e-6:
        data = data / max_val * {gain}
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

project_root = '{PROJECT_ROOT}'
prompt_wav = os.path.join(project_root, 'data-output/male_ref.wav')

instruct_prompt = f"You are a helpful assistant. {{instruct}}<|endofprompt|>"

output_list = []
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

    tmp_script = output_path.parent / f"_tmp_tts_{output_path.stem}.py"
    tmp_script.write_text(inference_script, encoding="utf-8")

    run_env = os.environ.copy()
    venv_path = run_env.pop("VIRTUAL_ENV", None)
    if venv_path:
        venv_bin = os.path.join(venv_path, "bin")
        paths = run_env.get("PATH", "").split(os.pathsep)
        run_env["PATH"] = os.pathsep.join([p for p in paths if p != venv_bin])

    try:
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
            logger.error("  ❌ CosyVoice 推理失败")
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


def main():
    parser = argparse.ArgumentParser(description="频道片头/片尾语音生成")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有文件")
    parser.add_argument("--intro", action="store_true", help="只生成片头")
    parser.add_argument("--outro", action="store_true", help="只生成片尾")
    args = parser.parse_args()

    # 默认两个都生成
    do_intro = args.intro or (not args.intro and not args.outro)
    do_outro = args.outro or (not args.intro and not args.outro)

    load_dotenv()
    cfg = load_config()
    logger = setup_logger()

    channel_cfg = cfg.get("channel", {})
    if not channel_cfg:
        logger.error("❌ config.yaml 中缺少 channel 配置区块")
        sys.exit(1)

    intro_text = channel_cfg.get("intro_text", "")
    outro_text = channel_cfg.get("outro_text", "")
    intro_emotion = channel_cfg.get("intro_emotion", "热情、诚挚")
    outro_emotion = channel_cfg.get("outro_emotion", "温暖、从容")
    intro_output = channel_cfg.get("intro_output", "data-input/Channel Intro Voice.wav")
    outro_output = channel_cfg.get("outro_output", "data-input/Channel Outro Voice.wav")

    intro_path = PROJECT_ROOT / intro_output
    outro_path = PROJECT_ROOT / outro_output

    # 确保输出目录存在
    intro_path.parent.mkdir(parents=True, exist_ok=True)
    outro_path.parent.mkdir(parents=True, exist_ok=True)

    tasks = []
    if do_intro:
        tasks.append(("片头", intro_text, intro_path, intro_emotion))
    if do_outro:
        tasks.append(("片尾", outro_text, outro_path, outro_emotion))

    success_count = 0
    for label, text, output_path, emotion in tasks:
        logger.info(f"{'='*50}")
        logger.info(f"生成{label}音频")
        logger.info(f"  文本：{text}")
        logger.info(f"  情绪：{emotion}")
        logger.info(f"  输出：{output_path.relative_to(PROJECT_ROOT)}")

        if not text:
            logger.error(f"  ❌ {label}文本为空，跳过")
            continue

        if output_path.exists() and output_path.stat().st_size > 0 and not args.force:
            logger.info(f"  ⏭️ 文件已存在，跳过（使用 --force 可覆盖）")
            success_count += 1
            continue

        if generate_voice_local(text, output_path, cfg, emotion, logger):
            logger.info(f"  ✅ 已保存：{output_path.name}")
            success_count += 1
        else:
            logger.error(f"  ❌ {label}音频生成失败")

    logger.info(f"{'='*50}")
    logger.info(f"完成：{success_count}/{len(tasks)} 个文件生成成功")

    if success_count < len(tasks):
        sys.exit(1)


if __name__ == "__main__":
    main()
