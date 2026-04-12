# Gemini API Key 配置指南

## 问题描述

使用 Gemini API 生成图片时出现 403 错误：

```
PERMISSION_DENIED: Requests from referer <empty> are blocked.
API_KEY_HTTP_REFERRER_BLOCKED
```

这是因为 API Key 设置了 HTTP Referrer 限制，而 Python 脚本（非浏览器）发起的请求没有 Referrer 头。

## 解决方案

### 方案 A：移除 Referrer 限制（推荐）

1. 打开 [Google Cloud Console - 凭据页面](https://console.cloud.google.com/apis/credentials)
2. 选择你的项目
3. 在 **API 密钥** 列表中，点击对应的密钥名称
4. 在 **应用限制** 部分：
   - 当前为 **HTTP 引荐来源网址** → 改为 **无**
   - 或改为 **IP 地址**，添加你的服务器 IP
5. 点击 **保存**
6. 等待 **5 分钟** 生效

### 方案 B：新建无限制的 API Key

1. 在 [凭据页面](https://console.cloud.google.com/apis/credentials) 点击 **+ 创建凭据** → **API 密钥**
2. 点击新建的密钥 → **编辑 API 密钥**
3. **应用限制 (Application restrictions)** → 选择 **无 (None)**
4. **API 限制 (API restrictions)** → 选 **Don't restrict key**（个人本地使用推荐）
   - 如果想限制，需要先 [启用 Generative Language API](https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com)，启用后才能在下拉列表中找到并勾选
5. 保存并复制密钥到 `.env` 文件：
   ```
   GEMINI_API_KEY=你的新密钥
   ```

### 方案 C：通过 Google AI Studio 获取

1. 访问 [Google AI Studio](https://aistudio.google.com/apikey)
2. 点击 **Create API Key**
3. 选择项目 → 生成密钥
4. 此处生成的密钥默认无 Referrer 限制

## 图像生成配额说明

| 计划 | 图像生成限制 |
|------|------------|
| 免费层 | 可能为 0（需付费） |
| Blaze 按需计费 | 正常使用 |

如免费层配额为 0（429 错误），需在 [Google Cloud Billing](https://console.cloud.google.com/billing) 启用计费。

## 验证配置

```bash
# 检查 API Key 是否正常工作
source venv/bin/activate
python -c "
from google import genai
import os
client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
models = [m.name for m in client.models.list() if 'image' in m.name]
print('可用图像模型:', models)
"
```
