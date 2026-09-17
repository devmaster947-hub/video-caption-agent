#!/usr/bin/env bash
set -euo pipefail

INPUT_VIDEO="${1:-}"
LANGUAGE="${2:-}"
OUTPUT_DIR="${3:-output}"

if [[ -z "$INPUT_VIDEO" ]]; then
  echo "Usage: ./add_subtitles.sh input.mp4 [Language] [output_dir]"
  echo "Example: ./add_subtitles.sh input.mp4 English output"
  exit 1
fi

if [[ ! -f "$INPUT_VIDEO" ]]; then
  echo "Input video not found: $INPUT_VIDEO"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$OUTPUT_DIR"

AUDIO_PATH="$OUTPUT_DIR/audio.wav"
SRT_PATH="$OUTPUT_DIR/subtitles.srt"
ASS_PATH="$OUTPUT_DIR/subtitles.ass"
FINAL_PATH="$OUTPUT_DIR/final_subtitled.mp4"

echo "Extracting audio..."
ffmpeg -y -i "$INPUT_VIDEO" -vn -acodec pcm_s16le -ar 16000 -ac 1 "$AUDIO_PATH"

echo "Running Whisper..."
if [[ -n "$LANGUAGE" ]]; then
  whisper "$AUDIO_PATH" --language "$LANGUAGE" --task transcribe --output_format srt --output_dir "$OUTPUT_DIR"
else
  whisper "$AUDIO_PATH" --task transcribe --output_format srt --output_dir "$OUTPUT_DIR"
fi

WHISPER_SRT="$OUTPUT_DIR/audio.srt"
if [[ ! -f "$WHISPER_SRT" ]]; then
  echo "Whisper SRT not found at expected path: $WHISPER_SRT"
  echo "Searching for generated SRT..."
  FOUND_SRT="$(find "$OUTPUT_DIR" -maxdepth 1 -name '*.srt' | head -n 1 || true)"
  if [[ -z "$FOUND_SRT" ]]; then
    echo "No SRT file generated."
    exit 1
  fi
  cp "$FOUND_SRT" "$SRT_PATH"
else
  cp "$WHISPER_SRT" "$SRT_PATH"
fi

echo "Converting SRT to ASS..."
python3 "$SCRIPT_DIR/srt_to_ass.py" "$SRT_PATH" "$ASS_PATH"

echo "Burning subtitles into video..."
ffmpeg -y -i "$INPUT_VIDEO" -vf "ass=$ASS_PATH" -c:a copy "$FINAL_PATH"

echo "Done: $FINAL_PATH"
