---
name: Matrixapi-imagegen
description: Generate or edit bitmap images through the recipient's own OpenAI-compatible Images API, perform deterministic local image processing, or update the Matrixapi-imagegen Skill from its fixed GitHub repository. Use for image generation/editing requests and explicit requests such as 更新 Matrixapi-imagegen. Do not use for SVG/code-native graphics.
---

# Matrixapi Image Generation

Generate or edit images with the bundled script and show the saved result to the user.

## Workflow

1. Resolve `scripts/generate.py` relative to this `SKILL.md` file.
2. Turn the user's request into a complete but concise image prompt, preferably below 900 characters. The pinned GPT Image 2 and GPT Image 2 Pro model limit is 1024 characters (characters, not tokens; Chinese characters count as one character). Preserve requested subject, composition, style, lighting, colors, text, and constraints. Do not invent identifying details. When the user supplies a local image, pass it as `--image` and treat it as the primary whole-scene reference. For ordinary edits, write the prompt to preserve the original composition and untouched areas, then require new content to match perspective, scale, light direction, color temperature, shadows, occlusion, depth of field, and texture; explicitly forbid pasted, sticker-like, cutout, or hard-edge results. Do not add `--mask` unless the user explicitly requests precise masked editing and the configured model channel is confirmed to support it. The script automatically compacts an overlong prompt to the 1024-character limit before submitting it, so do not manually retry after a local length error. On Windows, when the prompt contains quotes, apostrophes, newlines, or is long, pass it through `--prompt-file <UTF-8 file>` instead of embedding it in a PowerShell command; this prevents local shell parsing errors and does not add an API request.
3. Choose the requested size. The preferred GPT Image 2 values are `1K`, `2K`, and `4K`; pixel sizes such as `2048x1152` remain accepted for legacy relays. Use `--aspect-ratio auto|1:1|3:2|2:3` with size aliases. Both pixel edges must be multiples of 16, the longest edge must not exceed 3840 pixels or three times the shorter edge, and large 4K portrait/landscape requests may exceed the old 8.3MP limit. For a local `--image` edit with `--aspect-ratio auto` (the default), the script preserves the first input image's real geometry by scaling its dimensions to the selected 1K/2K/4K tier and omitting a forced enum ratio. This prevents wide banners and other non-standard layouts from silently becoming `3:2`; an explicit `--aspect-ratio` always wins.
   If the user asks for an arbitrary final pixel size (for example `1179x2556` or `1920x1080`), do not send that arbitrary size to the model. Select a valid model source tier/aspect ratio, then pass `--output-size WIDTHxHEIGHT` so the local deterministic post-processing stage produces the exact final dimensions. When `--output-size` is present with `--aspect-ratio auto`, its ratio takes precedence over input-image inference. Model ratios are limited to the three values above; use `--output-size` when exact original pixel dimensions are required.
4. Choose quality from `auto`, `low`, `medium`, and `high`. Map "草稿/快速预览" to `low`, "标准" to `medium`, "最终/细节丰富" to `high`, and omit a preference or use `auto` when the model should decide. The model price is per request; quality changes latency/detail rather than the configured per-request price.
5. Choose the request mode automatically: no reference image uses `/v1/images/generations`; local `--image` files use the relay's multipart `/v1/images/edits`; public `--reference-url` values use the GPT Image JSON `images` array on `/v1/images/generations`. On the pinned relay, local GPT Image 2 files are staged as short-lived reusable HTTPS URLs and forwarded through the documented JSON `images` flow when the model service does not accept inbound multipart media. The URL remains available during async validation and processing, then expires automatically. Up to 16 references are accepted for the pinned GPT Image 2 model. Do not mix local files and URL references in one request. For this relay, local `mask` is disabled by default for `gpt-image-2` and `gpt-image-2-pro`; the script rejects it locally before upload so a known-unsupported request cannot fail after billing. Do not treat a mask as an ordinary reference image.
   When the user says "edit the image above", "edit the previous image", or "edit the image just generated", reuse only the exact prior `download_files` or `files` path returned by the immediately preceding successful command in this same conversation and pass it as `--image`. Never scan an output, temporary, clipboard, download, or generated-images directory to guess the prior image. Never silently change an edit request into a new generation: if that exact path is unavailable in the conversation, report the missing path and stop. When the user instead asks to generate a new/different image, omit `--image` and `--reference-url`; the earlier editable image must not affect the new generation.
