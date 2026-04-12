# Vertex AI (Imagen 3) 权限开通与绑卡指南

> **当前默认配置**：项目现已默认使用 **Gemini API（AI Studio）** 生成插图，只需设置 `GEMINI_API_KEY` 即可，**无需 Vertex AI**。
>
> 若你希望切换到 Imagen 3（`imagen-3.0-generate-001`）等专业生图模型，或需要原生的 `aspect_ratio="9:16"` 竖屏长宽比控制，才需要按本文配置 Vertex AI。

---

## 何时需要 Vertex AI？

| 场景 | 所需认证 |
|------|----------|
| 默认模型（`gemini-3.1-flash-image-preview` 等） | `GEMINI_API_KEY`（AI Studio 免费获取） |
| Imagen 3（`imagen-3.0-generate-001`）等 Vertex 专属模型 | Vertex AI 服务账号 JSON + 计费绑卡 |

代码会根据 `config.yaml` 中 `image.model` 自动选择认证路径：名称含 `imagen` 的模型走 Vertex AI，其余优先使用 `GEMINI_API_KEY`。

---

## 1. 绑定信用卡并激活计费账号 (Billing)

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)。
2. 在左上角导航菜单中，点击 **结算 (Billing)**。
3. 点击 **关联结算账号** → **管理结算账号** → **添加结算账号 (Add Billing Account)**。
4. 按照屏幕上的提示：
   - 选择你所在的国家/地区。
   - 填写个人或公司信息。
   - 填入**信用卡**（Visa、MasterCard 等均可）。
   - 点击确认并提交。Google 可能会预授权约 $1（随后退还）以验证卡的有效性。
5. 成功创建结算账号后，进入你的**项目主页**，确保项目（如 `brief-news`）已**关联该结算账号**。

## 2. 启用 Vertex AI 服务

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 顶部搜索栏搜索 **Vertex AI API**。
2. 点击进入结果页，然后点击蓝色的 **启用 (Enable)** 按钮。
3. 稍等片刻，直到提示启用成功。

## 3. 创建服务账号并获取 JSON 密钥

Vertex AI 使用 OAuth 2.0 服务账号认证而非简单的 API Key，需要下载一个 JSON 凭据文件：

1. 在左侧导航菜单中，点击 **IAM 和管理 (IAM & Admin)** → **服务账号 (Service Accounts)**。
2. 点击顶部的 **+ 创建服务账号 (+ CREATE SERVICE ACCOUNT)**。
   - **名称**：随意填写，如 `vertex-ai-uploader`。
   - 点击 **创建并继续 (CREATE AND CONTINUE)**。
3. **分配角色 (Grant this service account access to project)**：
   - 在角色下拉菜单中搜索并选择 **Vertex AI 用户 (Vertex AI User)**。
   - 点击 **继续 (CONTINUE)**，再点击 **完成 (DONE)**。
4. 回到服务账号列表页，点击刚才创建的账号进入详情。
5. 切换到 **密钥 (KEYS)** 选项卡。
6. 点击 **添加密钥 (ADD KEY)** → **创建新密钥 (Create new key)**。
7. 选择 **JSON** 格式，然后点击 **创建 (CREATE)**。
8. 浏览器会自动下载包含私钥的 JSON 文件。

## 4. 配置本地环境

1. 将下载的 JSON 文件重命名（如 `vertex_credentials.json`），放入项目的 `config/` 目录。
2. 在 `.env` 文件中指定路径：
   ```bash
   GOOGLE_APPLICATION_CREDENTIALS="config/vertex_credentials.json"
   ```
3. 在 `config/config.yaml` 中将模型切换为 Imagen：
   ```yaml
   image:
     model: "imagen-3.0-generate-001"
   ```

配置完成后，代码会自动检测到 `imagen` 关键字并切换到 Vertex AI 认证路径，可使用 `imagen-3.0-generate-001` 等专业模型。
