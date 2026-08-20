---
name: Matrixapi-imagegen
description: Generate or edit bitmap images through the recipient's own OpenAI-compatible Images API, including local reference images and masks. Use for requests to generate, create, draw, edit, retouch, inpaint, or make a picture, illustration, poster, wallpaper, concept art, or other raster image, including Chinese prompts such as 生成图片, 画一张图, 生图, 修改这张图, and 局部重绘. Do not use for SVG/code-native graphics.
---

# API Image Generation

Generate or edit images with the bundled script and show the saved result to the user.

## Workflow

1. Resolve `scripts/generate.py` relative to this `SKILL.md` file.
2. Turn the user's request into a complete image prompt. Preserve requested subject, composition, style, lighting, colors, text, and constraints. Do not invent identifying details. When the user supplies a local image, pass it as `--image`; pass a supplied mask as `--mask`.
3. Choose the requested size, or use `1024x1024` when none is given. Common larger sizes are `2048x2048` for square 2K, `2048x1152` for landscape 2K, `3840x2160` for landscape 4K, and `2160x3840` for portrait 4K. Both edges must be multiples of 16, the longest edge must not exceed 3840 pixels or three times the shorter edge, and total pixels must be between 655,360 and 8,294,400.
4. Choose the request mode automatically: no `--image` uses `/v1/images/generations`; one or more `--image` files uses multipart `/v1/images/edits`; `--mask` is optional for edit/inpaint requests. Repeat `--image` for multiple reference images, up to seven.
5. Run a generation:

   ```text
   python <skill-directory>/scripts/generate.py --prompt "<prompt>" --size <WIDTHxHEIGHT> --n 1
   ```

   For an edit with an original image:

   ```text
   python <skill-directory>/scripts/generate.py --prompt "<edit instructions>" --image "<path>" --n 1
   ```

   For masked local editing, add `--mask "<mask path>"`. Keep `n` at 1 unless the user explicitly requests variants; the maximum is 4.
6. Parse the JSON written to stdout. For every path in `files`, display the image with Markdown using the absolute local path, then state the saved path briefly.
7. If generation or editing fails, report the sanitized error. Never reveal, repeat, or inspect API keys in the response. Reject unsupported dimensions and missing input files locally without calling the API.

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
