#!/usr/bin/env python3
"""Build phrase-level SRT cues from localized blocks and TTS alignment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


WORD_RE = re.compile(r"\S+")
BREAK_RE = re.compile(r"[,;:!?。！？，；：—–-][\"'’”》」】)]*$")


class SubtitleBuildError(ValueError):
    """The localization or alignment input is inconsistent."""


@dataclass(frozen=True)
class TextSpan:
    text: str
    start: int
    end: int


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubtitleBuildError(f"Could not read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SubtitleBuildError(f"Expected a JSON object in {path}")
    return value


def read_alignment(data: dict[str, Any]) -> tuple[list[str], list[float], list[float]]:
    characters = data.get("characters")
    starts = data.get("character_start_times_seconds")
    ends = data.get("character_end_times_seconds")
    if not all(isinstance(value, list) for value in (characters, starts, ends)):
        raise SubtitleBuildError("alignment.json is missing character timing arrays.")
    if not characters or not (len(characters) == len(starts) == len(ends)):
        raise SubtitleBuildError("alignment.json contains inconsistent array lengths.")
    try:
        return (
            [str(value) for value in characters],
            [float(value) for value in starts],
            [float(value) for value in ends],
        )
    except (TypeError, ValueError) as exc:
        raise SubtitleBuildError("Alignment timestamps must be numeric.") from exc


def split_phrase(text: str, max_chars: int) -> list[TextSpan]:
    if len(text) <= max_chars:
        return [TextSpan(text, 0, len(text))]
    words = [TextSpan(match.group(), match.start(), match.end()) for match in WORD_RE.finditer(text)]
    if len(words) <= 1:
        return [
            TextSpan(text[start : start + max_chars], start, min(len(text), start + max_chars))
            for start in range(0, len(text), max_chars)
        ]

    groups: list[list[TextSpan]] = []
    index = 0
    while index < len(words):
        first = index
        last = index
        while last + 1 < len(words) and words[last + 1].end - words[first].start <= max_chars:
            last += 1
        preferred = [
            candidate
            for candidate in range(first, last + 1)
            if BREAK_RE.search(words[candidate].text)
            and words[candidate].end - words[first].start >= max_chars * 0.45
        ]
        chosen = preferred[-1] if preferred else last
        groups.append(words[first : chosen + 1])
        index = chosen + 1

    # Avoid leaving a final cue with only one or two words when a balanced split is possible.
    if len(groups) > 1 and len(groups[-1]) <= 2 and len(groups[-2]) >= 4:
        groups[-1].insert(0, groups[-2].pop())

    return [
        TextSpan(text[group[0].start : group[-1].end], group[0].start, group[-1].end)
        for group in groups
    ]


def nonspace_bounds(characters: list[str], start: int, end: int) -> tuple[int, int]:
    indexes = [index for index in range(start, end) if characters[index].strip()]
    if not indexes:
        raise SubtitleBuildError(f"Character range {start}:{end} contains only whitespace.")
    return indexes[0], indexes[-1]


def seconds_to_srt(value: float) -> str:
    milliseconds = max(0, round(value * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def build_cues(
    localization: dict[str, Any],
    characters: list[str],
    starts: list[float],
    ends: list[float],
    max_chars: int,
    speed_factor: float,
    anchored: Optional[dict[str, Any]] = None,
) -> tuple[list[tuple[float, float, str]], list[dict[str, Any]]]:
    blocks = localization.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise SubtitleBuildError("localized_blocks.json must contain a non-empty blocks list.")
    if max_chars < 12:
        raise SubtitleBuildError("--max-chars must be at least 12.")
    if not 0.92 <= speed_factor <= 1.08:
        raise SubtitleBuildError(
            "--speed-factor must stay within 0.92–1.08; do not force a large tempo change."
        )

    anchor_by_id: dict[Any, dict[str, Any]] = {}
    if anchored is not None:
        anchored_blocks = anchored.get("blocks")
        if not isinstance(anchored_blocks, list) or not anchored_blocks:
            raise SubtitleBuildError("anchored_blocks.json must contain a non-empty blocks list.")
        for anchor in anchored_blocks:
            if not isinstance(anchor, dict) or "id" not in anchor:
                raise SubtitleBuildError("Every anchored block must be an object with an id.")
            if anchor["id"] in anchor_by_id:
                raise SubtitleBuildError(f"Duplicate anchored block id: {anchor['id']!r}.")
            anchor_by_id[anchor["id"]] = anchor

    cues: list[tuple[float, float, str]] = []
    block_report: list[dict[str, Any]] = []
    previous_block_id: Any = None
    for block in blocks:
        if not isinstance(block, dict):
            raise SubtitleBuildError("Every localized block must be a JSON object.")
        spoken = str(block.get("spoken_text", "")).strip()
        if not spoken:
            raise SubtitleBuildError(f"Block {block.get('id')} has empty spoken_text.")
        try:
            char_start = int(block["char_start"])
            char_end = int(block["char_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SubtitleBuildError(
                f"Block {block.get('id')} needs integer char_start and char_end."
            ) from exc
        if char_start < 0 or char_end <= char_start or char_end > len(characters):
            raise SubtitleBuildError(
                f"Block {block.get('id')} has invalid range {char_start}:{char_end}."
            )
        aligned_text = "".join(characters[char_start:char_end])
        if aligned_text != spoken:
            raise SubtitleBuildError(
                f"Block {block.get('id')} range does not match spoken_text: "
                f"alignment has {aligned_text!r}, block has {spoken!r}."
            )

        anchor = anchor_by_id.get(block.get("id")) if anchored is not None else None
        if anchored is not None and anchor is None:
            raise SubtitleBuildError(f"Missing anchored timing for block {block.get('id')!r}.")
        if anchor is not None:
            try:
                block_raw_start = float(anchor["raw_audio_start"])
                final_audio_start = float(anchor["final_audio_start"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SubtitleBuildError(
                    f"Anchored block {block.get('id')!r} has invalid audio timing."
                ) from exc
            first_block_char, _ = nonspace_bounds(characters, char_start, char_end)
            if abs(block_raw_start - starts[first_block_char]) > 0.02:
                raise SubtitleBuildError(
                    f"Anchored block {block.get('id')!r} does not match alignment.json."
                )
        else:
            block_raw_start = 0.0
            final_audio_start = 0.0

        block_cue_start = None
        block_cue_end = None
        subtitle_overlap_seconds = 0.0
        for phrase in split_phrase(spoken, max_chars):
            first, last = nonspace_bounds(
                characters,
                char_start + phrase.start,
                char_start + phrase.end,
            )
            if anchor is None:
                cue_start = starts[first] / speed_factor
                cue_end = ends[last] / speed_factor
            else:
                cue_start = final_audio_start + (starts[first] - block_raw_start) / speed_factor
                cue_end = final_audio_start + (ends[last] - block_raw_start) / speed_factor
            if cue_start >= cue_end:
                raise SubtitleBuildError(
                    f"Invalid cue timing for {phrase.text!r}: {cue_start:.3f} >= {cue_end:.3f}."
                )
            if cues and cue_start < cues[-1][1] - 0.001:
                overlap = cues[-1][1] - cue_start
                if anchor is None or previous_block_id == block.get("id"):
                    raise SubtitleBuildError(
                        f"Cue overlap before {phrase.text!r}: "
                        f"{cue_start:.3f} < {cues[-1][1]:.3f}."
                    )
                subtitle_overlap_seconds = max(subtitle_overlap_seconds, overlap)
            cues.append((cue_start, cue_end, phrase.text))
            previous_block_id = block.get("id")
            block_cue_start = cue_start if block_cue_start is None else block_cue_start
            block_cue_end = cue_end
        block_report.append(
            {
                "id": block.get("id"),
                "source_start": block.get("source_start"),
                "source_end": block.get("source_end"),
                "audio_start": block_cue_start,
                "audio_end": block_cue_end,
                "subtitle_overlap_seconds": subtitle_overlap_seconds,
            }
        )
    return cues, block_report


def write_srt(cues: list[tuple[float, float, str]], output: Path) -> None:
    blocks = []
    for index, (start, end, text) in enumerate(cues, 1):
        blocks.append(
            f"{index}\n{seconds_to_srt(start)} --> {seconds_to_srt(end)}\n{text}\n"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(blocks), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("localized_blocks", type=Path)
    parser.add_argument("alignment", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--anchored-blocks",
        type=Path,
        help="Use final block positions from build_anchored_audio.py",
    )
    parser.add_argument("--max-chars", type=int, default=42)
    parser.add_argument(
        "--speed-factor",
        type=float,
        default=None,
        help="Single global FFmpeg atempo factor; all timestamps are divided by it",
    )
    parser.add_argument("--video-duration", type=float)
    parser.add_argument("--narration-duration", type=float)
    parser.add_argument("--qa-output", type=Path)
    args = parser.parse_args()

    localization = load_json(args.localized_blocks)
    characters, starts, ends = read_alignment(load_json(args.alignment))
    anchored = load_json(args.anchored_blocks) if args.anchored_blocks else None
    anchored_speed = anchored.get("global_speed_factor") if anchored else None
    try:
        anchored_speed = float(anchored_speed) if anchored_speed is not None else None
    except (TypeError, ValueError) as exc:
        raise SubtitleBuildError("anchored_blocks.json has an invalid global_speed_factor.") from exc
    speed_factor = args.speed_factor if args.speed_factor is not None else anchored_speed or 1.0
    if anchored_speed is not None and abs(speed_factor - anchored_speed) > 1e-9:
        raise SubtitleBuildError(
            "--speed-factor must match anchored_blocks.json global_speed_factor."
        )
    cues, block_report = build_cues(
        localization,
        characters,
        starts,
        ends,
        args.max_chars,
        speed_factor,
        anchored,
    )
    write_srt(cues, args.output)

    required_speed_factor = None
    timing_warning = bool(anchored.get("timing_warning", False)) if anchored else False
    if args.video_duration is not None or args.narration_duration is not None:
        if not args.video_duration or not args.narration_duration:
            raise SubtitleBuildError(
                "Provide both positive --video-duration and --narration-duration values."
            )
        required_speed_factor = args.narration_duration / args.video_duration
        duration_warning = not 0.92 <= required_speed_factor <= 1.08
        timing_warning = timing_warning or duration_warning
        if duration_warning:
            print(
                "timing_warning: narration would require a tempo factor outside 0.92–1.08; "
                "keep the one-pass result and flag it for manual review.",
                file=sys.stderr,
            )

    if args.qa_output:
        final_audio_duration = (
            args.narration_duration / speed_factor
            if args.narration_duration is not None
            else None
        )
        qa = {
            "cue_count": len(cues),
            "speed_factor": speed_factor,
            "required_speed_factor": required_speed_factor,
            "timing_warning": timing_warning,
            "anchored_mode": anchored is not None,
            "final_audio_duration": final_audio_duration,
            "last_cue_end": cues[-1][1],
            "blocks": block_report,
        }
        args.qa_output.parent.mkdir(parents=True, exist_ok=True)
        args.qa_output.write_text(
            json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Wrote {len(cues)} timed subtitle cues to {args.output}")


if __name__ == "__main__":
    try:
        main()
    except SubtitleBuildError as exc:
        raise SystemExit(str(exc)) from exc
