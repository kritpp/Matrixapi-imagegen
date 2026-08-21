# Codex API Image Generation Skill

一个面向 Codex 的图片生成与编辑 Skill。它通过 MatrixAI 提供的 OpenAI 兼容图片接口工作，当前发行版只连接 `https://eos.manyuvip.com/`。

This repository distributes a Codex Skill for image generation and editing through the MatrixAI OpenAI-compatible Images API. This release is intentionally restricted to `https://eos.manyuvip.com/`.

## 功能 Features

- 文字生成新图：`POST /v1/images/generations`
- 上传一张或多张参考图进行编辑：`POST /v1/images/edits`
- 使用 PNG 蒙版进行局部重绘
- 自动选择生成或编辑模式，并保存结果到本机
- 支持 PNG、JPEG、WEBP、GIF 输入；最多 7 张输入图，最多 4 个结果
- 单次编辑最多可同时使用 7 张参考图片，适合固定人物、服装、构图和风格
- 支持 2K 与 4K 输出尺寸；可按横图、竖图或正方形选择尺寸

- Text-to-image generation via `POST /v1/images/generations`
- Reference-image editing via `POST /v1/images/edits`
- Masked local repainting with a PNG mask
- Automatic generate/edit mode selection and local result saving
- PNG, JPEG, WEBP, and GIF inputs; up to 7 input images and 4 outputs

## 安装 Install

### 一键下载

点击下面的链接即可直接从 GitHub 下载最新安装包：

