---
name: Matrixapi-imagegen
description: Generate or edit bitmap images through the recipient's own OpenAI-compatible Images API, including local reference images and masks, or update this Matrixapi-imagegen Skill from its official GitHub repository. Use for requests to generate, create, draw, edit, retouch, inpaint, update Matrixapi-imagegen, or make a picture, illustration, poster, wallpaper, concept art, or other raster image, including Chinese prompts such as 生成图片, 画一张图, 生图, 修改这张图, 局部重绘, and 更新 Matrixapi-imagegen. Do not use for SVG/code-native graphics.
---

# Matrix API 图片生成与编辑

Use the bundled script to generate or edit images through the recipient's configured
MatrixAI-compatible Images API. Start a clear request immediately and keep the
conversation response concise.

## Conversation and response rules

- In a new Codex conversation, explicitly invoke `$Matrixapi-imagegen` once. In the
  same conversation, keep using the active Skill without repeating the invocation.
- Begin a valid image task immediately. Before the command, output only `正在生成图片…`.
  Do not narrate script reads, parameter analysis, shell commands, retries, or API details.
- After success, use each object in `images`; do not guess dimensions from the request.
  First state `已生成，尺寸经检查为 WIDTH × HEIGHT，FORMAT 格式。` Then render the
  image with `![生成图片](<DISPLAY_PATH>)`, add `[点击打开或下载 RESOLUTION 原图](<DISPLAY_PATH>)`,
  and state `图片已保存至：PATH`. Use the exact `display_path`, `resolution`, and `path`
  returned by the script. Keep the preview and link on separate lines.
- Before rendering, require the script result to contain a non-empty `images` array and
  an existing saved file. Never emit an empty image placeholder. On failure, show only
  the sanitized error.
- Do not claim that this external Skill can create Codex's native image-generation
  shimmer card; that UI belongs to Codex itself.

## Hard execution boundaries

- Treat one user request as one task. Create one task ID and run the image command
  once. A successful response is a terminal state: stop immediately after showing
  the returned image and path.
- Never judge whether the returned picture matches the prompt and never start a
  second generation, edit, resize, upscale, OCR pass, file scan, or skill reload
  automatically. A visual mismatch is not a failure of the request.
- Only run another API request when the user explicitly asks for `重试`, `重新生成`,
  or another clearly new edit. Keep the new request separate from the prior task
  and use its newly returned `images` only.
- For an explicit retry of the same task ID, add `--allow-repeat`; never add this
  flag to an ordinary request.
- If the command exits without usable stdout, read only the exact sidecar
  `~/.codex/generated_images/Matrixapi-imagegen/.result-<task-id>.json` for the
  current task ID. Verify its listed files, then display that result. Never scan
  for the latest file, inspect another task, or run the API again because stdout
  was lost.
- Do not add a configuration check before an ordinary request. Do not expose command
  output, parameter analysis, or implementation details in the user-facing reply.

## Workflow

1. Resolve `scripts/generate.py` relative to this `SKILL.md` file.
2. Turn the user's request into a complete image prompt. Preserve requested subject, composition, style, lighting, colors, text, and constraints. Do not invent identifying details. When the user supplies a local image, pass it as `--image`; pass a supplied mask as `--mask`.
   Classify an edit from the request without adding extra API calls:
   - **Text/translation/replacement:** preserve the original layout and non-text
     content. If the user asks for exact text, translation accuracy, or sharper
     lettering, use the precision path (`--quality high --input-fidelity high`) and
     state the exact replacement text in the prompt.
   - **Person/background/object/local change:** use edit mode with the supplied
     reference image. If the user supplies a mask, pass it unchanged; otherwise
     describe the target region precisely and do not invent a mask file.
   - **Whole-image redesign:** use generation mode unless the user explicitly asks
     to preserve the supplied image.
   These paths are routing decisions for the single request, not permission to
   retry or post-process it. The API/model remains responsible for final text
   rendering and pixel-level fidelity.
3. Choose the requested size. For an ordinary edit without an explicit size, use
   the source aspect ratio in the relay's fast working range; an explicit 2K or 4K
   size is honored. Common larger sizes are `2048x2048` for square 2K, `2048x1152` for
   landscape 2K, `3840x2160` for landscape 4K, and `2160x3840` for portrait 4K.
   Both edges must be multiples of 16, the longest edge must not exceed 3840 pixels
   or three times the shorter edge, and total pixels must be between 655,360 and
   8,294,400.
