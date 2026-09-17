#!/usr/bin/env python3
"""List voices or synthesize one continuous narration with optional timestamps."""

from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import json
import os
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional


API_ROOT = "https://api.elevenlabs.io"
API_KEY_ENV = "ELEVENLABS_API_KEY"
KEYCHAIN_SERVICE = "codex.video-caption-agent.elevenlabs"


def keychain_account() -> str:
    return getpass.getuser()


def keychain_api_key() -> str:
    if shutil.which("security") is None:
        return ""
    completed = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            keychain_account(),
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def api_key() -> str:
    value = os.environ.get(API_KEY_ENV, "").strip()
    if not value:
        value = keychain_api_key()
    if not value:
        raise SystemExit(
            f"Missing {API_KEY_ENV}. Run this once in a local terminal: "
            "python scripts/elevenlabs_tts.py configure"
        )
    return value


def configure_key(_: argparse.Namespace) -> None:
    if shutil.which("security") is None:
        raise SystemExit(
            "macOS Keychain is unavailable. Configure ELEVENLABS_API_KEY in the local environment."
        )
    value = getpass.getpass("ElevenLabs API key (input is hidden): ").strip()
    if not value:
        raise SystemExit("No API key entered; Keychain was not changed.")
    completed = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-a",
            keychain_account(),
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
            value,
            "-U",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    value = ""
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown Keychain error"
        raise SystemExit(f"Could not save the API key to Keychain: {detail}")
    print("Saved the ElevenLabs API key to macOS Keychain.")


def request(
    url: str, *, method: str = "GET", body: Optional[Dict[str, Any]] = None
) -> bytes:
    curl = shutil.which("curl")
    if curl is None:
        raise SystemExit("curl is required for reliable ElevenLabs requests.")

    value = api_key()
    header_read, header_write = os.pipe()
    try:
        os.write(header_write, f"xi-api-key: {value}\n".encode("utf-8"))
    finally:
        os.close(header_write)
        value = ""

    command = [
        curl,
        "--silent",
        "--show-error",
        "--location",
        "--fail-with-body",
        "--connect-timeout",
        "30",
        "--max-time",
        "180",
        "--request",
        method,
        "--header",
        f"@/dev/fd/{header_read}",
    ]
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        command.extend(
            ["--header", "Content-Type: application/json", "--data-binary", "@-"]
        )
    elif method == "GET":
        command.extend(["--retry", "2", "--retry-all-errors", "--retry-delay", "1"])
    command.append(url)

    try:
        completed = subprocess.run(
            command,
            input=data,
            check=False,
            capture_output=True,
            pass_fds=(header_read,),
        )
    finally:
        os.close(header_read)
    if completed.returncode != 0:
        detail = completed.stdout or completed.stderr
        message = detail.decode("utf-8", errors="replace").strip()
        raise SystemExit(
            f"ElevenLabs request failed (curl {completed.returncode}): "
            f"{message or 'no response body'}"
        )
    return completed.stdout


def list_voices(_: argparse.Namespace) -> None:
    voices: List[Dict[str, Any]] = []
    token: Optional[str] = None
    while True:
        query = {"page_size": "100", "include_total_count": "false"}
        if token:
            query["next_page_token"] = token
        url = f"{API_ROOT}/v2/voices?{urllib.parse.urlencode(query)}"
        page = json.loads(request(url))
        voices.extend(page.get("voices", []))
        if not page.get("has_more"):
            break
        token = page.get("next_page_token")
        if not token:
            raise SystemExit("ElevenLabs returned has_more without a next-page token.")

    for voice in sorted(voices, key=lambda item: (item.get("name") or "").casefold()):
        labels = voice.get("labels") or {}
        language = labels.get("language") or labels.get("locale") or "-"
        category = voice.get("category") or "-"
        print(f"{voice.get('voice_id', '-')}\t{voice.get('name', '-')}\t{language}\t{category}")


