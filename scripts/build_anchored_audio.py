#!/usr/bin/env python3
"""Anchor timed TTS semantic blocks to their source-video start times."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


class AnchorBuildError(ValueError):
    """The localization, alignment, or requested audio timeline is invalid."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnchorBuildError(f"Could not read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnchorBuildError(f"Expected a JSON object in {path}")
    return value


def read_alignment(data: dict[str, Any]) -> tuple[list[str], list[float], list[float]]:
    characters = data.get("characters")
    starts = data.get("character_start_times_seconds")
    ends = data.get("character_end_times_seconds")
    if not all(isinstance(value, list) for value in (characters, starts, ends)):
        raise AnchorBuildError("alignment.json is missing character timing arrays.")
    if not characters or not (len(characters) == len(starts) == len(ends)):
        raise AnchorBuildError("alignment.json contains inconsistent array lengths.")
    try:
        parsed = (
            [str(value) for value in characters],
            [float(value) for value in starts],
            [float(value) for value in ends],
        )
    except (TypeError, ValueError) as exc:
        raise AnchorBuildError("Alignment timestamps must be numeric.") from exc
    for index, (start, end) in enumerate(zip(parsed[1], parsed[2])):
        if start < 0 or end < start:
            raise AnchorBuildError(f"Invalid character timing at index {index}: {start}–{end}.")
    return parsed


def nonspace_bounds(characters: list[str], start: int, end: int) -> tuple[int, int]:
    indexes = [index for index in range(start, end) if characters[index].strip()]
    if not indexes:
        raise AnchorBuildError(f"Character range {start}:{end} contains only whitespace.")
    return indexes[0], indexes[-1]


