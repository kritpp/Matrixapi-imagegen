---
name: Matrixapi-imagegen
description: Generate or edit bitmap images through the recipient's own OpenAI-compatible Images API, or update this Matrixapi-imagegen Skill from its GitHub repository. Use for image generation, editing, retouching, inpainting, and requests such as 更新 Matrixapi-imagegen. Do not use for SVG/code-native graphics.
---

# Matrix API 图片生成与编辑

Generate or edit images with the bundled script and show the saved result to the user.

## Workflow

1. Resolve `scripts/generate.py` relative to this `SKILL.md` file.
2. Turn the user's request into a complete image prompt. Preserve requested subject, composition, style, lighting, colors, text, and constraints. Do not invent identifying details. When the user supplies a local image, pass it as `--image`; pass a supplied mask as `--mask`.
3. Choose the requested size, or use `1024x1024` when none is given. Common larger sizes are `2048x2048` for square 2K, `2048x1152` for landscape 2K, `3840x2160` for landscape 4K, and `2160x3840` for portrait 4K. Both edges must be multiples of 16, the longest edge must not exceed 3840 pixels or three times the shorter edge, and total pixels must be between 655,360 and 8,294,400.
4. Choose the request mode automatically: no `--image` uses `/v1/images/generations`; one or more `--image` files uses multipart `/v1/images/edits`; `--mask` is optional for edit/inpaint requests. Repeat `--image` for multiple reference images, up to seven. For edit requests above a 1792px long edge, the script automatically lowers the API edit size to a 16px-aligned equivalent ratio before sending it; text-only generation keeps the requested size. The edit response is therefore saved at the lower working size, and the original input file is never overwritten.
5. Run a generation:

   ```text
   python <skill-directory>/scripts/generate.py --prompt "<prompt>" --size <WIDTHxHEIGHT> --n 1
   ```

   For an edit with an original image:

   ```text
   python <skill-directory>/scripts/generate.py --prompt "<edit instructions>" --image "<path>" --n 1
   ```

   For masked local editing, add `--mask "<mask path>"`. Keep `n` at 1 unless the user explicitly requests variants; the maximum is 4.
6. Parse the JSON written to stdout. For each output, use the matching item in `image_info` to report the actual saved image dimensions, file format, and resolution label before rendering its preview. The script determines these values from the output file itself, not from the requested size. Render both the inline preview and a clickable original-image link, using the normalized absolute paths from `preview_files` and `download_files`:

   ```text
   已生成，尺寸经检查为 3840 x 2160，PNG 格式，画质为 4K。
   ![generated image](C:/.../image.png)
   [点击打开或下载 4K 原图](C:/.../image.png)
   ```

   Resolution labels use the output's longest edge: `4K` at 3840px or above, `2K` at 2048px or above, otherwise `1K`.
   Do not put Windows `files` paths containing `\\` into Markdown; reserve `files` for the native saved-path report. Never omit the clickable original-image link.

   For a new text-to-image request (no `--image`), the current command's stdout JSON is the only source of the result. Require `ok: true`, a non-empty `preview_files` entry, and an existing file at that exact returned path before rendering. Never scan, sort, or inspect the shared generated-images directory, choose the newest or first image, or use any path from an earlier request, conversation, or Codex installation. If the current JSON is missing, invalid, or does not point to an existing file, report the generation as failed and do not display a local image. Do not trigger a retry because a directory image looks wrong or does not match the prompt; preserve the existing API-error retry policy, and use only the returned JSON from the retry that actually succeeds. This rule applies only to new text-to-image results; edits and redraws with `--image` continue to use the user-supplied input image and the current command's returned output.
7. If generation or editing fails, report the sanitized error. If an edit was resized, include the requested size and actual edit size from the JSON fields. Never reveal, repeat, or inspect API keys in the response. Reject unsupported dimensions and missing input files locally without calling the API.

## Configuration

This distribution is intentionally pinned to the user's image API at
`https://eos.manyuvip.com/`. Other image API hosts are rejected by the script.

The script discovers credentials in this order:

1. `IMAGEGEN_API_KEY`, with optional `IMAGEGEN_BASE_URL` (defaults to the fixed API address) and `IMAGEGEN_MODEL`.
2. `OPENAI_API_KEY` and `OPENAI_BASE_URL`, only when the URL is the fixed API address.
3. The current Codex provider selected in CC Switch, only when it points to the fixed API address.

The API must implement `POST /v1/images/generations` for new images and `POST /v1/images/edits` with multipart `image`/optional `mask` fields for editing. Responses may return either `data[].url` or `data[].b64_json`. The default model is `gpt-image-2`; set `IMAGEGEN_MODEL` to use another model exposed by the API.

To diagnose setup without generating or charging for an image, run:

```text
python <skill-directory>/scripts/generate.py --check-config
```

The check reports only whether a supported configuration was found, its generic source type, and the selected model. Do not print the provider name, endpoint, or credential.

## Updating the Skill

When the user asks to `更新 Matrixapi-imagegen`, run:

```text
python <skill-directory>/scripts/update_skill.py
```

The updater downloads the latest package from the official GitHub repository and replaces this Skill directory while preserving credentials stored outside it. Restart Codex after a successful update.

## Boundaries

- This is a script-backed API workflow, not a native first-party image tool or Canvas integration.
- Use only the recipient's locally configured API access. Do not embed or request a distributor's private endpoint or key.
- Edit, reference-image, and mask support depends on the configured provider accepting the OpenAI-compatible edits request. The script sends local files as multipart form data and does not upload them anywhere except the configured API endpoint.
- A mask should be an image file, normally a same-size PNG with transparency or the provider's documented mask convention. The provider remains responsible for its exact inpainting semantics.
- Do not claim that every image model supports every edit parameter; report the provider's sanitized error when it rejects a feature.
- Save results under the recipient's local Codex generated-images directory unless `--out-dir` is explicitly supplied.
