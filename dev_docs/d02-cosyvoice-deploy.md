# D02 — CosyVoice 本地部署指南

> 模型：Fun-CosyVoice3-0.5B-2512  
> 部署位置：`/opt/CosyVoice`  
> 运行方式：通过 `conda run` 本地推理（由 `s3-generate-voice.py` 调用）

---

## 一、系统要求

| 项目 | 最低要求 | 当前机器 |
|------|----------|----------|
| GPU | NVIDIA GPU, ≥6GB VRAM | RTX 4060 8GB ✅ |
| Python | 3.10 （conda 环境） | 3.10 via conda ✅ |
| CUDA | ≥11.8 | 已安装 ✅ |
| sox | 系统级安装 | `apt install sox libsox-dev` ✅ |
| 磁盘 | ~2GB（模型） + ~500MB（代码） | ✅ |

---

## 二、部署步骤

### 步骤 1：创建目录并 Clone 仓库

```bash
# 创建目录（需 sudo）
sudo mkdir -p /opt/CosyVoice
sudo chown $(whoami):$(whoami) /opt/CosyVoice

# Clone（含子模块）
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /opt/CosyVoice

# 如果子模块失败，手动重试
cd /opt/CosyVoice && git submodule update --init --recursive
```

✅ **已完成**

### 步骤 2：创建 conda 环境并安装依赖

> [!WARNING]
> `conda activate` 在 `&&` 链式命令中**不会生效**，必须分步执行或使用 `conda run`。

```bash
# 创建环境
conda create -n cosyvoice -y python=3.10

# 方式 A：交互式（推荐）
conda activate cosyvoice
cd /opt/CosyVoice
pip install -r requirements.txt

# 方式 B：非交互式
conda run -n cosyvoice --no-banner pip install -r /opt/CosyVoice/requirements.txt
```

> [!IMPORTANT]
> 如果之前 pip install 时报错 `ModuleNotFoundError: No module named 'pkg_resources'`，
> 说明用的是 base 环境（Python 3.12），需要在 cosyvoice 环境中重新安装：
> ```bash
> conda activate cosyvoice
> pip install setuptools   # 补装 pkg_resources
> cd /opt/CosyVoice && pip install -r requirements.txt
> ```

### 步骤 3：安装系统依赖

```bash
sudo apt-get install sox libsox-dev
```

✅ **已完成**

### 步骤 4：下载模型

```bash
conda activate cosyvoice

# 方式 A：通过 HuggingFace（海外推荐）
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512',
                  local_dir='/opt/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B')
"

# 方式 B：通过 ModelScope（国内推荐）
python3 -c "
from modelscope import snapshot_download
snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512',
                  local_dir='/opt/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B')
"
```

> 模型大小约 1-2GB，下载时间取决于网络。

### 步骤 5：创建软链接

```bash
cd /home/xs/projects/health_blog
ln -s /opt/CosyVoice/pretrained_models ./models
```

### 步骤 6：验证安装

```bash
conda activate cosyvoice
cd /opt/CosyVoice

python3 -c "
from cosyvoice.cli.cosyvoice import CosyVoice2
model = CosyVoice2('pretrained_models/Fun-CosyVoice3-0.5B',
                    load_jit=False, load_trt=False)
print('✅ CosyVoice 加载成功')
print(f'   采样率: {model.sample_rate}')
"
```

---

## 三、项目集成

`s3-generate-voice.py` 通过以下方式调用 CosyVoice：

```
conda run -n cosyvoice --no-banner python3 <临时推理脚本>
```

- 临时脚本在每段推理前自动生成，推理后自动删除
- 支持 `instruct` 模式，可传入情绪提示词
- 每段独立合成 wav，最后由 ffmpeg 拼接

### 相关配置（config/config.yaml）

```yaml
tts:
  engine: "cosyvoice-grpc"
  model: "Fun-CosyVoice3-0.5B-2512"
  voice: "male"
  speed: 1.0
  sample_rate: 22050

cosyvoice_server:
  install_dir: "/opt/CosyVoice"
  model_symlink: "./models"
  conda_env: "cosyvoice"
```

---

## 四、常见问题

### Q1: pip install 报 `pkg_resources` 错误
**原因**：使用了 Python 3.12（base env），该版本移除了 `pkg_resources`  
**解决**：确认在 cosyvoice 环境（Python 3.10）中运行

### Q2: 模型加载时 CUDA OOM
**原因**：RTX 4060 8GB 可能在其他程序占用 VRAM 时不够  
**解决**：关闭其他 GPU 程序，或使用 `CUDA_VISIBLE_DEVICES=0` 指定设备

### Q3: `sox` 相关报错
**解决**：`sudo apt-get install sox libsox-dev`

### Q4: 子模块 clone 失败
**解决**：
```bash
cd /opt/CosyVoice
git submodule update --init --recursive
```

---

## 五、部署状态清单

- [x] Clone CosyVoice 到 `/opt/CosyVoice`
- [ ] conda 环境 `cosyvoice` 依赖安装成功（需重新执行步骤 2）
- [ ] 模型下载到 `pretrained_models/Fun-CosyVoice3-0.5B`
- [ ] 软链接 `./models` → `/opt/CosyVoice/pretrained_models`
- [ ] 验证测试通过