4. Choose the request mode automatically: no `--image` uses `/v1/images/generations`; one or more `--image` files uses multipart `/v1/images/edits`; `--mask` is optional for edit/inpaint requests. Repeat `--image` for multiple reference images, up to seven.
5. Use the fast path by default: run one request with `--n 1`, without OCR, post-processing, automatic retries, or configuration checks.
6. For an explicit precision text/edit request, add `--quality high --input-fidelity high`
   in that same request. Do not silently run a second high-quality pass. These
   opt-in parameters may be slower; do not enable them for ordinary requests.
7. For a follow-up such as `把刚才的图片...`, reuse the exact last generated absolute path from the current conversation as `--image`. Preserve the current image, mask, size, composition, and style; replace only the requested change. If the user supplies a new image, start a new edit context. Never silently turn a follow-up edit into text-to-image generation.
8. Run a generation:

   ```text
   python <skill-directory>/scripts/generate.py --request-id "<task-id>" --prompt "<prompt>" --size <WIDTHxHEIGHT> --n 1
   ```

   For an edit with an original image:

   ```text
   python <skill-directory>/scripts/generate.py --request-id "<task-id>" --prompt "<edit instructions>" --image "<path>" --n 1
   ```

   For masked local editing, add `--mask "<mask path>"`. Keep `n` at 1 unless the user explicitly requests variants; the maximum is 4.
9. Parse the JSON written to stdout. If stdout is empty after the process exits,
   read the exact current-task result sidecar described above; this is a result
   recovery step, not a retry. Require the returned `request_id` to match the
   current task ID. For every item in `images`, show the verified dimensions and
   format, render the image from `display_path`, provide the corresponding 1K/2K/4K
   original-file link, and state the absolute `path`.
10. If generation or editing fails, report the sanitized error. Never reveal, repeat, or inspect API keys in the response. Reject unsupported dimensions and missing input files locally without calling the API.

## Updating the Skill

When the user asks to `更新 Matrixapi-imagegen`, do not generate an image. Run:

```text
python <skill-directory>/scripts/update_skill.py
```

The updater downloads the latest public package from the fixed GitHub repository,
validates the archive, replaces only the Skill directory, preserves credentials stored
outside that directory, and rolls back if replacement fails. Report only whether the
update succeeded and tell the user to restart Codex. Do not use the system
`$skill-installer` for updates because it intentionally refuses an existing target.

## Configuration

This distribution is intentionally restricted to the MatrixAI site at
`https://eos.manyuvip.com/`. Other image API hosts are rejected by the script.

The script discovers credentials in this order:

1. `IMAGEGEN_API_KEY`, with optional `IMAGEGEN_BASE_URL` (defaults to MatrixAI) and `IMAGEGEN_MODEL`.
2. `OPENAI_API_KEY` and `OPENAI_BASE_URL`, only when the URL is MatrixAI.
3. The local `~/.codex/Matrixapi-imagegen.env` file, used by the one-click macOS installer.
4. The current Codex provider selected in CC Switch, only when it points to MatrixAI.

MatrixAI must implement `POST /v1/images/generations` for new images and `POST /v1/images/edits` with multipart `image`/optional `mask` fields for editing. Responses may return either `data[].url` or `data[].b64_json`. The default model is `gpt-image-2`; set `IMAGEGEN_MODEL` only when using another model exposed by MatrixAI.

The Skill leaves the response format unspecified by default so the relay can use
its fastest supported response. It accepts both `data[].url` and `data[].b64_json`.
Set `IMAGEGEN_RESPONSE_FORMAT=b64_json` or `url` only when the relay requires a
specific format; returned URLs and redirects must still remain on
`eos.manyuvip.com`.

To diagnose setup without generating or charging for an image, run:

```text
python <skill-directory>/scripts/generate.py --check-config
```

The check reports only whether a supported configuration was found, its generic source type, and the selected model. Do not print the provider name, endpoint, or credential.

## Boundaries

- This is a script-backed API workflow, not a native first-party image tool or Canvas integration.
- Use only the recipient's locally configured MatrixAI API access. Do not embed or request a private API key.
- Edit, reference-image, and mask support depends on MatrixAI accepting the OpenAI-compatible edits request. The script sends local files as multipart form data and does not upload them anywhere except `eos.manyuvip.com`.
- A mask should be an image file, normally a same-size PNG with transparency or the provider's documented mask convention. The provider remains responsible for its exact inpainting semantics.
- Do not claim that every image model supports every edit parameter; report the provider's sanitized error when it rejects a feature.
- Save results under the recipient's local Codex generated-images directory unless `--out-dir` is explicitly supplied.