def build_anchor_plan(
    localization: dict[str, Any],
    characters: list[str],
    starts: list[float],
    ends: list[float],
    video_duration: float,
    speed_factor: float,
) -> list[dict[str, Any]]:
    blocks = localization.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise AnchorBuildError("localized_blocks.json must contain a non-empty blocks list.")
    if video_duration <= 0:
        raise AnchorBuildError("video_duration must be positive.")
    if not 0.92 <= speed_factor <= 1.08:
        raise AnchorBuildError(
            "--speed-factor must stay within 0.92–1.08; do not force a large tempo change."
        )

    plan: list[dict[str, Any]] = []
    previous_source_start = -1.0
    for block in blocks:
        if not isinstance(block, dict):
            raise AnchorBuildError("Every localized block must be a JSON object.")
        spoken = str(block.get("spoken_text", "")).strip()
        if not spoken:
            raise AnchorBuildError(f"Block {block.get('id')} has empty spoken_text.")
        try:
            char_start = int(block["char_start"])
            char_end = int(block["char_end"])
            source_start = float(block["source_start"])
            source_end = float(block["source_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AnchorBuildError(
                f"Block {block.get('id')} needs valid source and character ranges."
            ) from exc
        if char_start < 0 or char_end <= char_start or char_end > len(characters):
            raise AnchorBuildError(
                f"Block {block.get('id')} has invalid character range {char_start}:{char_end}."
            )
        if "".join(characters[char_start:char_end]) != spoken:
            raise AnchorBuildError(
                f"Block {block.get('id')} character range does not match spoken_text."
            )
        if source_start < 0 or source_end <= source_start:
            raise AnchorBuildError(
                f"Block {block.get('id')} has invalid source range {source_start}–{source_end}."
            )
        if source_start < previous_source_start:
            raise AnchorBuildError("Semantic blocks must be ordered by source_start.")
        previous_source_start = source_start

        first, last = nonspace_bounds(characters, char_start, char_end)
        raw_start = starts[first]
        raw_end = ends[last]
        if raw_end <= raw_start:
            raise AnchorBuildError(f"Block {block.get('id')} has no positive audio duration.")
        final_duration = (raw_end - raw_start) / speed_factor
        final_end = source_start + final_duration
        source_duration = source_end - source_start
        spill_seconds = max(0.0, final_end - source_end)
        plan.append(
            {
                "id": block.get("id"),
                "source_start": source_start,
                "source_end": source_end,
                "source_duration": source_duration,
                "char_start": char_start,
                "char_end": char_end,
                "raw_audio_start": raw_start,
                "raw_audio_end": raw_end,
                "raw_audio_duration": raw_end - raw_start,
                "final_audio_start": source_start,
                "final_audio_end": final_end,
                "final_audio_duration": final_duration,
                "spill_seconds": spill_seconds,
                "overlap_with_next_seconds": 0.0,
                "timing_warning": spill_seconds > 0.25 or final_end > video_duration,
            }
        )

    for index, block in enumerate(plan[:-1]):
        overlap = max(0.0, block["final_audio_end"] - plan[index + 1]["final_audio_start"])
        block["overlap_with_next_seconds"] = overlap
        if overlap > 0.25:
            block["timing_warning"] = True
    return plan


def ffmpeg_filter(plan: list[dict[str, Any]], video_duration: float, speed_factor: float) -> str:
    source_labels = "".join(f"[src{index}]" for index in range(len(plan)))
    tempo = f",atempo={speed_factor:.9f}" if abs(speed_factor - 1.0) > 1e-9 else ""
    if len(plan) == 1:
        source_stage = (
            f"[0:a]aformat=sample_rates=48000:channel_layouts=mono{tempo}[src0]"
        )
    else:
        source_stage = (
            f"[0:a]aformat=sample_rates=48000:channel_layouts=mono{tempo},"
            f"asplit={len(plan)}{source_labels}"
        )

    filters = [source_stage]
    for index, block in enumerate(plan):
        raw_start = block["raw_audio_start"] / speed_factor
        raw_end = block["raw_audio_end"] / speed_factor
        delay_ms = max(0, round(block["final_audio_start"] * 1000))
        filters.append(
            f"[src{index}]atrim=start={raw_start:.9f}:end={raw_end:.9f},"
            f"asetpts=PTS-STARTPTS,adelay={delay_ms}:all=1[block{index}]"
        )
    filters.append(f"anullsrc=r=48000:cl=mono,atrim=duration={video_duration:.9f}[base]")
    inputs = "[base]" + "".join(f"[block{index}]" for index in range(len(plan)))
    filters.append(
        f"{inputs}amix=inputs={len(plan) + 1}:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95:latency=1[out]"
    )
    return ";".join(filters)


def render_audio(
    narration: Path,
    output: Path,
    plan: list[dict[str, Any]],
    video_duration: float,
    speed_factor: float,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AnchorBuildError("FFmpeg is required to build anchored narration.")
    if not narration.is_file():
        raise AnchorBuildError(f"Narration audio not found: {narration}")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-i",
        str(narration),
        "-filter_complex",
        ffmpeg_filter(plan, video_duration, speed_factor),
        "-map",
        "[out]",
        "-t",
        f"{video_duration:.9f}",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown FFmpeg error"
        raise AnchorBuildError(f"Could not build anchored narration: {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("localized_blocks", type=Path)
    parser.add_argument("narration", type=Path)
    parser.add_argument("alignment", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("anchored_blocks_output", type=Path)
    parser.add_argument("--video-duration", type=float, required=True)
    parser.add_argument(
        "--speed-factor",
        type=float,
        default=1.0,
        help="One global atempo factor applied before every block is cut",
    )
    args = parser.parse_args()

    localization = load_json(args.localized_blocks)
    characters, starts, ends = read_alignment(load_json(args.alignment))
    plan = build_anchor_plan(
        localization,
        characters,
        starts,
        ends,
        args.video_duration,
        args.speed_factor,
    )
    render_audio(args.narration, args.output, plan, args.video_duration, args.speed_factor)

    result = {
        "video_duration": args.video_duration,
        "global_speed_factor": args.speed_factor,
        "timing_warning": any(block["timing_warning"] for block in plan),
        "blocks": plan,
    }
    args.anchored_blocks_output.parent.mkdir(parents=True, exist_ok=True)
    args.anchored_blocks_output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    warning_count = sum(bool(block["timing_warning"]) for block in plan)
    print(f"Wrote anchored narration to {args.output}")
    print(f"Wrote {len(plan)} anchored blocks to {args.anchored_blocks_output}")
    if warning_count:
        print(f"timing_warning: {warning_count} block(s) need manual review")


if __name__ == "__main__":
    try:
        main()
    except AnchorBuildError as exc:
        raise SystemExit(str(exc)) from exc
