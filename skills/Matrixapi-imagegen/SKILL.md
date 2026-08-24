---
name: Matrixapi-imagegen
description: Generate or edit bitmap images through the recipient's own OpenAI-compatible Images API, or update this Matrixapi-imagegen Skill from its GitHub repository. Use for image generation, editing, retouching, inpainting, and requests such as 更新 Matrixapi-imagegen. Do not use for SVG/code-native graphics.
---

# Matrix API 图片生成与编辑

Generate or edit images with the bundled script and show the saved result to the user.

## Workflow

1. Resolve `scripts/generate.py` relative to this `SKILL.md` file.
2. Turn the user's request into a complete image prompt. Preserve requested subject, composition, style, lighting, colors, text, and constraints. Do not invent identifying details. When the user supplies local images, use only the exact attachment paths exposed by the current user message, in attachment order, and pass every one as a separate `--image`. Never scan a temp, clipboard, Downloads, workspace, or generated-images directory to discover or guess input images, and never substitute an image from an earlier message or task. Pass a supplied mask as `--mask`.
3. Choose the requested size, or use `1024x1024` when none is given. Common larger sizes are `2048x2048` for square 2K, `2048x1152` for landscape 2K, `3840x2160` for landscape 4K, and `2160x3840` for portrait 4K. Both edges must be multiples of 16, the longest edge must not exceed 3840 pixels or three times the shorter edge, and total pixels must be between 655,360 and 14,745,600. Native GPT Image 2 4K edits are preserved.
4. Choose the request mode automatically: no `--image` uses `/v1/images/generations`; one or more `--image` files uses multipart `/v1/images/edits`; `--mask` is optional for edit/inpaint requests. Repeat `--image` for multiple reference images, up to sixteen. Send the requested size unchanged for edits, including 1K, 2K, and 4K sizes; the provider decides whether that size is supported and may return an error. The original input file is never overwritten.
   For current-message attachments, count the attachment paths before running anything and add `--expected-images <count>` to the single edit command. This count must equal the number of images attached to that user message. If any current attachment path is unavailable, stop with the missing count instead of scanning directories, silently submitting fewer images, or asking the image API to generate from an incomplete set. Do not run separate shell commands to enumerate, locate, compare, hash, or inspect attachment files; the generation script validates the exact paths once before contacting the API.
   When the user requests high quality, HD, a final-quality render, `高清`, or `高质量`, include `--quality high` on the single generation/edit command. Do not silently replace it with `standard` or omit it. Leave quality unset only when the user did not request a quality level.
5. Run a generation with one unique task ID for the whole user request:

   ```text
   python <skill-directory>/scripts/generate.py --request-id "<task-id>" --prompt "<prompt>" --size <WIDTHxHEIGHT> --n 1
   ```

   For an edit with an original image:

   ```text
   python <skill-directory>/scripts/generate.py --request-id "<task-id>" --prompt "<edit instructions>" --expected-images <attachment-count> --image "<path-1>" --image "<path-2>" --n 1
   ```

   For masked local editing, add `--mask "<mask path>"`. Keep `n` at 1 unless the user explicitly requests a different total output count. For a large local GPT Image 2 edit (6 or more references or at least 48 MiB), the script automatically submits one asynchronous task and polls its status; it never splits the reference set into separate image requests. A story sequence is handled as separate scene tasks, with each completed original explicitly passed to the next scene.
   Run this script exactly once for the task. If the command tool reports that the
   process is still running, resume or wait on that same command session until it
   exits. A running process with no stdout is not a failed request. Do not start a
   second Python process, create a new task ID, or tell the user that the result is
   missing while the original command remains active. Keep this continuation silent;
   do not emit progress narration between waits.
