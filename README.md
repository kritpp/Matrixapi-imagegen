# Matrix API 图片生成与编辑 Skill

一个面向 Codex 的图片生成与编辑 Skill。它通过用户自己配置的 OpenAI 兼容图片接口工作，当前发行版固定使用 `https://eos.manyuvip.com/` 图片接口。

This repository distributes a Codex Skill for image generation and editing through the user's own OpenAI-compatible Images API. This release is intentionally pinned to the `https://eos.manyuvip.com/` image API.

## 功能 Features

- 文字生成新图：`POST /v1/images/generations`
- 上传一张或多张参考图进行编辑：`POST /v1/images/edits`
- 使用 PNG 蒙版进行局部重绘
- 自动选择生成或编辑模式，并保存结果到本机
- 编辑请求保留用户指定的 1K、2K 或 4K 尺寸，由上游接口决定是否支持
- 支持 PNG、JPEG、WEBP、GIF 输入；最多 16 张输入图；输出数量按用户要求原样提交

- Text-to-image generation via `POST /v1/images/generations`
- Reference-image editing via `POST /v1/images/edits`
- Masked local repainting with a PNG mask
- Automatic generate/edit mode selection and local result saving
- Edit requests preserve the requested 1K, 2K, or 4K size; the provider decides whether it is supported
- PNG, JPEG, WEBP, and GIF inputs; up to 16 input images; requested output counts are passed through unchanged

## 安装 Install

### 一键安装（推荐）

下载本仓库中的 `Matrixapi-imagegen-v1.4.0.zip`，解压后双击压缩包根目录里的 `install-windows.bat`（Windows）或 `install-macos.command`（macOS）。安装脚本会完整替换 Codex 默认的 `C:\Users\当前用户名\.codex\skills\Matrixapi-imagegen`，并配置固定接口地址和模型；压缩包放在哪个磁盘都不会改变安装位置。识别到旧版 `api-imagegen` 技能代码时会将其移除，但不会删除 `generated_images\api-imagegen` 中的历史图片。

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

编辑请求会按用户指定尺寸发送，包括 1K、2K 和 4K；如果上游不支持该尺寸，Skill 会返回上游错误，不会静默降低画质。原图不会被覆盖。返回 JSON 会同时给出 `requested_size`、`edit_size` 和 `resized_for_edit`。

Edit requests are sent at the requested size, including 1K, 2K, and 4K. If the provider does not support that size, the Skill reports the provider error instead of silently reducing quality. The original file is never overwritten. JSON output includes `requested_size`, `edit_size`, and `resized_for_edit`.

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

安装完成后，在 Codex 中输入 `更新 Matrixapi-imagegen`，Skill 会下载本仓库的最新版发布包并自动完整替换本地同名 Skill，API Key 保留在本机。更新器会验证实际安装版本、当前模型及 `gpt-image-2`/`gpt-image-2-pro` 支持信息，并清理能安全识别的旧 `api-imagegen` 技能代码；历史图片不会删除。更新完成后重启 Codex；新建对话时重新输入一次 `$Matrixapi-imagegen`。

## 发布版本 Release

当前版本：`1.4.0`。输出总数不设技能侧上限；多图在一个本地命令内逐张请求、逐张保存并立即发出预览事件，避免一次批量超时丢失全部结果。默认单张等待为 600 秒；6 张以上或 48 MiB 以上参考图自动使用异步任务并轮询状态，支持原生 4K 竖版和本地缩放、裁剪、格式转换。自动更新会完整替换技能、报告安装版本、当前模型及支持模型并安全清理旧技能代码，新图片改存到 `generated_images/Matrixapi-imagegen`，历史图片保持原位。

Current version: `1.4.0`. The Skill imposes no total output cap. Multi-output jobs run sequential single-image calls inside one local command, save each result immediately, and emit an immediate preview event so a later failure cannot discard earlier files. The default per-image timeout is 600 seconds; large reference sets automatically use asynchronous task/status polling, with native portrait 4K and local resize, crop, and format conversion. Updates fully replace the current Skill, report the installed version, current model, and supported models, safely remove recognized legacy Skill code, and write new results under `generated_images/Matrixapi-imagegen` without moving historical images.

### 1.4.0

- 支持最多 16 张参考图；参考图达到 6 张或 48 MiB 时自动使用异步任务并轮询状态，整组参考图只提交一次。
- 支持最长边 3840px、总像素 14.7MP 的 4K 竖版请求；输出数量不设技能侧上限。
- 成功保存一张就立即输出预览事件；后续图片逐张继续，单张失败只重试该张，不重复已成功请求。
- 自动更新输出安装版本、当前模型和支持模型，并使用有上限的网络等待，避免长时间无反馈。
- 新增本地 `process-only` 缩放、裁剪、格式转换和压缩，不重新调用上游、不覆盖原图。

### 1.3.3

- 多图总数不设技能上限；一个本地命令内逐张请求，单次接口调用固定为 1 张。
- 每张图片保存后立即输出 `image_saved` 预览事件；后续失败时保留并返回全部已成功图片，不自动重试。
- 单张请求和结果下载默认等待提高到 600 秒，覆盖已确认超过 5 分钟的 4K 编辑。
- 自动更新完整替换当前技能，安装后显示 `gpt-image-2`、`gpt-image-2-pro` 和当前模型。
- 仅删除可识别的旧 `api-imagegen` 技能代码；历史图片不删除，新图片使用 `generated_images/Matrixapi-imagegen`。
- Multi-output jobs have no Skill-side cap and use sequential one-image API calls inside one command.
- Each saved image emits an immediate preview event; later failures preserve completed files without an automatic retry.
- The per-image request/download timeout is now 600 seconds.
- Updates replace the active Skill, report both supported models, retire recognized legacy Skill code, and preserve historical images.

