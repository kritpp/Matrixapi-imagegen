# Matrix API 图片生成与编辑 Skill

一个面向 Codex 的图片生成与编辑 Skill。它通过用户自己配置的 OpenAI 兼容图片接口工作，当前发行版固定使用 `https://eos.manyuvip.com/` 图片接口。

This repository distributes a Codex Skill for image generation and editing through the user's own OpenAI-compatible Images API. This release is intentionally pinned to the `https://eos.manyuvip.com/` image API.

## 功能 Features

- 文字生成新图：`POST /v1/images/generations`
- 上传一张或多张参考图进行编辑：`POST /v1/images/edits`
- 使用 PNG 蒙版进行局部重绘
- 自动选择生成或编辑模式，并保存结果到本机
- 编辑请求保留用户指定的 1K、2K 或 4K 尺寸，由上游接口决定是否支持
- 支持 PNG、JPEG、WEBP、GIF 输入；最多 15 张输入图，最多 4 个结果

- Text-to-image generation via `POST /v1/images/generations`
- Reference-image editing via `POST /v1/images/edits`
- Masked local repainting with a PNG mask
- Automatic generate/edit mode selection and local result saving
- Edit requests preserve the requested 1K, 2K, or 4K size; the provider decides whether it is supported
- PNG, JPEG, WEBP, and GIF inputs; up to 15 input images and 4 outputs

## 安装 Install

### 一键安装（推荐）

下载本仓库中的 `Matrixapi-imagegen-v1.2.8.zip`，解压后双击压缩包根目录里的 `install-windows.bat`（Windows）或 `install-macos.command`（macOS）。安装脚本会把 Skill 写入 Codex 默认的 `C:\Users\当前用户名\.codex\skills\Matrixapi-imagegen`，并配置固定接口地址和模型；压缩包放在哪个磁盘都不会改变安装位置。

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

安装完成后，在 Codex 中输入 `更新 Matrixapi-imagegen`，Skill 会下载本仓库的最新版发布包并自动替换本地同名 Skill，API Key 保留在本机。更新器带有互斥锁和有限重试，更新完成后重启 Codex；新建对话时重新输入一次 `$Matrixapi-imagegen`。

## 发布版本 Release

当前版本：`1.2.8`。本版本会在图片请求开始前锁定任务；当命令仍在运行时，Codex 只继续等待同一进程，不能把暂时没有 JSON 误判为失败。即使发生同任务重叠调用，后一个进程也只复用第一次的结果，不会再次请求图片接口。图片保存后仍立即输出成功 JSON，不增加目录扫描或额外收尾检查。

Current version: `1.2.8`, keeping one command session active until its real completion and reusing the first result if the same task is invoked concurrently, without adding image API calls or post-success scans.

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

