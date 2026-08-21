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
- After success, display every returned file with an absolute-path Markdown image and
  state `图片已保存至：<absolute path>`. On failure, show only the sanitized error.
- Do not claim that this external Skill can create Codex's native image-generation
  shimmer card; that UI belongs to Codex itself.

## Workflow

1. Resolve `scripts/generate.py` relative to this `SKILL.md` file.
2. Turn the user's request into a complete image prompt. Preserve requested subject, composition, style, lighting, colors, text, and constraints. Do not invent identifying details. When the user supplies a local image, pass it as `--image`; pass a supplied mask as `--mask`.
3. Choose the requested size, or use `1024x1024` when none is given. Common larger sizes are `2048x2048` for square 2K, `2048x1152` for landscape 2K, `3840x2160` for landscape 4K, and `2160x3840` for portrait 4K. Both edges must be multiples of 16, the longest edge must not exceed 3840 pixels or three times the shorter edge, and total pixels must be between 655,360 and 8,294,400.
4. Choose the request mode automatically: no `--image` uses `/v1/images/generations`; one or more `--image` files uses multipart `/v1/images/edits`; `--mask` is optional for edit/inpaint requests. Repeat `--image` for multiple reference images, up to seven.
5. Use the fast path by default: run one request with `--n 1`, without OCR, post-processing, automatic retries, or configuration checks.
6. If the user explicitly says `精准文字`, add `--quality high`. If the user explicitly says `精准重绘`, add `--quality high --input-fidelity high`. These opt-in paths may be slower; do not enable them for ordinary requests.
7. For a follow-up such as `把刚才的图片...`, reuse the exact last generated absolute path from the current conversation as `--image`. Preserve the current image, mask, size, composition, and style; replace only the requested change. If the user supplies a new image, start a new edit context. Never silently turn a follow-up edit into text-to-image generation.
8. Run a generation:

   ```text
   python <skill-directory>/scripts/generate.py --prompt "<prompt>" --size <WIDTHxHEIGHT> --n 1
   ```

   For an edit with an original image:

   ```text
   python <skill-directory>/scripts/generate.py --prompt "<edit instructions>" --image "<path>" --n 1
   ```

   For masked local editing, add `--mask "<mask path>"`. Keep `n` at 1 unless the user explicitly requests variants; the maximum is 4.
9. Parse the JSON written to stdout. For every path in `files`, display the image with Markdown using the absolute local path, then state the saved path briefly.
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
