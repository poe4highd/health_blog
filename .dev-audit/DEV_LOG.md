# 开发日志 — 2026-04-12 (22:45)

## 任务：CosyVoice3 推理管线深度优化与 Bug 修复
### 需求与背景
- 用户反馈语速不均、音量过小、音色男女切换、以及中间出现“胡言乱语”的幻觉（约10个字）。
- 需要支持专业、响应感强的男声科学素养风格。

### 已完成
#### P1：核心协议对齐 (Monkey Patch 4) ✅
- **拦截器**：拦截 `frontend_zero_shot`，在 `prompt_text` 末尾和 `text` 开头双向注入 Token `151646`。
- **作用**：完全切断了句子间隙复读提示词的问题。

#### P2：音色锁定与质量优化 ✅
- **音色锁定**：转码官方男声素材 `cross_lingual_prompt.wav` 为 PCM16 `male_ref.wav`。
- **协议闭环**：在推理时同步传入该音频对应的正文文本。**这是消除幻觉（胡言乱语）的关键**。
- **API 切换**：基于 V3 官方用例，将推理核心改用 `inference_zero_shot`。

#### P3：音响效果增强 ✅
- **动态响度**：修改音频归一化补丁，增益倍数提升至 1.5 倍并配合 Clipping，达到感官音量加倍。
- **语速调节**：回归至平稳的 1.2x 速。

### 经验与坑点
1. **零样本模式必须对齐文本**：在 CosyVoice3 中，仅提供 `prompt_wav` 而不提供对应的 `prompt_text` 必然导致模型产生逻辑幻觉（Hallucination）。
2. **Token 硬编码要求**：底层 Qwen 必须检测到 `151646` 才能正确划分推理边界。
3. **音频格式兼容性**：官方部分 Asset 采用 IEEE Float 编码，Python 标准库 `wave` 不支持，需预先用 `ffmpeg` 转码。

---

# 开发日志 — 2026-04-12 (早期记录)

## 任务：文章插图视频生成管线（新方向重构）

### 需求
- 按 idea.txt 指导，从新闻简报视频生成器重构为文章插图视频生成管线
- 4步脚本：分段提示词 → 图片生成 → 语音播报 → 视频合成
- 用 a0001.txt 测试第一个文件

### 已完成

#### P0：归档旧文件 + 创建目录结构
- 归档 main.py、youtube_credentials.json、token.json、youtube_api_setup.md、vertex_ai_setup.md 到 `_archive/`
- 创建 scripts/ 目录

#### P1：创建 venv + requirements.txt + config.yaml
- 重建 venv（Python 3.12.2）
- 依赖：google-genai, Pillow, PyYAML, python-dotenv, grpcio, protobuf
- 重写 config.yaml 和 config.example.yaml
- 更新 .env.example

#### P2：s1-generate-prompts.py ✅
- 调用 claude CLI 分段 + 生成提示词
- **关键决策**：使用 text_start/text_end 标识代替完整原文，避免 JSON 引号转义问题
- **坑点**：Claude 生成的 JSON 中中文文本包含未转义双引号，`--output-format json` 还会导致双重编码。最终去掉该参数改用纯文本输出 + JSON 修复函数
- 测试结果：10 个段落成功生成

#### P3：s2-generate-image.py ✅
- 调用 Gemini API 生成 16:9 配图
- **坑点**：`generate_images` API 对 gemini-3.1-flash-image-preview 返回 404，自动降级到 `generate_content` 备选方案成功
- 测试结果：10/10 张图片全部生成（每张约15秒）

#### P4：s3-generate-voice.py（脚本已写完，等待 CosyVoice 部署）
- 通过 `conda run -n cosyvoice` 调用 CosyVoice 本地推理
- 按 segments 分段合成 → ffmpeg 拼接为完整音频
- 生成 `a0001-voice.txt`（带情绪标注的播报文本）
- **阻塞项**：需用户手动 `sudo` 部署 CosyVoice 到 `/opt/CosyVoice`

#### P5：s4-generate-video.py ✅
- ffmpeg 将图片按音频时长转为视频片段，拼接后合并音频
- 无音频时默认每张 5 秒
- 测试结果：50 秒静音视频 3.9MB 生成成功

#### P6：run.py 总控 ✅
- 支持 `--skip s1 s2`（跳过已完成步骤）和 `--emotion` 参数
- 每步执行后检查日志中的 `[ERROR]`，有错误则中止

### 进行中
- P4：CosyVoice 部署 + s3 测试（需用户手动 sudo）

### 修复任务：支持 CosyVoice3 推理（2026-04-12）
#### 需求与记录：
- 之前由于强行调用 `CosyVoice2` 导致的 `AssertionError` 与传参 `TypeError` 已通过动态加载模型并兼容参数修复完成。
- **坑点补充**：在加载 `CosyVoice3` 时，抛出 `ModuleNotFoundError: No module named 'matcha'` 的依赖缺失报错（追踪到 `flow_matching.py`）。该依赖尚未包含在原工程内。

#### 计划：
1. 运行 `conda run -n cosyvoice pip install matcha-tts` 补充环境依赖。
2. 重新使用 `-s 1` 选项测试模块单段生成是否畅通。
- [ ] 正在执行环境修复。

### 经验
1. Claude CLI JSON 输出不可靠：中文文本常包含未转义引号，建议只让 Claude 输出短字段
2. Gemini generate_images API 对部分模型 404，需要 generate_content 做 fallback
3. text_start/text_end 定位法比直接包含原文更可靠，但需处理模糊匹配场景
