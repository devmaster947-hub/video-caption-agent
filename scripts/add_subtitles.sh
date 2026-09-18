#!/usr/bin/env bash
set -euo pipefail

INPUT_VIDEO="${1:-}"
LANGUAGE="${2:-}"
OUTPUT_DIR="${3:-output}"
QUALITY="${4:-standard}"

if [[ -z "$INPUT_VIDEO" ]]; then
  echo "Usage: ./add_subtitles.sh input.mp4 [Language] [output_dir] [fast|standard|high]"
  echo "Example: ./add_subtitles.sh input.mp4 English output standard"
  exit 1
fi

case "$QUALITY" in
  fast)
    WHISPER_MODEL="base"
    VIDEO_PRESET="veryfast"
    VIDEO_CRF="23"
    ;;
  standard)
    WHISPER_MODEL="small"
    VIDEO_PRESET="medium"
    VIDEO_CRF="18"
    ;;
  high)
    WHISPER_MODEL="medium"
    VIDEO_PRESET="slow"
    VIDEO_CRF="16"
    ;;
  *)
    echo "Unsupported quality: $QUALITY (expected fast, standard, or high)"
    exit 1
    ;;
esac

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
  whisper "$AUDIO_PATH" --model "$WHISPER_MODEL" --language "$LANGUAGE" --task transcribe --output_format srt --output_dir "$OUTPUT_DIR"
else
  whisper "$AUDIO_PATH" --model "$WHISPER_MODEL" --task transcribe --output_format srt --output_dir "$OUTPUT_DIR"
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
ffmpeg -y -i "$INPUT_VIDEO" -vf "ass=$ASS_PATH" \
  -c:v libx264 -preset "$VIDEO_PRESET" -crf "$VIDEO_CRF" \
  -c:a copy "$FINAL_PATH"

echo "Done: $FINAL_PATH"
