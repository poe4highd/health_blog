# YouTube API 配置与自动发布指南

本项目支持通过 `youtube_uploader.py` 将生成的每日简报自动上传到 YouTube 频道，并在 `config/config.yaml` 中自定义标题、简介、标签及可见性。

## 前置准备：获取 OAuth 端点密钥 (Client Secrets)

由于视频上传属于敏感操作，需要获取 OAuth 2.0 客户端密钥授权：

1. 打开 [Google Cloud Console](https://console.cloud.google.com/) 并选择与 Gemini API 相同的项目。
2. 搜索并 **启用 "YouTube Data API v3"**。
3. 进入 **API 和服务 -> OAuth 同意屏幕 (OAuth consent screen)**：
   - 选择 **外部 (External)**，然后创建。
   - 填写必填项（应用名称如 "Brief News Auto", 用户支持电子邮件填你自己的）。
   - 在 **授权域 (Authorized domains)** 不用填。最后保存。
   - **测试用户 (Test users)** 中，点击"添加用户"，将你**需要上传视频的目标 YouTube 频道的 Google 邮箱**加进去。
4. 进入 **凭据 (Credentials)** 页面：
   - 点击 **创建凭据 -> OAuth 客户端 ID**。
   - 应用类型选择 **桌面应用 (Desktop app)**，命名随意。
5. 创建成功后，点击列表中刚才创建的 ID 右侧的 **"下载" 按钮** (图标为向下箭头，下载为 JSON 格式)。
6. 在本项目中创建 `config` 文件夹：
   ```bash
   mkdir -p config
   ```
7. 将下载的 JSON 文件放入 `config` 文件夹，并改名为 `youtube_credentials.json`！

## 配置文件说明

打开 `config/config.yaml` 找到 `youtube` 相关部分：

```yaml
youtube:
  enabled: true   # 将 false 改为 true 启用自动上传
  channel_credentials: "config/youtube_credentials.json"
  privacy: "private"  # 首发测试建议为 private（仅自己可见），稳定后改为 public
```

## 首次执行授权

因为是 OAuth，**第一次执行时必须人工参与浏览器授权**：
你可以不跑全流程，直接拿已经生成的产物去单独测试上传功能完成授权。

打开终端，执行以下命令：
```bash
source venv/bin/activate
python youtube_uploader.py output/News_20260316_21.mp4 output/News_20260316_21_sources.json
```
*(注意替换成你 `output/` 文件夹下实际存在的文件名)*

在运行到最后一步时，程序会在屏幕上**打印一个验证 URL 或自动打开你的本地浏览器**。
请用你在前面设置为"测试用户"的 Google 账号登录并同意授权。

**💡 如果你是在云服务器等没有界面的环境：**
你会在自己的电脑点开链接授权，然后 Google 会跳转到一个 `http://localhost:xxxx/...` 的报错页（找不到网页）。这是正常的。
你只需要把那个报错页面的 **完整网址(URL)** 复制出来，到你的服务器上另开一个终端窗口执行（注意双引号）：
```bash
curl "你复制的完整网址"
```
这样服务器上的脚本就会接收到授权 Code 继续上传。

授权完成后，程序会自动在 `config/token.json` 生成离线访问的 Token。之后程序只要读到 `token.json`，即使是之后跑 `main.py` 还是定时任务，都可无需人工干预、完全静默自动首发了。

## 定时任务控制台 (Crontab)

自动上传配置跑通后，你可以通过 `setup_cron.sh` 写入系统定时：

```bash
./setup_cron.sh
```

这会自动根据 `config/config.yaml` 里的 `schedule` 配置每天按要求执行全流程。日志会在 `output/cron.log` 中。
