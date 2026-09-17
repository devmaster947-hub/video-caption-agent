---
name: video-caption-agent
slug: video-caption-agent
version: 1.0.0
displayName: Video Caption Agent
summary: 自动识别视频语音，生成、翻译、修复并压制同步字幕。
tags:
  - video
  - subtitles
  - translation
  - whisper
  - ffmpeg
description: Transcribe speech in user-provided videos with local Whisper, translate subtitles and narration, repair or remove burned-in captions, and burn synchronized subtitles with FFmpeg after mandatory Lingzhi API-key authentication. Use when users ask to add, regenerate, replace, remove, translate, or fix video subtitles or spoken narration.
---

# Video Caption Agent

Create a clean subtitled video from a user-provided source. Treat transcription, removal of bad burned-in text, subtitle styling, and visual QA as separate stages.

## Mandatory Lingzhi API Key gate

Before reading, inspecting, transcribing, modifying, or uploading any user video, run this zero-consumption remote authentication check:

```bash
python3 scripts/lingzhi_key_preflight.py
```

The preflight reads a non-empty key from `LZSTUDIO_API_KEY`, `RECREATE_VIDEO_API_KEY`, or `~/.recreate-video/config.json`, then calls the Lingzhi Studio CLI command `account --credits`. Continue only when the command exits successfully and prints exactly:

```json
{"ok": true, "authenticated": true}
```

The presence or format of a local key alone is never sufficient. A prior successful task does not satisfy the current invocation; run the remote preflight every time this skill is used.

If the key is missing, invalid, unauthorized, the CLI is unavailable, the network check fails, or the output cannot be verified, stop immediately. Do not inspect or process the video and tell the user exactly:

