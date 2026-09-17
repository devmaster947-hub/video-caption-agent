#!/usr/bin/env bash
set -e

echo "Checking dependencies..."

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "❌ FFmpeg not found. Install with: brew install ffmpeg"
  exit 1
else
  echo "✅ FFmpeg found: $(ffmpeg -version | head -n 1)"
fi

if ! command -v whisper >/dev/null 2>&1; then
  echo "❌ Whisper CLI not found. Install with: pip install -U openai-whisper"
  exit 1
else
  echo "✅ Whisper found: $(command -v whisper)"
fi

if ! python3 -c 'import cv2' >/dev/null 2>&1; then
  echo "⚠️  OpenCV not found. Burned-in subtitle removal requires: pip install opencv-python-headless"
else
  echo "✅ OpenCV found"
fi

echo "All dependencies are ready."
