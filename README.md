# Matrixapi Image Generation Skill

This repository distributes the `Matrixapi-imagegen` Codex Skill for image generation, reference-image editing, masked local repainting, and deterministic local image delivery through the `matrixapii.com` relay. It is adapted from the original author's v1.4.3 source.

Current release: **v1.8.20**

## 安装 Install

### 一键安装包（推荐）

[下载 Matrixapi-imagegen v1.8.20](https://github.com/kritpp/Matrixapi-imagegen/raw/refs/heads/main/Matrixapi-imagegen-v1.8.20.zip)

解压后按系统运行安装程序：

- Windows：双击 `install-windows.bat`，或运行 `install-windows.ps1`
- macOS：双击 `install-macos.command`，或运行 `install-macos.sh`

安装程序只要求输入 MatrixAI API Key，并默认使用 `gpt-image-2`。API URL
`https://matrixapii.com` 已固定在 Skill 内部，无需输入或配置。安装完成后必须
重启 Codex。

本版本以 v1.8.19 为基线，保留异步任务编号兼容和防重复计费保护，并合并 v1.8.18 的比例透传修复。客户明确指定的比例（包括 `16:9`、`21:9` 等正整数比例）原样发送给上游；编辑默认保持输入图片比例，不做比例预检、自动替换、裁剪、本地重绘或二次提交。未指定比例时使用模型默认值，普通生成和渠道配置不变。

安装位置：

- Windows：`%USERPROFILE%\.codex\skills\Matrixapi-imagegen`
- macOS：`~/.codex/skills/Matrixapi-imagegen`

### 在 Codex 中安装

In a Codex conversation, run:

```text
$skill-installer https://github.com/kritpp/Matrixapi-imagegen/tree/main/skills/Matrixapi-imagegen
```

If the Skill does not appear immediately, restart Codex once.

这种方式只安装 Skill 文件，不会询问 API Key。安装后按下方“手动配置”设置 Key，
然后重启 Codex。

The release ZIP also keeps all four one-click installers:

- `install-windows.bat`
- `install-windows.ps1`
- `install-macos.command`
- `install-macos.sh`

## 手动配置 Configure

只需配置 API Key。模型变量可省略，默认使用 `gpt-image-2`：

Windows PowerShell：

```powershell
[Environment]::SetEnvironmentVariable("IMAGEGEN_API_KEY", "<your-api-key>", "User")
[Environment]::SetEnvironmentVariable("IMAGEGEN_MODEL", "gpt-image-2", "User")
```

macOS/Linux：

```bash
export IMAGEGEN_API_KEY="<your-api-key>"
export IMAGEGEN_MODEL="gpt-image-2"
```

`IMAGEGEN_BASE_URL` 不需要配置。即使客户电脑残留旧值，也不能覆盖 Skill 内固定的
`https://matrixapii.com`。需要 Pro 时，只把 `IMAGEGEN_MODEL` 改为
`gemini-3-pro-image`。

macOS 一键安装器会将 Key 和模型保存到权限为仅当前用户可读的
`~/.codex/Matrixapi-imagegen.env`；Skill 会自动读取该文件，但不会从中读取 URL。

检查配置且不调用生图接口、不产生费用：

```text
python <skill-directory>/scripts/generate.py --check-config
```

## 后续自动更新

使用一键安装包或 GitHub 安装完成后，可以在 Codex 中输入：

```text
更新 Matrixapi-imagegen
```

Skill 会运行内置 `scripts/update_skill.py`，从本 GitHub 仓库选择版本号最高的
`Matrixapi-imagegen-vX.Y.Z.zip`。更新器会先校验 ZIP 路径安全、大小、必需文件、
版本号与固定 URL，再原子替换 Skill；新版本自检通过后才删除回滚备份。任何校验或
自检失败都会恢复旧版本。

自动更新不会删除历史图片，不会修改 API Key、模型变量或
`~/.codex/Matrixapi-imagegen.env`，也不会调用生图接口。更新成功后必须重启 Codex。

The fixed API implements:

- `POST /v1/images/generations`
- `POST /v1/images/edits` with multipart `image` and optional `mask`

The Skill accepts up to 16 references. Native GPT Image 2 4K edits are kept at
full resolution. Large local edits (6 or more references, or at least 48 MiB
of source files) use the relay's asynchronous task endpoint automatically and
poll for the result, so a long synchronous connection cannot be lost after the
upstream has completed. It never retries a billed task automatically.

## Fast sequential comics

For a connected multi-page comic, `--story-pages` starts generation immediately
without requiring Codex to pre-write every page prompt. Page 1 uses the complete
original reference set. Each successful page returns an exact `next_arguments`
array; continuing with those arguments makes page 2 use only page 1, page 3 use
only page 2, and so on. The shared story request, style, characters, requested
size, quality, and ratio are persisted in a task-scoped state file.

Each command generates and returns one page, allowing Codex to display it before
starting the next page. Story pages always use `n=1`, default to vertical `2:3`
when no ratio is specified, and use async delivery. A failed page permanently
stops automatic continuation, so a completed or billed page is not submitted
again automatically.

```text
python skills/Matrixapi-imagegen/scripts/generate.py --task-id task-story-0001 \
  --story-pages 3 --prompt "<complete story request>" \
  --image /path/to/reference-1.png --image /path/to/reference-2.png \
  --size 4K --quality high
```

When editing or generating with an explicit `--aspect-ratio` such as `16:9`, the
Skill forwards that ratio unchanged to the configured upstream. It does not
choose a nearest enum ratio or resubmit with a different size. Use
`--output-size WIDTHxHEIGHT` only when the customer explicitly requests local
deterministic post-processing.

Local image edits use semantic whole-image reference editing by default so new
content can match the scene's perspective, lighting, shadows, and texture. For
the pinned GPT Image 2 channels, local mask requests are rejected before upload
because the documented model interface does not guarantee `mask`. Only set
`IMAGEGEN_MASK_SUPPORT=1` after the relay confirms mask support.

## Local delivery processing

The Skill can process generated files locally without another API request:

- exact custom output sizes, including dimensions that are not multiples of 16;
- `cover`, `contain`, `fill`, `inside`, and `outside` fitting;
- pixel-coordinate cropping and crop positioning;
- PNG, JPEG, WebP, and AVIF conversion and compression;
- preservation of the untouched upstream image for subsequent edits;
- a `--process-only` mode for existing local files;
- a JSON manifest describing each deterministic transformation.

Local processing uses Pillow for pixel operations only. It never downloads or
runs a local image-understanding model. Semantic editing, inpainting,
background removal, restoration, and other model-backed work remain upstream.
Install Pillow with `python -m pip install pillow` only if local processing is
requested and the active Python environment does not already provide it.

## Clearer upstream errors

The Skill distinguishes explicit content-policy responses, missing model/channel
routes, upstream service failures, and relays that return only a generic
`400 request failed`. A generic 400 is reported as an unknown upstream rejection,
not assumed to be a copyright verdict. Named franchise-character refusals are
not bypassed with obfuscated prompts or repeated paid retries.

## Current-task result delivery

Every generation or edit command uses a unique `--task-id`. The script writes one
atomic JSON record containing that task ID, request start/completion timestamps,
and the exact image paths, while returning the same JSON on stdout. Codex uses
only that current stdout result and never scans an image directory for a newer
file. On Windows the JSON record is hidden shortly after stdout delivery without
blocking the command or delaying image display.

Editing "the previous image" uses only the exact path returned by the preceding
successful result in the same conversation. A request to generate a new image
does not carry that path and is isolated from the previous edit chain.

## Use it

After installation, natural language prompts such as `生成一张图片……` trigger the Skill automatically. For an explicit invocation, use `$Matrixapi-imagegen`.

For an exact final size:

```text
python skills/Matrixapi-imagegen/scripts/generate.py --task-id task-example-0001 \
  --prompt "..." --size 4K --aspect-ratio auto \
  --output-size 1920x1080 --fit cover --output-format webp --output-quality 88
```

To process an existing image without calling the API:

```text
python skills/Matrixapi-imagegen/scripts/generate.py --task-id task-example-0002 --process-only \
  --image /path/to/image.png --output-size 1179x2556 --fit contain
```

Never commit API keys or other credentials to this repository.