6. For a multi-output request, the script writes an `image_saved` JSON line immediately after each image reaches local storage. Render that exact `preview_file` and link its `download_file` immediately, then silently resume the same command session; do not start another command or add progress narration. The final `complete` JSON line summarizes every saved output. For each output, use its existing `image_info` to report the actual saved dimensions, file format, and resolution label:

   ```text
   已生成，尺寸经检查为 3840 x 2160，PNG 格式，画质为 4K。
   ![generated image](C:/.../image.png)
   [点击打开或下载 4K 原图](C:/.../image.png)
   ```

   Resolution labels use the output's longest edge: `4K` at 3840px or above, `2K` at 2048px or above, otherwise `1K`.
   Do not put Windows `files` paths containing `\\` into Markdown; reserve `files` for the native saved-path report. Never omit the clickable original-image link.

   Require `ok: true`, a matching `request_id`, a non-empty `execution_id`, and a usable preview path. Render each `image_saved` event immediately, including for the first output, then silently continue the same command session. Do not start another command or add progress narration. Treat `event: complete` as the final signal. If it reports `partial: true`, keep and display all completed images and report only the failed scene/output; never retry a billed task automatically. Do not run any directory scan, sort, process check, dimension recheck, marker wait, or extra command after a result event. Never choose a file from an earlier request. A command that is still running can never trigger a retry. These rules apply equally to new images, edits, redraws, and second-pass modifications.
7. If generation or editing fails, report the sanitized error. Report the requested and actual saved size from the JSON fields. Never reveal, repeat, or inspect API keys in the response. Reject unsupported dimensions and missing input files locally without calling the API.

## Configuration

This distribution is intentionally pinned to the user's image API at
`https://eos.manyuvip.com/`. Other image API hosts are rejected by the script.

The script discovers credentials in this order:

1. `IMAGEGEN_API_KEY`, with optional `IMAGEGEN_BASE_URL` (defaults to the fixed API address) and `IMAGEGEN_MODEL`.
2. `OPENAI_API_KEY` and `OPENAI_BASE_URL`, only when the URL is the fixed API address.
3. The current Codex provider selected in CC Switch, only when it points to the fixed API address.

The API must implement `POST /v1/images/generations` for new images and `POST /v1/images/edits` with multipart `image`/optional `mask` fields for editing. Large local GPT Image 2 edits additionally use `async=true` and `GET /v1/status/{task_id}`. Responses may return either `data[].url` or `data[].b64_json`. Supported models are `gpt-image-2` and `gpt-image-2-pro`; the default is `gpt-image-2`. Set `IMAGEGEN_MODEL=gpt-image-2-pro` to use Pro.

To diagnose setup without generating or charging for an image, run:

```text
python <skill-directory>/scripts/generate.py --check-config
```

The check reports only whether a supported configuration was found, its generic source type, the current model, both supported models, and the exact Skill version. Do not print the provider name, endpoint, or credential.
It also reports `skill_version`, which is the authoritative installed version; do not infer the version from a folder name or an old log entry.

## Local size and crop processing

When an exact final size, crop, format, or compression is requested, use the local deterministic processing options after the upstream result. Preserve the untouched upstream file separately and never make another image API request for these pixel-only operations. The package supports `--output-size`, `--fit cover|contain|fill|inside|outside`, `--position`, `--crop`, `--output-format`, `--output-quality`, and `--process-only` for existing local files.

## Updating the Skill

When the user asks to `更新 Matrixapi-imagegen`, run:

```text
python <skill-directory>/scripts/update_skill.py
```

The updater downloads the latest package from the official GitHub repository, atomically replaces this Skill directory, validates the installed version/configuration, and removes a recognized legacy `api-imagegen` Skill directory. It never deletes or moves historical images under `generated_images/api-imagegen`; new results use `generated_images/Matrixapi-imagegen`. After success, **the final response must include the updater JSON `display_message` verbatim** (or reproduce the same fields if that string is unavailable), showing the installed version, current model, both supported models, and the restart requirement. Do not replace this with a generic success-only sentence.

## Boundaries

- This is a script-backed API workflow, not a native first-party image tool or Canvas integration.
- Use only the recipient's locally configured API access. Do not embed or request a distributor's private endpoint or key.
- Edit, reference-image, and mask support depends on the configured provider accepting the OpenAI-compatible edits request. The script sends local files as multipart form data and does not upload them anywhere except the configured API endpoint.
- A mask should be an image file, normally a same-size PNG with transparency or the provider's documented mask convention. The provider remains responsible for its exact inpainting semantics.
- Do not claim that every image model supports every edit parameter; report the provider's sanitized error when it rejects a feature.
- Save results under the recipient's local Codex generated-images directory unless `--out-dir` is explicitly supplied.