def synthesize(args: argparse.Namespace) -> None:
    source = Path(args.text_file)
    text = source.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"Narration text is empty: {source}")

    query = {"output_format": args.output_format}
    if args.language == "ja":
        query["apply_language_text_normalization"] = "true"
    voice_id = urllib.parse.quote(args.voice_id, safe="")
    url = f"{API_ROOT}/v1/text-to-speech/{voice_id}?{urllib.parse.urlencode(query)}"
    payload = {"text": text, "model_id": args.model_id}
    audio = request(url, method="POST", body=payload)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(audio)
    print(f"Saved {len(audio)} bytes to {output}")


def synthesize_timed(args: argparse.Namespace) -> None:
    source = Path(args.text_file)
    text = source.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"Narration text is empty: {source}")

    query = {"output_format": args.output_format}
    if args.language == "ja":
        query["apply_language_text_normalization"] = "true"
    voice_id = urllib.parse.quote(args.voice_id, safe="")
    url = (
        f"{API_ROOT}/v1/text-to-speech/{voice_id}/with-timestamps?"
        f"{urllib.parse.urlencode(query)}"
    )
    payload = {"text": text, "model_id": args.model_id}
    try:
        response = json.loads(request(url, method="POST", body=payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("ElevenLabs timed TTS returned invalid JSON.") from exc

    encoded_audio = response.get("audio_base64")
    if not isinstance(encoded_audio, str) or not encoded_audio:
        raise SystemExit("ElevenLabs timed TTS returned no audio_base64 data.")
    alignment = response.get("alignment")
    alignment_source = "alignment"
    if not isinstance(alignment, dict):
        alignment = response.get("normalized_alignment")
        alignment_source = "normalized_alignment"
    if not isinstance(alignment, dict):
        raise SystemExit("ElevenLabs timed TTS returned no character alignment.")

    required = (
        "characters",
        "character_start_times_seconds",
        "character_end_times_seconds",
    )
    arrays = [alignment.get(key) for key in required]
    if not all(isinstance(value, list) for value in arrays):
        raise SystemExit("ElevenLabs timed TTS returned malformed alignment arrays.")
    if not arrays[0] or len({len(value) for value in arrays}) != 1:
        raise SystemExit("ElevenLabs timed TTS returned inconsistent alignment lengths.")
    try:
        audio = base64.b64decode(encoded_audio, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SystemExit("ElevenLabs timed TTS returned invalid base64 audio.") from exc

    output = Path(args.output)
    alignment_output = Path(args.alignment_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    alignment_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(audio)
    alignment_output.write_text(
        json.dumps(
            {
                "alignment_source": alignment_source,
                **{key: alignment[key] for key in required},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(audio)} bytes to {output}")
    print(f"Saved {len(arrays[0])} character timings to {alignment_output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser = subparsers.add_parser(
        "configure", help="Securely save the API key to macOS Keychain"
    )
    configure_parser.set_defaults(func=configure_key)

    list_parser = subparsers.add_parser("list", help="List voices available to the account")
    list_parser.set_defaults(func=list_voices)

    synth = subparsers.add_parser("synthesize", help="Generate one continuous narration")
    synth.add_argument("--voice-id", required=True)
    synth.add_argument("--text-file", required=True)
    synth.add_argument("--output", required=True)
    synth.add_argument("--language", default="ja", choices=("ja", "auto"))
    synth.add_argument("--model-id", default="eleven_multilingual_v2")
    synth.add_argument("--output-format", default="mp3_44100_128")
    synth.set_defaults(func=synthesize)

    timed = subparsers.add_parser(
        "synthesize-timed",
        help="Generate one continuous narration and character timestamps",
    )
    timed.add_argument("--voice-id", required=True)
    timed.add_argument("--text-file", required=True)
    timed.add_argument("--output", required=True)
    timed.add_argument("--alignment-output", required=True)
    timed.add_argument("--language", default="ja", choices=("ja", "auto"))
    timed.add_argument("--model-id", default="eleven_multilingual_v2")
    timed.add_argument("--output-format", default="mp3_44100_128")
    timed.set_defaults(func=synthesize_timed)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