### 1.3.2

- 取消技能侧最多 4 张输出的限制；用户要求 5 张、10 张或其他正整数时原样提交给接口。
- 默认输出仍为 1 张；零或负数在联网前拒绝，不产生图片费用。
- 参考图、WA/VX 请求、重试、旧图保护和快速成功 JSON 逻辑均未改动。
- Removed the Skill-side four-output cap; any positive requested count is passed through unchanged.
- The default remains one, and non-positive counts fail before the image API is called.

### 1.3.1

- 多参考图只接受当前消息的准确附件路径；联网前核对期望数量，缺图时明确停止且不调用图片接口。
- 禁止为寻找输入图片扫描临时目录或历史目录，避免漏图、旧图混入和多轮无效命令。
- Direct generation and existing image request/result behavior are unchanged.
- Multi-reference requests accept only exact current-message attachment paths and validate the expected count before contacting the image API.

### 1.3.0

- 参考图上限从 15 张提高到 16 张；用户明确要求高清时保留并发送 `quality=high`。
- Reference-image limit increased from 15 to 16; explicit high-quality requests preserve and send `quality=high`.

### 1.2.9

- ready 结果新增唯一执行标识，旧时间段留下的同任务 ID 结果不再被复用。
- 仅同一首个命令仍在运行期间发生的重叠调用可复用结果，继续防止重复调用和计费。
- 图片保存后立即输出 JSON，随后才隐藏记录文件；不增加目录扫描、指纹计算或额外命令。
- 自动更新器接受 ZIP 中合法的 `./` 开头路径，并继续拒绝绝对路径和目录穿越路径。
- Windows 与 macOS 根目录自动安装脚本继续包含在压缩包内。
- Ready results now carry a unique execution ID, so a later run cannot reuse an old result with the same task ID.
- Only invocations overlapping the still-active original process may reuse its result.
- Success JSON is emitted immediately after saving and before cosmetic hiding, with no scans, fingerprints, or extra commands.
- The updater accepts legitimate `./` ZIP paths while still rejecting absolute paths and traversal.
- Root-level Windows and macOS installers remain included.

### 1.2.8

- 命令仍在运行时只续等原命令，不再因暂时没有 stdout/JSON 启动第二次生图。
- 图片请求前原子锁定任务；同任务发生重叠调用时复用首次成功结果，不重复调用或计费。
- 图片保存后立即输出成功 JSON，隐藏记录文件和重复保护不会增加正常展示链路。
- `--check-config` 新增明确的 `skill_version`，日志 User-Agent 同步为当前版本。
- 压缩包继续包含 Windows 与 macOS 根目录自动安装脚本。
- While a command is still running, Codex resumes the same command instead of starting another image request because stdout is temporarily empty.
- A task is reserved before the API call; overlapping invocations reuse the first successful result without another image request or charge.
- The success JSON is emitted immediately after saving, with no extra post-success scans.
- `--check-config` now reports the authoritative `skill_version`, and the request User-Agent matches it.
- Windows and macOS root-level automatic installers remain included in the package.

### 1.2.5

- 成功 JSON 现在明确是最终结果；Codex 渲染后立即结束，不再发起额外确认、目录扫描、尺寸复核或收尾请求。
- 保留 `request_id`、精确 `preview_files` 路径和 ready 标记，继续防止旧图和重复生图。
- The success JSON is explicitly the final result; Codex renders it immediately without follow-up confirmation, directory scans, dimension rechecks, or cleanup requests.
- `request_id`, exact `preview_files` paths, and the ready marker remain in place to prevent stale images and duplicate generation.

### 1.2.4

- 成功路径只写入一个隐藏的 `.ready-<task-id>.json`，输出成功 JSON 后立即结束，不再重复写入 `.result` 和 `.completed` 文件。
- Codex 收到有效成功 JSON 后必须立即展示，不再扫描目录、检查进程或等待其他标记。
- The success path writes one hidden `.ready-<task-id>.json`, prints the validated JSON, and exits without duplicate result/completion sidecars.
- Codex must render a valid success JSON immediately and must not scan directories, inspect processes, or wait for another marker.

### 1.2.3

- 参考图上限从 7 张提高到 15 张。
- Reference-image limit increased from 7 to 15.

### 1.2.2

- 图片文件和尺寸校验完成后立即写入 `.ready-<task-id>.json`，Codex 可直接读取并显示当前任务图片，不必等待命令包装器退出。
- `.ready-*.json`、`.result-*.json` 和 `.completed-*.json` 均会在 Windows 资源管理器中隐藏，但仍保留原路径和可读性。
- Ready 标记同时锁定任务 ID，避免命令收尾较慢时重复提交生图请求。
- After the image file and dimensions are validated, `.ready-<task-id>.json` is written immediately so Codex can render the current task without waiting for wrapper cleanup.
- `.ready-*.json`, `.result-*.json`, and `.completed-*.json` are hidden in Windows Explorer while remaining readable at their original paths.
- The ready marker also reserves the task ID, preventing duplicate image requests while command cleanup is still running.

### 1.2.1

- 编辑请求不再自动降到 1792px；1K、2K、4K 按请求尺寸发送，上游不支持时明确返回错误。
- Edit requests no longer shrink to 1792px; requested 1K, 2K, and 4K sizes are sent unchanged, with an explicit provider error when unsupported.

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