6. For `gpt-image-2-pro`, URL-reference edits preserve native `1K/2K/4K` sizing. Local GPT Image 2 edits on the pinned relay also preserve the requested native size through its temporary URL conversion. Other relays may still require a public reference URL or an enabled multipart upload capability. Set `IMAGEGEN_LEGACY_EDIT_RESIZE=1` only when an older relay rejects high-resolution edits; this intentionally changes the output size and is reported in the JSON result. The JSON result includes `aspect_ratio_source` (`input_image`, `output_size`, `user`, or `model_default`) so the selected ratio is visible.
7. Run a generation:

   ```text
   python <skill-directory>/scripts/generate.py --task-id "task-<fresh-unique-id>" --model gpt-image-2 --prompt "<prompt>" --size 4K --aspect-ratio 2:3 --quality high
   ```

   For an edit with an original image:

   ```text
   python <skill-directory>/scripts/generate.py --task-id "task-<fresh-unique-id>" --model gpt-image-2 --prompt "<edit instructions>" --image "<exact prior result path>" --size 4K --quality high
   ```

   For URL-based reference editing, repeat `--reference-url "https://..."` up to 16 times instead of using `--image`. For masked local editing, add `--mask "<mask path>"` only after the model channel has confirmed mask support. On the pinned relay, use ordinary reference editing instead; describe the target area and blending constraints in the prompt. Keep `n` at 1 unless the user explicitly requests variants; the maximum is 4.
   Generate a fresh `--task-id` before every command and never reuse it. Reference images are scoped to the current request, not the whole conversation. Do not pass every image mentioned earlier. For a new page or variant, use the explicitly requested current-turn references and, only when the request is an edit, the exact latest generated output from this conversation; do not append older generated 4K outputs to the next request. For a repeated edit, replace the previous input set with the exact latest output unless the user explicitly asks to keep another reference.
   For GPT Image 2 and GPT Image 2 Pro, the script omits the generic `n=1` field from the outbound request because the pinned model does not guarantee that parameter.
   For long 2K/4K generation requests or URL-reference edits, add `--async`; the script submits the task and polls `/v1/status/{task_id}`. Local GPT Image 2 edits keep native 1K/2K/4K pixels. When a local 2K/4K edit has at least 6 references or the combined source files are at least 48 MiB, the script automatically uses the relay's async JSON-reference path; smaller edits stay synchronous. This avoids losing a long synchronous response after the model has completed the billed task. The automatic path does not downscale or resubmit on timeout, and a failed task is reported once. The relay rejects a local multipart request above the 192 MiB safety threshold before sending it. Add `--stream` only when the caller can consume SSE events. `--webhook URL` and `--metadata '{"order_id":"..."}'` require explicit `--async`.

   For a request for two or more connected comic/story pages, use fast sequential story mode. Do not first narrate, outline, inspect the references again, or compose separate page prompts. The first action after resolving the user's reference paths and requested settings must be one `generate.py` command with the complete story request unchanged in `--prompt`, the requested count in `--story-pages`, and all current-turn references. Omit `--n`; the script generates exactly one page per command, uses `2:3` when the user did not choose a ratio, and enables async delivery. Example:

   ```text
   python <skill-directory>/scripts/generate.py --task-id "task-<fresh-unique-id>" --story-pages 3 --prompt "<complete story request>" --image "<reference-1>" --image "<reference-2>" --size 4K --quality high
   ```

   Page 1 uses the complete original reference set. The script persists the story request and continuity state; page 2 uses only page 1's original output, page 3 uses only page 2, and later pages continue the same way. After each success, immediately render that page from `preview_files` and its original link from `download_files` before doing anything else. If `story.status` is `active`, invoke the same script again using exactly the ordered strings in `story.next_arguments`; do not rewrite the prompt, select a reference yourself, scan a directory, or add another option. If a page fails, stop: the state deliberately supplies no next command and the failed page cannot be submitted again automatically. Never use `--n` to represent story pages because variants do not provide sequential continuity.
