#!/usr/bin/env python3
"""Convert SRT subtitles to a simple styled ASS file for short-form videos."""

import re
import sys
from pathlib import Path


def srt_time_to_ass(t: str) -> str:
    # SRT: 00:00:01,230 -> ASS: 0:00:01.23
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    cs = int(ms[:3]) // 10
    return f"{int(h)}:{m}:{s}.{cs:02d}"


def parse_srt(text: str):
    blocks = re.split(r"\n\s*\n", text.strip(), flags=re.MULTILINE)
    events = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        time_line_index = 1 if "-->" in lines[1] else 0
        if "-->" not in lines[time_line_index]:
            continue
        start, end = [x.strip() for x in lines[time_line_index].split("-->")]
        subtitle_lines = lines[time_line_index + 1 :]
        subtitle = r"\N".join(subtitle_lines)
        events.append((srt_time_to_ass(start), srt_time_to_ass(end), subtitle))
    return events


def write_ass(events, output_path: Path):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,58,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,4,0,2,80,80,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for start, end, subtitle in events:
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{subtitle}\n")
    output_path.write_text("".join(lines), encoding="utf-8")


def main():
    if len(sys.argv) != 3:
        print("Usage: srt_to_ass.py input.srt output.ass", file=sys.stderr)
        sys.exit(1)
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    if not input_path.exists():
        print(f"Input SRT not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    events = parse_srt(input_path.read_text(encoding="utf-8-sig"))
    if not events:
        print("No subtitle events found in SRT.", file=sys.stderr)
        sys.exit(1)
    write_ass(events, output_path)
    print(f"Wrote ASS subtitles: {output_path}")


if __name__ == "__main__":
    main()
