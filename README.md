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

下载本仓库中的 `Matrixapi-imagegen-v1.2.0.zip`，解压后双击 `install-windows.bat`（Windows）或 `install-macos.command`（macOS）。安装脚本会把 Skill 写入 Codex 默认的 `C:\Users\当前用户名\.codex\skills\Matrixapi-imagegen`，并配置固定接口地址和模型；压缩包放在哪个磁盘都不会改变安装位置。

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

当前版本：`1.2.0`。本版本以原作者核心请求流程为基准，增强当前任务结果回传与重复请求保护，并在 Windows 中隐藏任务状态文件。

Current version: `1.2.0`, based on the original request flow with stronger current-task result delivery, duplicate-request protection, and hidden Windows task sidecars.

### 1.2.0

- Windows 会自动隐藏 `.result-*.json` 和 `.completed-*.json` 状态文件；文件仍保留在原路径，Codex 可正常读取。
- Windows automatically hides `.result-*.json` and `.completed-*.json` task sidecars while keeping them at the same readable paths.

### 1.1.9

- 每次请求绑定唯一任务 ID，并写入同任务结果文件；命令收尾较慢时可直接读取已完成结果，避免重复生图。
- Each request now has a unique task ID and result sidecar, so a delayed command wrapper cannot cause duplicate generation.

### 1.1.8

- 新生图只展示当前命令返回的图片路径，禁止从共享输出目录扫描或误用历史图片；编辑和重绘流程保持不变。
- New text-to-image results use only the image path returned by the current command; shared-directory history cannot be used as a fallback. Editing and redraw behavior is unchanged.

### 1.1.7

- 生成结果现在会从实际输出文件检测宽高与 PNG、JPEG、WEBP 或 GIF 格式，并为每张图片输出 `1K`、`2K` 或 `4K` 画质标签。
- 预览下方的原图链接会显示对应画质，例如“点击打开或下载 4K 原图”。

- Generated results now inspect the actual output dimensions and PNG, JPEG, WEBP, or GIF format, then report a per-image `1K`, `2K`, or `4K` label.
- Each preview's original-image link now includes the matching resolution label, such as “open or download 4K original”.