[**一键下载 Matrixapi-imagegen v1.1.0 安装包**](https://github.com/kritpp/Matrixapi-imagegen/raw/refs/heads/main/Matrixapi-imagegen-v1.1.0.zip)

下载后解压，Windows 双击 `install-windows.bat`；macOS 双击 `install-macos.command`。

直接运行安装脚本时，脚本会隐藏提示你输入自己的 API Key，并自动配置固定图片接口 `https://eos.manyuvip.com` 和 `gpt-image-2`。安装完成后重启 Codex 即可，不需要再手动填写环境变量。

Windows 安装位置固定使用 Codex 默认目录：`C:\Users\当前用户名\.codex\skills\Matrixapi-imagegen`。压缩包可以下载到任意磁盘，安装结果不会写入下载盘或网站源码目录。

### 在 Codex 中拉取

也可以在 Codex 对话中运行：

```text
$skill-installer https://github.com/kritpp/Matrixapi-imagegen/tree/main/skills/Matrixapi-imagegen
```

这种方式只安装 Skill 文件，不会自动配置图片接口。拉取完成后，需要自己设置接口地址、API Key 和模型。

如果 Skill 没有立即出现，重启一次 Codex。

Restart Codex once if the Skill does not appear immediately.

## 手动配置 Configure（仅 Codex 拉取方式需要）

如果你使用 Codex 拉取方式，请使用你在 MatrixAI 网站生成的 API Key 手动配置。直接运行安装脚本的用户不需要重复设置。不要把 Key 提交到 GitHub，也不要把 Key 发到聊天中。

Use an API key created on your MatrixAI site. Never commit the key to GitHub or send it in chat.

Windows PowerShell:

```powershell
[Environment]::SetEnvironmentVariable("IMAGEGEN_BASE_URL", "https://eos.manyuvip.com", "User")
[Environment]::SetEnvironmentVariable("IMAGEGEN_API_KEY", "<your-api-key>", "User")
[Environment]::SetEnvironmentVariable("IMAGEGEN_MODEL", "gpt-image-2", "User")
```

Linux/macOS:

```bash
export IMAGEGEN_BASE_URL="https://eos.manyuvip.com"
export IMAGEGEN_API_KEY="<your-api-key>"
export IMAGEGEN_MODEL="gpt-image-2"
```

macOS 一键安装程序会把上述配置保存到 `~/.codex/Matrixapi-imagegen.env`，并限制文件权限；手动配置时也可以使用环境变量。

`IMAGEGEN_BASE_URL` 可省略，Skill 默认使用 MatrixAI；如果填写，脚本只接受 `eos.manyuvip.com`。

The macOS installer stores the configuration in `~/.codex/Matrixapi-imagegen.env` with local-only permissions. Environment variables are also supported.

The base URL is optional because the Skill defaults to MatrixAI. If supplied, the script accepts only the `eos.manyuvip.com` host.

检查配置但不发起生图：

Check configuration without generating an image:

```text
python <skill-directory>/scripts/generate.py --check-config
```

## 使用 Usage

直接说“生成一张图片……”通常会自动触发 Skill，也可以显式使用：

Natural language prompts usually trigger the Skill automatically. Explicit invocation:

```text
使用 $Matrixapi-imagegen 生成一张古装美女站在樱花树下的画面：春日柔光、粉色花瓣、精致汉服、自然姿态、电影感构图，不要文字。需要 4K 横图时指定 `3840x2160`。
```

### Codex 调用规则

- 在一个新建的 Codex 对话中，第一次使用时显式输入 `$Matrixapi-imagegen`。
- 同一个对话后续继续生成、编辑或重绘时，不需要重复调用，直接描述新需求即可。
- 新建另一个 Codex 对话时，需要在新对话中重新输入一次 `$Matrixapi-imagegen`；这只是启用当前对话，不是重新安装。
- 技能开始任务后会直接执行，完成后显示图片和本地保存路径。

### 后续自动更新

完成支持更新功能的新版安装后，在 Codex 中输入：

```text
更新 Matrixapi-imagegen
```

Skill 会从本仓库获取最新版，替换自身文件并保留本机 API 配置。更新完成后重启 Codex；在新对话中重新调用一次 `$Matrixapi-imagegen` 即可。更新不使用系统 `$skill-installer`，因为系统安装器遇到同名目录会停止而不会覆盖。

普通请求使用快速路径，不做 OCR、自动重试或额外检查。只有明确输入“精准文字”或“精准重绘”时，Skill 才启用更高质量和更高参考图保真度参数；这些模式可能更慢。

编辑原图：

Edit an original image:

```text
使用 $Matrixapi-imagegen 修改我上传的图片：只把天空改成日落，保持主体、构图和其他区域不变。
```

蒙版局部重绘：

Masked repainting:

```text
使用 $Matrixapi-imagegen 修改这张图：只重绘透明蒙版区域，把那里改成一扇木门，其他区域保持不变。
```

### 参考图与清晰度提示

- 一次编辑最多同时上传 **7 张参考图片**；可以分别提供人物、服装、姿势、场景、色彩和风格参考。
- 2K 示例：`2048x2048`（正方形）、`2048x1152`（横图）或 `1152x2048`（竖图）。
- 4K 示例：`3840x2160`（横图）或 `2160x3840`（竖图）。
- 示例：`使用 $Matrixapi-imagegen 参考这 7 张图片，生成一位古装美女站在樱花树下的 4K 竖图，保持人物脸部、服装纹理和整体色调一致。`

底层命令示例：

Underlying command:

```powershell
python "<skill-directory>\\scripts\\generate.py" `
  --prompt "只重绘蒙版区域，把那里改成一扇木门，其他区域保持不变。" `
  --image "D:\\images\\original.png" `
  --mask "D:\\images\\mask.png" `
  --size 1024x1024 `
  --n 1
```

## 接口要求 API requirements

MatrixAI 图片接口需要实现 OpenAI 兼容的以下接口：

The relay must implement these OpenAI-compatible endpoints:

- `POST /v1/images/generations` for text-to-image
- `POST /v1/images/edits` for multipart image editing with `image` and optional `mask`
- JSON responses with `data[].url` or `data[].b64_json`

本 Skill 只允许连接 `eos.manyuvip.com`，不会自动连接其他域名。接口返回的图片 URL 也必须属于该域名，编辑参数是否被模型完全支持，以网站返回结果为准。

This Skill only allows the `eos.manyuvip.com` host and does not silently connect to another domain. Returned image URLs must use the same host. Exact edit-parameter support remains provider-dependent.

## 安全 Security

- API Key 只保存在用户本机环境变量，macOS 一键安装时也会写入权限为仅本人可读的 `~/.codex/Matrixapi-imagegen.env`。
- 仓库不包含任何密钥、个人图片或生成结果。
- 输入图片只会发送到 `eos.manyuvip.com`。

- Keep API keys in local environment variables or the installer-created local-only `~/.codex/Matrixapi-imagegen.env` file.
- This repository contains no keys, personal images, or generated results.
- Input images are sent only to `eos.manyuvip.com`.

## License

Use this Skill in accordance with the terms of your relay provider and Codex environment.