8. Apply local deterministic post-processing only when the user requests a final size, crop, output format, compression, or derived variants. The local stage may resize, crop, fit to a canvas, convert PNG/JPEG/WebP/AVIF, compress, and write a manifest; it must not install or invoke a local ML model. Use `--output-size`, `--fit cover|contain|fill|inside|outside`, `--position`, `--crop`, `--output-format`, `--output-quality`, and `--output-background` as needed. Preserve the unmodified model result in `original_files` for future edits, and use `processed_files` for the requested deliverable. For an existing image with no API request, use `--process-only --image <path>` with the same local options.
9. Parse only the JSON written to stdout by the current command. Accept it only when `ok` is true, its `task_id` exactly equals the command's `--task-id`, `result_match.task_id` matches it, and `completed_at_ms` is not earlier than `request_started_at_ms`. Use its `preview_files` immediately; do not open or scan the output directory, search for a newer file, reread the result JSON with another shell command, inspect dimensions, or run another verification command. The script atomically writes that same task-scoped JSON and, on Windows, schedules it to become hidden after stdout has been delivered; this background hide does not delay command completion. For each output, render both the inline preview and a clickable original-image link, using the normalized absolute paths from `preview_files` and `download_files`:
   `![generated image](C:/.../image.png)`
   `[点击打开或下载原图](C:/.../image.png)`
   Do not put Windows `files` paths containing `\\` into Markdown; reserve `files` for the native saved-path report. Never omit the clickable original-image link.
10. If generation or editing fails, report the sanitized error. Classify the failure as `content_policy` only when the model response explicitly mentions copyright, trademark, safety, moderation, disallowed content, or equivalent Chinese wording. Classify model/channel mapping failures separately. For a generic relay response such as HTTP 400 `request failed` / `bad_response_status_code`, say clearly that the cause is unknown and cannot be confirmed as copyright from that response alone; do not claim that the named character caused the failure. Include `model`, `quality`, requested size, actual edit size, whether the edit was resized, whether `prompt_compacted` was true, and any local post-processing status from the JSON fields. If model generation succeeds but local processing fails, return the original file and the local error; never repeat the billed API request automatically. Never reveal, repeat, or inspect API keys in the response. Reject unsupported model dimensions and missing input files locally without calling the API.

   User-visible wording: say “模型”, “模型服务”, or “中转站” for normal status and errors. Say “模型明确拒绝” only when the response explicitly contains a content, copyright, trademark, safety, moderation, or other policy refusal; a generic 400 is an unknown error.

## Reference Editing Strategy

Use semantic whole-image reference editing by default. The model should see the
complete source scene so it can rebuild the requested change with matching light,
perspective, shadows, depth, and material texture. A mask is a stricter boundary,
not automatically a higher-quality result: use it only when the user needs a
precise edit region and the model service documents mask support. If the current
channel does not support masks, do not retry with a mask or upload the mask as a
reference; explain the limitation and use the full-image reference path instead.

For a local edit, a good prompt shape is:

```text
Use the input image as the primary scene reference. Preserve the composition,
camera angle, untouched subjects, lighting, perspective, and environment. Change
only [requested change]. Integrate the result with matching scale, light direction,
color temperature, shadows, occlusion, depth of field, and texture. No pasted,
sticker-like, cutout, or hard-edge result.
```

## Local Asset Processing and Model Boundary

The Skill combines two deliberately separate stages:

- **Configured model stage:** generation, semantic editing, inpainting, background removal, object isolation, outpainting, restoration, and any other operation that needs visual understanding. Use the configured image model/API. Do not download Hugging Face, Torch, RMBG, BiRefNet, or another local ML model.
- **Local deterministic stage:** exact resizing, crop/fit, canvas padding, format conversion, compression, multi-size derivation, and GIF encoding from already separated frames. These operations must not call the image API and must not change semantic content.

If the configured model does not expose a model-backed operation, report that limitation instead of installing a local model or silently replacing it with a prompt-only approximation. Local processing is allowed after a model result and is never a substitute for a failed model request.

## Named Characters and Rights-Sensitive Requests

- Treat a request for a named franchise character, logo, trademark, or recognizable protected costume as potentially restricted by the model's content policy.
- Do not bypass a refusal by obfuscating the name, switching languages, using spelling variants, describing the same character indirectly, or retrying with hidden visual cues. Do not claim that a generic HTTP 400 proves copyright when the relay has not returned a policy reason.
- If a neutral control prompt succeeds but the named-character request repeatedly fails, report that the behavior is consistent with a model content/rights restriction, while noting that the relay did not expose a definitive reason. Do not spend additional paid retries on the same refused request.
- Do not silently replace the requested character. Offer two explicit paths: use a provider-supported/licensed reference if the provider accepts it, or generate a clearly original character with distinct name, emblem, costume, colors, and silhouette after the user agrees. An original superhero archetype is not the requested franchise character.
- If the user agrees to an original alternative, rewrite the prompt to remove the protected name, trademarked emblem, franchise-specific costume, and distinctive identifying details before sending it to the model. Keep the result clearly labeled as an original character.

Examples:

```text
python <skill-directory>/scripts/generate.py --model gpt-image-2 \
  --prompt "..." --size 4K --aspect-ratio auto \
  --output-size 1920x1080 --fit cover --output-format webp --output-quality 88
```

