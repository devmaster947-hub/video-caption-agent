#!/usr/bin/env python3
"""Require successful Lingzhi Studio API-key authentication before skill use."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


SUCCESS = {"ok": True, "authenticated": True}
KEY_HELP = "请获取灵智工坊API Key：[https://www.lingzhiai.com.cn/](https://www.lingzhiai.com.cn/)"


class PreflightError(RuntimeError):
    pass


def load_key() -> str:
    for name in ("LZSTUDIO_API_KEY", "RECREATE_VIDEO_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value

    config = Path.home() / ".recreate-video" / "config.json"
    if config.is_file():
        try:
            payload = json.loads(config.read_text(encoding="utf-8"))
            value = payload.get("apiKey", "") if isinstance(payload, dict) else ""
        except (OSError, json.JSONDecodeError):
            value = ""
        if isinstance(value, str) and value.strip():
            return value.strip()

    raise PreflightError("本机未配置灵智工坊 API Key。")


def bundled_cli() -> Path | None:
    root = Path(__file__).resolve().parent.parent / "cli"
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return root / "macos-arm64" / "lzstudio"
    if system == "Windows" and machine in {"amd64", "x86_64", "x64"}:
        return root / "windows-x64" / "lzstudio.exe"
    return None


def resolve_cli(explicit: str | None) -> str:
    candidates: list[str | Path | None] = [
        explicit,
        os.environ.get("LZSTUDIO_CLI"),
        shutil.which("lzstudio"),
        shutil.which("lzstudio.exe"),
        bundled_cli(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    raise PreflightError("未找到可执行的灵智工坊 CLI。")


def parse_json_output(output: str) -> Any:
    text = output.strip()
    if not text:
        raise PreflightError("灵智工坊鉴权未返回可验证结果。")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise PreflightError("灵智工坊鉴权返回了无法验证的结果。")


def verify_key(cli: str, key: str) -> None:
    command = [cli, "account", "--api-key", key, "--credits"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise PreflightError("灵智工坊鉴权超时。") from None
    except OSError as exc:
        raise PreflightError(f"无法启动灵智工坊 CLI：{exc}") from None

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误").replace(key, "[REDACTED]")
        detail = " ".join(detail.split())[:500]
        raise PreflightError(f"灵智工坊 API Key 鉴权失败：{detail}")

    parse_json_output(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", help="Path to the Lingzhi Studio CLI")
    args = parser.parse_args()

    try:
        key = load_key()
        cli = resolve_cli(args.cli)
        verify_key(cli, key)
    except PreflightError as exc:
        print(f"灵智工坊 API Key 预检未通过：{exc}", file=sys.stderr)
        print(KEY_HELP, file=sys.stderr)
        return 1

    print(json.dumps(SUCCESS, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
