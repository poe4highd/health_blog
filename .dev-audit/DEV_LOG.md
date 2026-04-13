# 开发日志 — 2026-04-12 (23:55)

## 任务：补齐 run 与 s1 的文章路径边界校验
### 需求
- 工作区还保留 `scripts/run.py` 与 `scripts/s1-generate-prompts.py` 的未提交改动，内容均为文章输入路径的项目目录边界校验。
- 需要将这两处改动作为独立任务提交，并满足“代码与审计文档同提交”的原子性要求。
- 关键 Error Stack：本次为防御性增强，未提供新的运行时错误栈。

### 计划
1. 为 `scripts/run.py` 增加 `resolve()` 与 `relative_to(PROJECT_ROOT)` 检查，拒绝项目目录外的文章路径。
2. 为 `scripts/s1-generate-prompts.py` 增加同样的路径边界校验，和其他步骤保持一致。
3. 通过 `--help` 轻量验证两个脚本仍能正常启动。
4. 更新项目历史并单独提交、推送这两处剩余改动。

### 回顾
- 已在 `scripts/run.py` 中补齐文章路径 `resolve()` 和项目目录边界校验，越界时会明确报错退出。
- 已在 `scripts/s1-generate-prompts.py` 中补齐同样的边界校验，避免和 `s2/s3/s4` 的路径约束不一致。
- 本地已验证 `python3 scripts/run.py --help` 与 `python3 scripts/s1-generate-prompts.py --help` 均可正常执行。

### 经验
1. 多入口脚本的安全约束如果只补一半，最终还是会在最弱的入口处失守。
2. 这类“剩余未提交改动”适合拆成独立小提交，既方便回溯，也避免和主要功能修复混在一起。

# 开发日志 — 2026-04-12 (23:35)

## 任务：移除总音轨依赖，修复视频漏段音频
### 需求
- 用户反馈最终合成视频里缺少第 7 段音频，但对应分段文件实际存在。
- 经排查，`s4-generate-video.log` 已识别第 7 段 `46.2s`，但最终视频总时长与 `a0001-voice.wav` 对不上。
- 关键问题：`s3-generate-voice.py -s 7` 单段重跑后会更新 `a0001-voice-7.wav`，但不会重建总音轨 `a0001-voice.wav`，导致 `s4` 继续使用过期总音轨。

### 计划
1. 移除 `scripts/s3-generate-voice.py` 中生成总音轨 `voice.wav` 的逻辑。
2. 修改 `scripts/s4-generate-video.py`，直接按段落顺序拼接 `voice-*.wav` 作为最终视频音轨来源。
3. 对缺失或损坏的分段音频自动补静音，保证画面时长与音轨对齐。
4. 保持临时拼接音频只存在于 `_tmp_video/`，不再输出正式总音轨文件。
5. 通过实际重新合成验证第 7 段不再丢失，并同步更新项目历史。

### 回顾
- 已删除 `scripts/s3-generate-voice.py` 中的总音轨拼接逻辑；后续 `s3` 只生成分段音频 `voice-*.wav`。
- 已在 `scripts/s4-generate-video.py` 中新增按段落顺序临时拼接分段音频的逻辑，不再依赖输出目录中的 `voice.wav`。
- 已增加缺失段落补静音机制；若分段音频不存在或时长探测失败，会自动补足对应时长，避免最终视频画音错位。
- 临时合成音轨现存放于 `_tmp_video/narration.wav`，在收尾时清理，不再产生新的正式总音轨文件。
- 旧的 `data-output/a0001/a0001-voice.wav` 会保留在磁盘上，但新流程不会再生成它，也不会再使用它。

### 经验
1. 聚合型中间产物如果不会自动随单段重跑同步刷新，就很容易变成“看起来存在、实际上过期”的隐性状态源。
2. 对视频流水线来说，分段素材是更可靠的单一真相来源；最终合成阶段应尽量直接从分段素材组装，而不是依赖额外缓存文件。

# 开发日志 — 2026-04-12 (23:10)

## 任务：s2 图片生成支持指定段落
### 需求
- 用户要求更新 `scripts/s2-generate-image.py`，增加 `-s SEGMENT, --segment SEGMENT` 选项，只生成指定段落 ID 的图片。
- 参考现有 `scripts/s3-generate-voice.py` 的同类参数行为，保持脚本使用体验一致。
- 关键 Error Stack：本次为功能增强，未提供新的运行时错误栈。

### 计划
1. 为 `scripts/s2-generate-image.py` 增加 `-s/--segment` 命令行参数。
2. 在读取 `prompts.json` 后按 `segments[*].id` 过滤目标段落。
3. 若指定 ID 不存在，则记录错误并退出，避免静默成功。
4. 保持统计、日志和已有“跳过已存在图片”逻辑兼容。
5. 完成后同步更新项目历史，并做一次本地帮助信息验证。

### 回顾
- 已在 `scripts/s2-generate-image.py` 中新增 `-s/--segment` 参数，类型为 `int`，语义与 `s3-generate-voice.py` 对齐。
- 已在读取提示词后增加段落过滤逻辑；当指定段落不存在时，脚本会输出 `未找到段落 ID` 并以非零状态退出。
- 已补充脚本头部用法说明，加入单段执行示例。
- 本地验证已覆盖 `python scripts/s2-generate-image.py --help`，确认新参数已出现在帮助信息中。

### 经验
1. 多步骤流水线脚本的命令行参数最好在各步骤之间保持一致，能明显降低重复记忆成本。
2. 对“筛选后为空”的场景应显式失败，不要输出“成功 0/N”这类容易误导的结果。

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