```text
python <skill-directory>/scripts/generate.py --process-only \
  --image "C:/path/to/image.png" --output-size 1179x2556 \
  --fit cover --position center
```

## Configuration

This distribution is intentionally pinned to the user's image relay at
`https://matrixapii.com/`. Other image API hosts are rejected by the script.

The script discovers credentials in this order:

1. `IMAGEGEN_API_KEY`, with optional `IMAGEGEN_MODEL`. The API URL is compiled into the Skill; `IMAGEGEN_BASE_URL` is not required and cannot override it.
2. `OPENAI_API_KEY` and `OPENAI_BASE_URL`, only when the URL is the pinned relay.
3. The current Codex provider selected in CC Switch, only when it points to the pinned relay.

The pinned relay must implement `POST /v1/images/generations` for JSON generation, URL-reference editing, async tasks, and SSE; it should also keep `POST /v1/images/edits` with multipart `image`/optional `mask` fields for local-file compatibility. For GPT Image 2 models, the pinned relay can convert local image parts to short-lived reusable URLs before calling the model JSON endpoint. Local mask editing is disabled by default for the pinned GPT Image 2 channels because their documented interface does not guarantee `mask`. Only after the relay confirms support may `IMAGEGEN_MASK_SUPPORT=1` be set for a request. Responses may return either `data[].url` or `data[].b64_json`. The default model is `gpt-image-2`; set `IMAGEGEN_MODEL=gpt-image-2-pro` or pass `--model gpt-image-2-pro` only when Pro is explicitly requested.

For native GPT Image 2 4K editing, leave `IMAGEGEN_LEGACY_EDIT_RESIZE` unset. Set it to `1` only for a relay that cannot accept native 4K multipart edits; this intentionally changes the working size and is reported in the JSON result.

To diagnose setup without generating or charging for an image, run:

```text
python <skill-directory>/scripts/generate.py --check-config
```

The check reports only whether a supported configuration was found, its generic source type, and the selected model. Do not print the provider name, endpoint, or credential.

## Updating

### Duplicate-charge protection

Each API request is keyed by a deterministic fingerprint containing the current
Codex thread, prompt, model, mode, size, quality, count, and the ordered
reference-image content. A completed identical request is returned from its
local result record without another API call. If a prior request was submitted
but its final state is unknown, the identical request is blocked to prevent a
second charge. Only an explicit `--force-new` retry can override that guard.

When the user explicitly asks to update Matrixapi-imagegen, run exactly once:

```text
python <skill-directory>/scripts/update_skill.py
```

The updater downloads the highest-versioned `Matrixapi-imagegen-vX.Y.Z.zip` from
the fixed public GitHub repository. It validates archive paths, file/size limits,
the package filename against `SKILL_VERSION`, the fixed Matrixapi URL, and all
required Skill files before replacing anything. It holds an update lock and keeps
the installed Skill as a rollback backup until the new version passes
`--check-config`. A failed validation restores the previous version. It does not
touch generated images, `IMAGEGEN_API_KEY`, `IMAGEGEN_MODEL`, or the local
`Matrixapi-imagegen.env` file, and it never calls the image generation API.

After a successful update, show the updater's `display_message` exactly, including
the installed version, current model, supported models, and requirement to restart
Codex. Do not run the updater more than once for the same user request.

## Boundaries

- This is a script-backed API workflow, not a native first-party image tool or Canvas integration.
- Use only the recipient's locally configured API access. Do not embed or request a distributor's private endpoint or key.
- Edit, reference-image, and mask support depends on the configured model service accepting the OpenAI-compatible edits request. The script sends local files as multipart form data and does not upload them anywhere except the configured API endpoint. Large GPT Image 2 local edits are staged by the pinned relay as short-lived HTTPS references before the async model request. The pinned GPT Image 2 mask path is rejected locally unless `IMAGEGEN_MASK_SUPPORT=1` is explicitly enabled after capability confirmation.
- Local output processing uses Pillow only for deterministic pixel operations. It does not download or run a local image-understanding model. Model-backed operations must remain model-backed.
- When mask support is confirmed, a mask should be an image file, normally a same-size PNG with transparency or the provider's documented mask convention. The provider remains responsible for its exact inpainting semantics.
- Do not claim that every image model supports every edit parameter; report the provider's sanitized error when it rejects a feature.
- Save results under the recipient's local Codex `generated_images/Matrixapi-imagegen` directory unless `--out-dir` is explicitly supplied.
