"""Audio mixing utility.

Mixes one or more per-user WAV files captured by discord.py's WaveSink into a
single mono MP3 file using FFmpeg.  The output file is placed in the same
directory as the input files.

FFmpeg is invoked as a subprocess and must be on PATH (it is installed in the
Docker image via apt-get).

If only one input file is provided the amix filter is skipped (FFmpeg raises an
error if amix receives a single stream) and the file is converted directly to
mono MP3.
"""

import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


async def mix_to_mp3(wav_paths: list[Path], output_path: Path) -> None:
    """Mix *wav_paths* into a mono MP3 at *output_path*.

    Raises RuntimeError if FFmpeg exits with a non-zero code.
    wav_paths must not be empty.
    """
    if not wav_paths:
        raise ValueError("mix_to_mp3 requires at least one input file")

    if len(wav_paths) == 1:
        # Single stream — just re-encode to mono MP3.
        cmd = [
            "ffmpeg", "-y",
            "-i", str(wav_paths[0]),
            "-ac", "1",          # mono
            "-q:a", "4",         # VBR ~165 kbps — good quality, small file
            str(output_path),
        ]
    else:
        # Multiple streams — mix all inputs to a single mono MP3.
        input_args: list[str] = []
        for p in wav_paths:
            input_args += ["-i", str(p)]

        filter_str = f"amix=inputs={len(wav_paths)}:duration=longest:normalize=0"
        cmd = [
            "ffmpeg", "-y",
            *input_args,
            "-filter_complex", filter_str,
            "-ac", "1",
            "-q:a", "4",
            str(output_path),
        ]

    log.debug("FFmpeg command: %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "FFmpeg not found — ensure it is installed and available on PATH"
        )

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(
            f"FFmpeg exited {proc.returncode}: {stderr.decode(errors='replace').strip()}"
        )

    size_kb = output_path.stat().st_size // 1024
    log.info("Mixed %d file(s) → %s (%d KB)", len(wav_paths), output_path.name, size_kb)
