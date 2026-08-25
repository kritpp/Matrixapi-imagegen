# Matrix API 图片生成与编辑 Skill

一个面向 Codex 的图片生成与编辑 Skill。它通过 MatrixAI 提供的 OpenAI 兼容图片接口工作，当前发行版只连接 `https://eos.manyuvip.com/`。

This repository distributes a Codex Skill for image generation and editing through the MatrixAI OpenAI-compatible Images API. This release is intentionally restricted to `https://eos.manyuvip.com/`.

## 功能 Features

- 文字生成新图：`POST /v1/images/generations`
- 上传一张或多张参考图进行编辑：`POST /v1/images/edits`
- 使用 PNG 蒙版进行局部重绘
- 自动选择生成或编辑模式，并保存结果到本机
- 支持 PNG、JPEG、WEBP、GIF 输入；最多 16 张输入图，输出数量按用户请求执行
- 单次编辑最多可同时使用 16 张参考图片，适合固定人物、服装、构图和风格
- 支持 2K 与 4K 输出尺寸；可按横图、竖图或正方形选择尺寸
- 支持生成、编辑、重绘和蒙版局部重绘；原图永不覆盖
- 支持本地无 API 调用的确定性后处理：精确尺寸、缩放适配、像素裁剪、格式转换和压缩
- 支持 `cover`、`contain`、`fill`、`inside`、`outside` 五种尺寸适配方式和裁剪位置
- 支持 PNG、JPEG、WebP、AVIF 输出，以及 `--process-only` 处理已有本地图片
- 后处理保留未修改的上游原图，并生成 JSON 清单记录每次转换
- 多图输出逐张保存并立即返回；不会限制用户请求的输出数量

- Text-to-image generation via `POST /v1/images/generations`
- Reference-image editing via `POST /v1/images/edits`
- Masked local repainting with a PNG mask
- Automatic generate/edit mode selection and local result saving
- PNG, JPEG, WEBP, and GIF inputs; up to 16 input images and user-requested output count
- Local deterministic resizing, cropping, format conversion, and compression without another API call
- `cover`, `contain`, `fill`, `inside`, and `outside` fit modes with adjustable crop position
- `--process-only` for existing files, preserving the untouched upstream original and writing a JSON manifest

## 安装 Install

### 一键下载

点击下面的链接即可直接从 GitHub 下载最新安装包：

[**一键下载 Matrixapi-imagegen v1.8.11 安装包**](https://github.com/kritpp/Matrixapi-imagegen/raw/refs/heads/main/Matrixapi-imagegen-v1.8.11.zip)

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

默认不强制指定 `response_format`，由接口选择最快的兼容返回格式；脚本同时支持 `data[].b64_json` 和 `data[].url`。只有当接口明确要求固定格式时，才设置 `IMAGEGEN_RESPONSE_FORMAT=b64_json` 或 `url`；使用 URL 时，返回的图片 URL 和重定向仍必须是 `eos.manyuvip.com`。

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

生成成功后，Skill 会检查实际图片文件，并显示真实尺寸、文件格式、图片预览和本地保存路径。原图链接会按真实尺寸显示为“点击打开或下载 1K 原图”“点击打开或下载 2K 原图”或“点击打开或下载 4K 原图”。

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

- 一次编辑最多同时上传 **16 张参考图片**；可以分别提供人物、服装、姿势、场景、色彩和风格参考。
- 2K 示例：`2048x2048`（正方形）、`2048x1152`（横图）或 `1152x2048`（竖图）。
- 4K 示例：`3840x2160`（横图）或 `2160x3840`（竖图）。
- 示例：`使用 $Matrixapi-imagegen 参考这 16 张图片，生成一位古装美女站在樱花树下的 4K 竖图，保持人物脸部、服装纹理和整体色调一致。`

### 本地尺寸、裁剪与格式处理

这部分只在本机使用 Pillow 进行像素处理，不会再次请求图片 API，也不会产生新的生图费用。未处理的上游原图会保留，处理结果另存为新文件；原图不会被覆盖。

- `--output-size WIDTHxHEIGHT`：输出精确尺寸，支持不是 16 倍数的本地最终尺寸
- `--fit cover`：铺满目标画布并裁掉多余部分
- `--fit contain`：完整保留图片并在画布中留边
- `--fit fill`：直接拉伸到目标尺寸
- `--fit inside`：只缩小，不放大，保持完整内容
- `--fit outside`：保证覆盖目标尺寸，必要时放大后裁剪
- `--position center|top|bottom|left|right`：调整裁剪或留边位置，也支持 `x,y` 位置
- `--crop x,y,width,height`：按像素坐标裁剪源图后再处理
- `--output-format same|png|jpeg|webp|avif`：转换输出格式
- `--output-quality 1-100`：控制 JPEG、WebP 或 AVIF 压缩质量
- `--process-only`：只处理已有本地图片，禁止调用上游接口

示例：

```powershell
# 将已有图片裁剪为 1200x800，不调用上游，输出 WebP
python "<skill-directory>\\scripts\\generate.py" `
  --process-only --image "D:\\images\\original.png" `
  --output-size 1200x800 --fit cover --position center `
  --output-format webp --output-quality 90
```

每次本地处理都会在输出目录生成 `postprocess-manifest.json`，记录输入文件、输出文件、尺寸、裁剪、适配方式、格式和质量参数，便于追溯和复现。

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

默认不发送 `response_format`，让接口返回其最快的兼容格式；技能可直接处理
`data[].b64_json` 或同域名的 `data[].url`。如需固定格式，可设置
`IMAGEGEN_RESPONSE_FORMAT=b64_json` 或 `url`；返回 URL 及重定向仍必须保持
`eos.manyuvip.com`，其他域名会被拒绝。

The Skill leaves `response_format` unspecified by default so the relay can use its
fastest compatible response. It accepts `data[].b64_json` and same-host
`data[].url`. Set `IMAGEGEN_RESPONSE_FORMAT=b64_json` or `url` only when the relay
requires a fixed format; returned URLs and redirects must still remain on
`eos.manyuvip.com`, or they will be rejected.

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
