#!/usr/bin/env python3
"""Remove simple high-contrast burned-in captions with glyph-level inpainting."""

import argparse
import subprocess
from pathlib import Path

import cv2
import numpy as np


def parse_roi(value: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(part) for part in value.split(","))
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("ROI must be X,Y,W,H") from None
    if min(x, y) < 0 or min(w, h) <= 0:
        raise argparse.ArgumentTypeError("ROI values must be non-negative and sized above zero")
    return x, y, w, h


def select_components(binary: np.ndarray, max_height: int) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    selected = np.zeros_like(binary)
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if 3 <= area <= 900 and 2 <= w <= 180 and 3 <= h <= max_height:
            if x > 0 and y > 0 and x + w < binary.shape[1] and y + h < binary.shape[0]:
                selected[labels == label] = 255
    return selected


def build_mask(
    frame: np.ndarray,
    roi: tuple[int, int, int, int],
    polarity: str,
    light_min: int,
    dark_max: int,
    saturation_max: int,
) -> np.ndarray:
    x, y, w, h = roi
    crop = frame[y : y + h, x : x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1]
    max_glyph_height = max(8, int(h * 0.75))

    mask = np.zeros_like(gray)
    if polarity in {"white", "both"}:
        light = np.uint8((gray >= light_min) & (saturation <= saturation_max)) * 255
        light = select_components(light, max_glyph_height)
        # Grow through the light fill and its dark outline, not the whole caption band.
        light = cv2.dilate(light, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
        mask = cv2.bitwise_or(mask, light)
    if polarity in {"black", "both"}:
        dark = np.uint8(gray <= dark_max) * 255
        dark = select_components(dark, max_glyph_height)
        dark = cv2.dilate(dark, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        mask = cv2.bitwise_or(mask, dark)

    full_mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    full_mask[y : y + h, x : x + w] = mask
    return full_mask


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--roi", required=True, type=parse_roi, help="Caption area as X,Y,W,H")
    parser.add_argument("--polarity", choices=("white", "black", "both"), default="white")
    parser.add_argument("--light-min", type=int, default=175)
    parser.add_argument("--dark-max", type=int, default=72)
    parser.add_argument("--saturation-max", type=int, default=80)
    parser.add_argument("--radius", type=float, default=4.0)
    parser.add_argument("--preview-dir", type=Path)
    args = parser.parse_args()

    capture = cv2.VideoCapture(str(args.input))
    if not capture.isOpened():
        raise SystemExit(f"Could not open input: {args.input}")
    fps = capture.get(cv2.CAP_PROP_FPS)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    x, y, w, h = args.roi
    if x + w > width or y + h > height:
        raise SystemExit(f"ROI {args.roi} exceeds video size {width}x{height}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.preview_dir:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
    sample_frames = {int(frame_count * ratio) for ratio in (0.1, 0.5, 0.9)}

    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", f"{fps:.8f}", "-i", "-",
        "-i", str(args.input), "-map", "0:v:0", "-map", "1:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", str(args.output),
    ]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE)

    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        mask = build_mask(
            frame, args.roi, args.polarity, args.light_min, args.dark_max, args.saturation_max
        )
        cleaned = cv2.inpaint(frame, mask, args.radius, cv2.INPAINT_TELEA)
        if args.preview_dir and index in sample_frames:
            cv2.imwrite(str(args.preview_dir / f"frame_{index:06d}_mask.png"), mask)
            cv2.imwrite(str(args.preview_dir / f"frame_{index:06d}_cleaned.png"), cleaned)
        assert encoder.stdin is not None
        encoder.stdin.write(cleaned.tobytes())
        index += 1

    capture.release()
    assert encoder.stdin is not None
    encoder.stdin.close()
    if encoder.wait() != 0:
        raise SystemExit("FFmpeg encoder failed")
    print(f"Wrote repaired video: {args.output}")


if __name__ == "__main__":
    main()
