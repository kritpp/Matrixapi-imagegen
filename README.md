# Matrix API 图片生成与编辑 Skill

一个面向 Codex 的图片生成与编辑 Skill。它通过用户自己配置的 OpenAI 兼容图片接口工作，当前发行版固定使用 `https://eos.manyuvip.com/` 图片接口。

This repository distributes a Codex Skill for image generation and editing through the user's own OpenAI-compatible Images API. This release is intentionally pinned to the `https://eos.manyuvip.com/` image API.

## 功能 Features

- 文字生成新图：`POST /v1/images/generations`
- 上传一张或多张参考图进行编辑：`POST /v1/images/edits`
- 使用 PNG 蒙版进行局部重绘
- 自动选择生成或编辑模式，并保存结果到本机
- 高分辨率编辑自动按比例降到稳定的编辑尺寸，避免中转站高分辨率超时
- 支持 PNG、JPEG、WEBP、GIF 输入；最多 7 张输入图，最多 4 个结果

- Text-to-image generation via `POST /v1/images/generations`
- Reference-image editing via `POST /v1/images/edits`
- Masked local repainting with a PNG mask
- Automatic generate/edit mode selection and local result saving
- High-resolution edits automatically use a proportional lower working size to avoid relay timeouts
- PNG, JPEG, WEBP, and GIF inputs; up to 7 input images and 4 outputs

## 安装 Install

### 一键安装（推荐）

下载本仓库中的 `Matrixapi-imagegen-v1.1.6.zip`，解压后双击 `install-windows.bat`（Windows）或 `install-macos.command`（macOS）。安装脚本会把 Skill 写入 Codex 默认的 `C:\Users\当前用户名\.codex\skills\Matrixapi-imagegen`，并配置固定接口地址和模型；压缩包放在哪个磁盘都不会改变安装位置。

### Codex 拉取安装

在 Codex 对话中运行：

In a Codex conversation, run:

```text
$skill-installer https://github.com/kritpp/Matrixapi-imagegen/tree/main/skills/Matrixapi-imagegen
```

如果 Skill 没有立即出现，重启一次 Codex。

Restart Codex once if the Skill does not appear immediately.

## 配置 Configure

请在 MatrixAI 网站申请属于你自己的 API Key。不要把 Key 提交到 GitHub，也不要把 Key 发到聊天中。

Obtain your own API key from MatrixAI. Never commit the key to GitHub or send it in chat.

Windows PowerShell:

```powershell
[Environment]::SetEnvironmentVariable("IMAGEGEN_BASE_URL", "https://eos.manyuvip.com", "User")
[Environment]::SetEnvironmentVariable("IMAGEGEN_API_KEY", "<your-relay-key>", "User")
[Environment]::SetEnvironmentVariable("IMAGEGEN_MODEL", "gpt-image-2", "User")
```

Linux/macOS:

```bash
export IMAGEGEN_BASE_URL="https://eos.manyuvip.com"
export IMAGEGEN_API_KEY="<your-relay-key>"
export IMAGEGEN_MODEL="gpt-image-2"
```

`IMAGEGEN_BASE_URL` 可省略，Skill 默认使用 MatrixAI；如果填写，脚本只接受 `eos.manyuvip.com`。

`IMAGEGEN_BASE_URL` is optional because the Skill defaults to AUV. If supplied, the script rejects other hosts.

检查配置但不发起生图：

Check configuration without generating an image:

```text
python <skill-directory>/scripts/generate.py --check-config
```

## 使用 Usage

直接说“生成一张图片……”通常会自动触发 Skill，也可以显式使用：

Natural language prompts usually trigger the Skill automatically. Explicit invocation:

```text
使用 $Matrixapi-imagegen 生成一张 16:9 的未来城市海报：夜景、雨后街道、霓虹灯、电影感，不要文字。
```

编辑原图：

Edit an original image:

```text
使用 $Matrixapi-imagegen 修改我上传的图片：只把天空改成日落，保持主体、构图和其他区域不变。
```

对于高分辨率编辑，Skill 会自动降低本次 API 编辑尺寸并保持宽高比；原图不会被覆盖。返回 JSON 会同时给出 `requested_size`、`edit_size` 和 `resized_for_edit`。

For high-resolution edits, the Skill automatically lowers the API working size while preserving aspect ratio. The original file is never overwritten. JSON output includes `requested_size`, `edit_size`, and `resized_for_edit`.

蒙版局部重绘：

Masked repainting:

```text
使用 $Matrixapi-imagegen 修改这张图：只重绘透明蒙版区域，把那里改成一扇木门，其他区域保持不变。
```

## 接口要求 API requirements

中转站需要实现 OpenAI 兼容的以下接口：

The relay must implement these OpenAI-compatible endpoints:

- `POST /v1/images/generations` for text-to-image
- `POST /v1/images/edits` for multipart image editing with `image` and optional `mask`
- JSON responses with `data[].url` or `data[].b64_json`

本 Skill 只允许连接 `eos.manyuvip.com`，不会自动使用其他图片接口。编辑参数是否被模型完全支持，以接口返回结果为准。

This Skill only allows the `eos.manyuvip.com` host and does not silently use another image API. Exact edit-parameter support remains provider-dependent.

## 安全 Security

- API Key 只存在于用户本机环境变量。
- 仓库不包含任何密钥、个人图片或生成结果。
- 输入图片只会发送到用户配置的 AUV 中转站。

- Keep API keys in local environment variables only.
- This repository contains no keys, personal images, or generated results.
- Input images are sent only to the user's configured AUV relay.

## 更新 Update

安装完成后，在 Codex 中输入 `更新 Matrixapi-imagegen`，Skill 会从本仓库获取最新版本并自动替换本地同名 Skill，API Key 保留在本机。更新完成后重启 Codex；新建对话时重新输入一次 `$Matrixapi-imagegen`。

## 发布版本 Release

当前版本：`1.1.6`。本版本以原作者核心请求流程为基准，仅固定 MatrixAI 图片接口并更名为 Matrixapi-imagegen。

Current version: `1.1.6`, based on the original request flow with only the MatrixAI API address and Skill naming changed.

### 1.1.2

- 新增 `download_files` 输出，并要求每张预览图附带“点击打开或下载原图”链接。
- Added `download_files` output and a clickable “open or download original image” link for every preview.

### 1.1.1

- 新增 `preview_files` 输出，使用适配聊天渲染器的绝对路径预览生成图片。
- Added `preview_files` output with renderer-compatible absolute paths for generated-image previews.
