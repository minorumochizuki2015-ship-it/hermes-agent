#!/usr/bin/env python3
"""Assemble a demo clip list through ffmpeg with sanitized output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def _result(outcome: str, code: str, **fields: object) -> None:
    print(json.dumps({"outcome": outcome, "code": code, **fields}, sort_keys=True))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", required=True, type=Path, help="UTF-8 clip path list")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _concat_line(path: Path) -> str:
    escaped = str(path.resolve()).replace("'", "'\\''")
    return f"file '{escaped}'"


def main() -> int:
    args = _parse_args()
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        _result("failure", "media_tool_unavailable")
        return 69

    try:
        clips = [
            Path(line.strip()).expanduser()
            for line in args.list.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except OSError:
        _result("failure", "clip_list_unreadable")
        return 66
    if not clips or any(not clip.is_file() for clip in clips):
        _result("failure", "clip_input_unavailable", input_count=len(clips))
        return 66
    if args.audio is not None and not args.audio.is_file():
        _result("failure", "audio_input_unavailable")
        return 66
    resolved_clips = [clip.resolve() for clip in clips]
    resolved_audio = args.audio.resolve() if args.audio is not None else None
    resolved_output = args.output.resolve()
    if resolved_output in resolved_clips or resolved_output == resolved_audio:
        _result("failure", "output_conflicts_with_input")
        return 64
    if args.dry_run:
        _result("success", "assembly_preflight_passed", input_count=len(clips))
        return 0

    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hermes-demo-") as temp_dir:
        concat = Path(temp_dir) / "clips.txt"
        concat.write_text(
            "\\n".join(_concat_line(clip) for clip in clips) + "\\n",
            encoding="utf-8",
        )
        command = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
        ]
        if resolved_audio is not None:
            command.extend(["-i", str(resolved_audio)])
        command.extend(
            [
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            ]
        )
        if resolved_audio is not None:
            command.extend(["-c:a", "aac", "-shortest"])
        command.append(str(resolved_output))
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    if completed.returncode != 0 or not resolved_output.is_file():
        _result("failure", "assembly_failed", exit_code=completed.returncode)
        return 1
    _result("success", "assembly_complete", input_count=len(clips))
    return 0


if __name__ == "__main__":
    sys.exit(main())