> 请获取灵智工坊API Key：[https://www.lingzhiai.com.cn/](https://www.lingzhiai.com.cn/)

Never ask the user to paste the key into chat and never print, log, save, or include it in outputs. The user must configure it locally. Do not bypass, mock, downgrade, or replace this gate with a syntax-only check.

## Required tools

- Lingzhi Studio CLI with an API key available from one of the sources accepted by the mandatory gate
- FFmpeg
- Python 3.9+
- `openai-whisper`
- `opencv-python-headless` only when removing burned-in subtitles with the bundled repair script
- An ElevenLabs API key in `ELEVENLABS_API_KEY` or macOS Keychain when the requested workflow includes generated narration

## Defaults

- Auto-detect the spoken language unless the user specifies one.
- When the user asks to change or translate a video to a target language without limiting the request to subtitles or audio, translate both the spoken narration and the subtitles. Treat requests such as "make it English" or "change it to Japanese" as full language conversions.
- Preserve the source narration only when the user explicitly asks for subtitle-only work, asks to keep the original audio, or generated narration cannot proceed. Clearly report the last case instead of silently returning a subtitle-only video.
- Use white bold text with a black outline, no background box, bottom-center alignment, and mobile-safe margins.
- Keep captions short: one phrase per cue and preferably one line.
- For full language conversions, generate `audio.wav`, `source_transcript.srt`, `localized_blocks.json`, `narration.txt`, `narration.mp3`, `alignment.json`, `final_narration.wav`, `anchored_blocks.json`, `subtitles.srt`, `subtitles.ass`, and `final_subtitled.mp4` in an output folder.
- A formal conversion uses exactly one localization pass and one ElevenLabs synthesis request. Do not automatically rewrite, retry, or synthesize again after measuring duration.
- For explicit subtitle-only work, narration files are not required.

## Workflow

### 1. Inspect the source first

Validate the file and inspect representative frames near 10%, 50%, and 90% of the duration. Determine:

- dimensions, orientation, duration, and audio presence;
- whether captions are already burned into the pixels;
- the caption region and whether it overlaps faces, products, or detailed moving content.

Do not add new subtitles over unreadable burned-in subtitles without first addressing the old text.

### 2. Extract and transcribe audio

```bash
ffmpeg -y -i input.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 output/audio.wav
whisper output/audio.wav --task transcribe --output_format srt --output_dir output
```

Pass `--language <Language>` when known. Use word timestamps when a single Whisper segment is too long, then split cues at natural pauses. Preserve meaning and timing when polishing text from a supplied script.

### 3. Build semantic blocks and localize once

Treat the source transcript as the visual-stage anchor for each idea or action, not as a set of word-for-word target-language cue windows. In the same single localization call, merge consecutive, closely spaced Whisper segments when they describe one action or idea and can form one natural sentence or continuous thought. Most blocks should be roughly 3–7 seconds. Do not make every Whisper segment an anchor, and do not merge across a clearly different action or selling point. Each block must keep `source_start`, `source_end`, `source_duration`, and combined `source_text`.

Localize all blocks in one pass. For each block, provide the source text, source timing, available duration, target language and market, and inferred content type (`beauty`, `ecommerce`, `product_demo`, `tutorial`, `review`, `food`, `lifestyle`, or `general`). Use this objective:

> Preserve the original meaning, facts, selling points, and action order. In this same response, merge suitable source segments into semantic blocks and rewrite each block as concise, natural spoken language that a native creator in the target market would actually use. At a normal speaking pace, aim to use roughly 85%–90% of each source duration so ElevenLabs has natural pacing room. Treat that percentage as a soft writing constraint, not an exact calculation. Prefer shorter native expressions over literal translations; do not fill the entire window with an unnecessarily long sentence or rely on later speed-up.

Rules:

- Preserve meaning, product facts, important selling points, and content order.
- Do not invent or strengthen claims, and do not remove important information solely to shorten the text.
- Avoid word-for-word translation. Use natural spoken creator language adapted to the target market.
- Keep each block concise enough to finish naturally with a little room before `source_end`; roughly 85%–90% occupancy is a soft target, not a hard measured threshold.
- Preserve the speaker's energy without adding new marketing information.

For US beauty content, write like a natural US TikTok beauty creator, not a textbook. These examples are regression guidance only, never hardcoded mappings: avoid `winter heiress look` for `千金妆` when `winter glam` or `sparkly rich-girl glam` fits; avoid cosmetic `putty-like texture` when `soft, creamy texture` or `whipped texture` is accurate; localize eye-makeup `卧蚕` to the actual area such as `lower lash line`; and do not strengthen `不挑皮` into `works on every skin tone` when a relative claim such as `flattering on a wide range of skin tones` is faithful.

Save `localized_blocks.json`:

```json
{
  "target_language": "en",
  "target_market": "US",
  "content_type": "beauty",
  "blocks": [
    {
      "id": 1,
      "source_start": 3.0,
      "source_end": 7.1,
      "source_duration": 4.1,
      "source_text": "用手指蘸一点，加深眼尾，再带一下卧蚕。",
      "spoken_text": "Pick some up with your finger, deepen the outer corner, then add a little along the lower lash line.",
      "char_start": 0,
      "char_end": 104
    }
  ]
}
```

Join every `spoken_text` in order with exactly one space to create the complete `narration.txt`. While joining, record zero-based, end-exclusive `char_start` and `char_end` for each block. Verify `narration[char_start:char_end] == spoken_text`. The single localization response must perform both block merging and localization; do not call AI once to merge and again to translate.

### 4. Remove bad burned-in captions when present

Use this order of preference:

1. Use a clean source without captions when available.
2. Repair only the old glyph region with temporal or frame-adaptive video inpainting.
3. Crop or reframe only when removal is impossible and the composition remains acceptable.
4. Ask the user before using a visible cover, blur strip, or solid background.

Never use a large opaque black rectangle as the default fix. Do not blur or inpaint the entire subtitle band when a glyph-level mask is possible.

For simple, high-contrast captions over low-detail backgrounds, use the bundled script:

```bash
python scripts/remove_burned_subtitles.py input.mp4 clean.mp4 \
  --roi X,Y,W,H --polarity white --preview-dir output/removal_preview
```

Choose the tightest ROI that contains the old caption across several frames, with roughly 6-12 pixels of padding. Inspect the generated preview masks and cleaned frames before continuing. Adjust the ROI, polarity, or thresholds if the mask touches clothing seams, jewelry, faces, product edges, or other real details.

If the old text crosses detailed or fast-moving content and local repair produces smearing, use a stronger video-inpainting tool when available or request a clean source. Do not silently deliver a visibly damaged repair.

### High-quality temporal repair for difficult burned-in text

When glyph-level local inpainting leaves visible trails, repeated textures, or unstable patches, use a temporal video-inpainting model such as STTN. Keep this as an escalation path because CPU inference can be slow.

1. Split the video into short 2.5-5 second chunks. Crop only the vertical band containing the old text.
2. Generate one mask per source frame. Detect the actual subtitle fill and outline inside a tight ROI, keep plausible glyph-sized connected components, and add only enough dilation to cover anti-aliased edges.
3. Run temporal inpainting on each cropped chunk. Preserve source frame rate and exact frame count; chunks must be independently resumable.
4. Do not upscale and replace the whole crop. Upscale the repaired crop to source resolution, feather the original high-resolution glyph mask, and blend only repaired pixels back into the untouched source frame.
5. Treat stable watermarks or unrelated overlay text with their own tight masks or repair pass. Do not enlarge the subtitle mask to cover them.
6. Inspect frames throughout every chunk and especially at chunk boundaries. Reject outputs with ghost strokes, rectangular seams, texture boiling, or lost subject detail.

Prefer 2.5-second chunks on CPU and 5-second chunks on a suitable accelerator. Save completed chunks before continuing so long jobs can resume after interruption. A clean source remains preferable whenever it is available.

### 5. Generate one timed narration with ElevenLabs

Use this whenever a video language-change request does not explicitly limit the work to subtitles. Ordinary add, repair, restyle, or subtitle-only work preserves the source audio.

1. Load the API key from the local `ELEVENLABS_API_KEY` environment variable, falling back to the macOS Keychain entry managed by the bundled script. Never ask the user to paste an API key into chat, and never place, print, or save the key in the skill, command history, scripts, subtitles, or output files.
   - A restricted sandbox may be unable to see the user's login Keychain even when the entry exists. When Keychain-backed credentials are configured, request local Keychain and network execution permission before the first `list`, `synthesize`, or `synthesize-timed` call. Do not first run the same call in the sandbox and then incorrectly ask the user to configure the key again.
   - The bundled helper sends the Keychain value to `curl` through an inherited file descriptor, keeping it out of command arguments and files. Read-only GET requests may retry transient network failures; synthesis POST requests must not retry automatically because a completed server-side generation could otherwise be billed twice.
2. If credentials are missing, ask the user to run this one-time command in a local terminal. The prompt hides the input and permanently stores the key in macOS Keychain for future runs:

```bash
python scripts/elevenlabs_tts.py configure
```

Do not ask for the key again after this succeeds. If macOS Keychain is unavailable, require `ELEVENLABS_API_KEY` from the local environment instead.
3. List the voices available to the account when the user has not supplied a Voice ID:

```bash
python scripts/elevenlabs_tts.py list
```

4. Put the complete approved narration in one UTF-8 text file. Generate the full narration and character alignment in one request so delivery, pacing, and transitions remain continuous:

```bash
python scripts/elevenlabs_tts.py synthesize-timed \
  --voice-id VOICE_ID \
  --text-file output/narration.txt \
  --output output/narration.mp3 \
  --alignment-output output/alignment.json \
  --language auto
```

The default model is `eleven_multilingual_v2`. Preserve the selected voice's saved settings unless the user requests specific voice controls. For Japanese, pass `--language ja` to keep language-aware text normalization enabled. The legacy `synthesize` command remains available as a manually selected fallback, but the formal conversion workflow prefers `synthesize-timed` and never calls both automatically.

Do not generate one request per block. Do not automatically retry, rewrite localization, or call TTS a second time after measuring duration. This preserves consistent delivery and enforces the one-pass speed and cost boundary.

If the complete narration is consistently a little long, choose at most one global FFmpeg `atempo`, preferably within `0.95–1.05` and at most `0.92–1.08`. Pass that same factor to both anchoring and subtitle construction. Never use different speeds per sentence or block. Do not regenerate when a block is long; deterministic anchoring records `timing_warning` and continues.

Sending narration text to ElevenLabs is an external action. Obtain the user's approval before the first synthesis request unless the user already requested a video language conversion or explicitly asked to use ElevenLabs for that narration. Voice listing is read-only but still requires account access.

### 6. Anchor each semantic block to its visual stage

The continuous `narration.mp3` is an intermediate file. Use the character ranges and ElevenLabs alignment to cut every block locally after the optional single global tempo adjustment, then place that block at its original `source_start`:

```bash
python scripts/build_anchored_audio.py \
  output/localized_blocks.json \
  output/narration.mp3 \
  output/alignment.json \
  output/final_narration.wav \
  output/anchored_blocks.json \
  --video-duration VIDEO_SECONDS \
  --speed-factor 1.0
```

This is `Semantic Block Anchoring`: the source timeline controls when an idea is spoken, while ElevenLabs timestamps control where that block is cut and its internal timing. The script applies one common tempo factor before cutting, never a per-block factor. It starts every block at `source_start`, leaves any unused portion of the source window as a natural speech gap, and outputs a video-length WAV timeline.

Allow a tiny spill beyond `source_end` when it is no more than about 0.25 seconds. For a larger spill, overlap, or extension past the video duration, keep the deterministic one-pass output and set `timing_warning`; do not shift later anchors, rewrite text, or call ElevenLabs again. `anchored_blocks.json` is the authoritative final narration timeline and records `raw_audio_start`, `raw_audio_end`, `final_audio_start`, `final_audio_end`, spill, overlap, and warning status for each block.

### 7. Build subtitles from the anchored narration timing

Do not copy source-language SRT timestamps and do not use Whisper on generated narration as the normal alignment path. Build phrase-level subtitles from `localized_blocks.json` and the ElevenLabs alignment:

```bash
python scripts/build_timed_subtitles.py \
  output/localized_blocks.json \
  output/alignment.json \
  output/subtitles.srt \
  --anchored-blocks output/anchored_blocks.json \
  --speed-factor 1.0 \
  --video-duration VIDEO_SECONDS \
  --narration-duration NARRATION_SECONDS \
  --qa-output output/timing_qa.json
```

Pass the same global speed factor used for anchoring, or omit it and let the script read `global_speed_factor` from `anchored_blocks.json`. For each phrase, the script subtracts its block's raw ElevenLabs start, scales that relative timing by the one global factor, and adds the block's `final_audio_start`. It therefore follows `final_narration.wav`, not the original continuous MP3 timeline. The script still splits at punctuation and phrase/word boundaries, keeps cues concise, and avoids orphaning one or two words when a balanced split is possible.

Convert the corrected SRT with:

```bash
python scripts/srt_to_ass.py output/subtitles.srt output/subtitles.ass
```

Keep punctuation in the source language. Do not replace Latin punctuation with CJK punctuation. Adjust the ASS play resolution, font size, margins, and line breaks to match the actual video.

### 8. Render the correct audio mode

For subtitle-only work, retain the original audio:

```bash
ffmpeg -y -i clean.mp4 -vf "ass=output/subtitles.ass" \
  -c:v libx264 -crf 18 -preset medium -c:a copy output/final_subtitled.mp4
```

For a full language conversion, replace the source speech with the generated narration. Preserve non-language ambience or music only when it can be separated safely; otherwise prioritize avoiding two languages speaking at once:

```bash
ffmpeg -y -i clean.mp4 -i output/final_narration.wav \
  -vf "ass=output/subtitles.ass" -map 0:v:0 -map 1:a:0 \
  -c:v libx264 -crf 18 -preset medium -c:a aac \
  output/final_subtitled.mp4
```

Do not use the original continuous `narration.mp3` in the final render; only `final_narration.wav` has been returned to the source visual anchors.

### 9. Verify visually, semantically, and technically

Inspect frames from every cue, including the longest line, plus the beginning and end. Confirm:

- no old text or ghost outlines remain;
- no black bar, blur band, mask edge, or obvious inpainting smear is visible;
- new text is spelled correctly, synchronized, readable, and inside safe margins;
- no face, product, or important visual is covered;
- the final video decodes successfully and retains audio, duration, dimensions, and frame rate.
- localization sounds native, preserves claims and selling points, and avoids literal or culturally awkward phrasing;
- every semantic block begins at its own `source_start`, so a short earlier block cannot pull later narration ahead of its visual stage;
- each action or idea occurs in the correct visual stage, with small natural gaps or up to about 0.25 seconds of spill accepted;
- cues use anchored relative character timing and stay aligned to the final narration at the beginning, midpoint, and end without cumulative drift;
- narration uses one stable global pace; `timing_warning` is recorded when the one-pass duration mismatch exceeds the allowed tempo range;
- the formal run used one localization pass and one ElevenLabs request, with no timing-triggered regeneration.

Return the final MP4 and the SRT. Mention repaired burned-in captions only when that operation was required.

## Failure handling

- No speech: explain that reliable subtitles cannot be generated.
- Weak transcription: use a larger Whisper model or the original script while preserving timings.
- Unavailable font: fall back to Arial or another installed font with the required glyphs.
- Missing narration credentials during a full language conversion: if the user already configured macOS Keychain, retry once with local Keychain access before treating the key as missing. Otherwise explain that the conversion is incomplete and ask the user to run the one-time Keychain configuration command; do not request the key in chat or silently downgrade to subtitle-only output.
- Timed TTS unavailable: do not automatically fall through to a second paid synthesis call. Report the failed request. The legacy `synthesize` command is retained for a separately chosen fallback run, but precise subtitle alignment then requires timing data supplied by another explicit alignment method.
- Large duration mismatch: anchor every block anyway, preserve later `source_start` positions, set `timing_warning` for spills or overlaps, and report that manual revision may be needed. Do not automatically regenerate.
- Failed removal: do not substitute a black box automatically; request a clean source or present the least damaging alternative.
