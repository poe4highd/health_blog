# 健康真相实验室 — 文章转视频生成管线

将纯文本文章自动生成配图视频，完整流程：分段 → 配图 → 语音 → 合成。

---

## 环境准备

```bash
# 1. 安装 Python 依赖
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 GEMINI_API_KEY

# 3. 启动 CosyVoice 本地服务（语音生成依赖）
# 参考 config/config.yaml 中的 cosyvoice_server 配置
```

---

## 使用方法

### 一键运行（推荐）

```bash
python scripts/run.py data-input/a0001.txt
```

支持跳过已完成的步骤：

```bash
python scripts/run.py data-input/a0001.txt --skip s1 s2
```

---

### 分步运行

#### 步骤 0：生成频道片头/片尾音频（一次性）

```bash
# 生成两个文件（已存在则跳过）
python scripts/s0-intro-outro-voice.py

# 强制覆盖已有文件
python scripts/s0-intro-outro-voice.py --force

# 只生成片头
python scripts/s0-intro-outro-voice.py --intro

# 只生成片尾
python scripts/s0-intro-outro-voice.py --outro
```

输出：
- `data-input/Channel Intro Voice.wav`
- `data-input/Channel Outro Voice.wav`

文案和情绪在 `config/config.yaml` 的 `channel` 区块中配置。

---

#### 步骤 1：文章分段 + 提示词生成

```bash
python scripts/s1-generate-prompts.py data-input/a0001.txt
```

输出：`data-output/a0001/a0001-prompts.json`

---

#### 步骤 2：图片生成

```bash
python scripts/s2-generate-image.py data-input/a0001.txt
```

输出：`data-output/a0001/a0001-{1..N}.png`

---

#### 步骤 3：语音播报生成

```bash
python scripts/s3-generate-voice.py data-input/a0001.txt

# 只生成指定段落
python scripts/s3-generate-voice.py data-input/a0001.txt -s 3

# 覆盖语速
python scripts/s3-generate-voice.py data-input/a0001.txt --speed 1.0
```

输出：`data-output/a0001/a0001-voice-{1..N}.wav`

---

#### 步骤 4：视频合成

```bash
# 默认：正文视频首尾自动拼接片头/片尾
python scripts/s4-generate-video.py data-input/a0001.txt

# 跳过片头/片尾，只输出正文视频
python scripts/s4-generate-video.py data-input/a0001.txt --no-bumpers
```

片头/片尾文件路径在 `config/config.yaml` 的 `channel.intro_video` / `channel.outro_video` 中配置。

输出：`data-output/a0001/a0001.mp4`

---

## 配置文件

`config/config.yaml` 控制所有参数：

| 区块 | 说明 |
|------|------|
| `paths` | 输入/输出目录 |
| `segmentation` | 分段数量、Claude 超时 |
| `image` | Gemini 模型、画面比例、风格提示词 |
| `tts` | CosyVoice 模型、语速、风格后缀 |
| `video` | 分辨率、帧率、转场时长 |
| `cosyvoice_server` | CosyVoice 安装路径、conda 环境 |
| `channel` | 片头/片尾文案、情绪、音频和视频文件路径配置 |

---

## 目录结构

```
health_blog/
├── config/
│   └── config.yaml          # 全局配置
├── data-input/
│   ├── a0001.txt            # 文章原文
│   ├── Channel Intro Voice.wav  # 片头语音（s0 生成）
│   ├── Channel Outro Voice.wav  # 片尾语音（s0 生成）
│   ├── Health Channel Intro Music Logo Voice.mp4  # 片头视频
│   └── Health Channel Outro Music Logo Voice.mp4  # 片尾视频
├── data-output/
│   ├── male_ref.wav         # 参考音频（CosyVoice 音色克隆）
│   └── a0001/               # 单篇输出目录
│       ├── a0001-prompts.json
│       ├── a0001-{N}.png
│       ├── a0001-voice-{N}.wav
│       └── a0001.mp4
├── scripts/
│   ├── run.py               # 总控脚本
│   ├── s0-intro-outro-voice.py
│   ├── s1-generate-prompts.py
│   ├── s2-generate-image.py
│   ├── s3-generate-voice.py
│   └── s4-generate-video.py
└── requirements.txt
```
