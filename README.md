# Matrixapi Image Generation Skill

This repository distributes the `Matrixapi-imagegen` Codex Skill for image generation, reference-image editing, masked local repainting, and deterministic local image delivery through the `matrixapii.com` relay. It is adapted from the original author's v1.4.3 source.

Current release: **v1.9.0**

## 安装 Install

### 一键安装包（推荐）

[下载 Matrixapi-imagegen v1.9.0](https://github.com/kritpp/Matrixapi-imagegen/releases/download/v1.9.0/Matrixapi-imagegen-v1.9.0.zip)

解压后按系统运行安装程序：

- Windows：双击 `install-windows.bat`，或运行 `install-windows.ps1`
- macOS：双击 `install-macos.command`，或运行 `install-macos.sh`

安装程序只要求输入 MatrixAI API Key，并默认使用 `gpt-image-2`。API URL
`https://matrixapii.com` 已固定在 Skill 内部，无需输入或配置。安装完成后必须
重启 Codex。

v1.9.0 新增 GPT Image 2.6 与 Midjourney 三种模式，统一支持提示词任意位置的模型简称。安装与更新结果会列出完整支持模型和简称。

| 简称（提示词任意位置） | 完整请求模型 | 用途 |
| --- | --- | --- |
| 不指定 | `gpt-image-2`（按 Key 可用模型发现） | 默认图片生成与编辑 |
| `-f2.5` | `gpt-image-2.5-flare` | 2.5 Flare |
| `-s2.5` | `gpt-image-2.5-sunburst` | 2.5 Sunburst |
| `-f2.6` | `gpt-image-2.6-flare` | 2.6 快速迭代与交互式创作 |
| `-s2.6` | `gpt-image-2.6-sunburst` | 2.6 细致视觉创作与构图 |
| 完整模型名 | `gemini-3-pro-image-preview` | 现有 Gemini 图片通道 |
| `-m8` | `midjourney-v8.2` | MJ 普通模式 |
| `-m8f` | `midjourney-v8.2-fast` | MJ Fast 模式 |
| `-m8t` | `midjourney-v8.2-turbo` | MJ Turbo 模式 |

`-f2.6 生成花海`、`生成 -f2.6 花海`、`生成花海 -f2.6` 均选择相同模型。
兼容 `模型-f2.5`、`技能-m8f` 等写法；相近型号不会混淆，不同模型简称冲突时本地停止。
未指定时按当前 API Key 的可用模型选择：普通图片分组默认 `gpt-image-2`，只有 Gemini 的分组自动用 Gemini，MJ 分组默认 `midjourney-v8.2`；显式指定时使用对应完整请求模型。

### MJ 比例、尺寸和返回图片

例如：`生成一张蓝色陶杯，-m8f，比例16：9`。客户也可写 `16:9` 或 `--ar 16:9`，放在提示词任意位置；技能会转成上游需要的末尾 `--ar` 参数。这不限制模型简称的位置。

MJ 每次固定返回 **4 张图片**，四张合计一次调用费用，不再按返回张数乘 4；金额以模型广场当前分组价格为准。一次请求交付四张，不需要重复调用。当前通道仅支持文字生图，不支持参考图或编辑。

MJ 没有独立的 1K/2K/4K/8K 尺寸参数；不要给 MJ 传 `--size`。`4K` 描述词和 `--hd` 均不保证精确 4K 像素，交付以返回图片实际尺寸为准。若明确需要本地放大至 3840×2160，可使用 `--output-size 3840x2160`，必须说明是本地处理并保留原图。MJ 不使用通用 `--quality`、`--async`、`--stream`；风格和质量使用提示词中的 `--s`、`--q`、`--hd` 等支持参数。

MJ 分组未指定简称时默认 `midjourney-v8.2`；只有明确指定 `-m8f`/`-m8t` 或完整模型名才用 Fast/Turbo，普通版不可用时不自动换到更快模式。

实测：普通版和 Fast 的 `16:9` 返回四张 `1456×816`；Turbo 的 `9:16 --hd` 返回四张 `1648×2944`，均已通过实际 Codex 技能调用并展示图片。写 `4K` 细节描述并未使普通版变成原生 4K。尺寸是本次结果，不保证每次固定相同。

当前支持情况取决于 API Key 所在分组。2.5 的显式 `max` 和原有 Gemini 流程继续保留。

本版本不做比例预检或自动改比例。客户明确指定的比例（包括 `16:9`）原样发送给上游；不会因比例判断而二次提交、裁剪或本地重绘。未指定比例时才使用模型默认值，普通生成和渠道配置不变。

GPT Image 图片通道支持显式 `8K` 输出档位（MJ 不适用）。只有客户明确要求 `8K` 时才发送该档位；
`1K`、`2K`、`4K` 的默认行为、比例和画质参数保持不变。

8K 等大结果采用流式保存：状态查询只接收小型任务 JSON，完成后的图片分块写入临时文件并校验后原子保存。不会因图片大于旧的内存读取上限而重新提交、重复扣费或降低请求尺寸。

成功返回会在图片链接下显示最终图片的真实像素尺寸和比例；这只是本地回显，不增加任何上游请求或等待时间。

v1.8.92 明确区分 600 秒本地等待上限与上游终态：本地等待结束不会被当作失败或退款依据，并保留同一 task-id 供后续只读状态恢复；不会重新提交付费请求。只有中转站确认同一上游任务最终失败、取消、过期或明确不存在时才允许退款，排队、处理中、网络错误与 5xx 均保持结果未知且不退款。

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
`https://matrixapii.com`。支持模型与简称见上表。不写模型标记时按当前 Key 的可用分组选择默认模型；
提示词任意位置写 `模型-f2.5` 可选择 `gpt-image-2.5-flare`，写 `模型-s2.5` 可选择
`gpt-image-2.5-sunburst`；
当 2.5 模型所在老分组明确返回模型/渠道不可用时，Skill 只自动回退一次到 `gpt-image-2`，
普通 502/503 或可能已送达上游的错误不会重试。未显式指定模型时，Skill 会通过一次
不产生费用的 `/v1/models` 查询识别当前 API Key 的可用分组：优先使用
`gpt-image-2`；仅有 2.5、2.6、Gemini 或 MJ 时直接请求并展示该分组实际开放的官方模型。

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

自动更新不会删除历史图片、不会修改 API Key，也不会调用生图接口。升级到 v1.8.96
时，只会把旧安装器的默认模型 `gpt-image-2.5-flare` 迁移为 `gpt-image-2`；其他自定义
模型值保持不变。更新成功后必须重启 Codex。

The fixed API implements:

- `POST /v1/images/generations`
- `POST /v1/images/edits` with multipart `image` and optional `mask`

Non-MJ image routes accept up to 16 references; MJ does not support references. Native GPT Image 2 4K edits are kept at
full resolution. Large local edits (6 or more references, or at least 48 MiB
of source files) use the relay's asynchronous task endpoint automatically and
poll for the result, so a long synchronous connection cannot be lost after the
upstream has completed. It never retries a billed task automatically.

The VPS determines the selected image route internally. For its Yaliai routes,
it accepts up to six forwarded images, 12 MiB per image, and 30 MiB total; when
needed, the VPS creates deterministic, uncropped JPEG contact sheets with up
to six source images each and still makes one upstream request. Other providers
remain on the original reference path and are never packed.

## Fast sequential comics

For a connected multi-page comic on a non-MJ image-editing route, `--story-pages` starts generation immediately
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
  --size 4K
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

An explicit content-policy refusal is scoped only to that accepted task. It
must not become a conversation-wide block: when the customer later explicitly
asks to generate again, the Skill starts one new task and checks the current
upstream response, even in the same Codex conversation. It never disguises the
subject or retries the old billed task; if the current upstream refuses again,
that new task is reported once using its own actual response.

## Current-task result delivery

Every generation or edit command uses a unique `--task-id`. The script writes one
transient atomic JSON handoff containing that task ID, request start/completion
timestamps, and exact image paths while returning the same JSON on stdout. Codex
uses only that current stdout result and never scans an image directory for a
newer file. After stdout delivery, successful and known-failed task JSON is
removed automatically; Windows performs that cleanup in the background without
blocking the command or delaying image display. A genuinely unknown submitted
task keeps only the recovery metadata needed to prevent a duplicate paid POST.

Async 2K/4K/8K commands may run for several minutes. If the shell tool yields a
running session/cell instead of an exit code, that is progress, not an image
failure: Codex must keep waiting on that exact process until it exits and emits
its terminal JSON. It must not answer the customer, classify the task as failed,
or start another command while the original process is still running. A pending
task continues to use free status GET requests and never resubmits the paid image
request.

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

