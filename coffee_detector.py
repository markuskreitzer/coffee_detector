#!/usr/bin/env python3

import argparse
import json
import math
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SAMPLE_RATE = 16_000
BLOCK_SIZE = 2_048


@dataclass(frozen=True)
class DetectionConfig:
    target_hz: float = 4_105.0
    tolerance_hz: float = 120.0
    minimum_tone_dbfs: float = -80.0
    minimum_tone_ratio_db: float = 8.0
    minimum_beep_seconds: float = 0.12
    maximum_beep_seconds: float = 0.55
    minimum_gap_seconds: float = 0.55
    maximum_gap_seconds: float = 1.45
    required_beeps: int = 3


class BeepCadenceDetector:
    def __init__(self, config: DetectionConfig = DetectionConfig()):
        self.config = config
        self._tone_started_at: float | None = None
        self._beeps: deque[float] = deque(maxlen=config.required_beeps)

    def observe(self, samples: np.ndarray, timestamp: float) -> bool:
        tone_present = self._is_tone_present(samples)
        if tone_present and self._tone_started_at is None:
            self._tone_started_at = timestamp
        elif not tone_present and self._tone_started_at is not None:
            duration = timestamp - self._tone_started_at
            started_at = self._tone_started_at
            self._tone_started_at = None
            if self.config.minimum_beep_seconds <= duration <= self.config.maximum_beep_seconds:
                return self._record_beep(started_at)
        return False

    def finish(self, timestamp: float) -> bool:
        if self._tone_started_at is None:
            return False
        duration = timestamp - self._tone_started_at
        started_at = self._tone_started_at
        self._tone_started_at = None
        if self.config.minimum_beep_seconds <= duration <= self.config.maximum_beep_seconds:
            return self._record_beep(started_at)
        return False

    def _record_beep(self, started_at: float) -> bool:
        if self._beeps:
            gap = started_at - self._beeps[-1]
            if not self.config.minimum_gap_seconds <= gap <= self.config.maximum_gap_seconds:
                self._beeps.clear()
        self._beeps.append(started_at)
        if len(self._beeps) < self.config.required_beeps:
            return False
        self._beeps.clear()
        return True

    def _is_tone_present(self, samples: np.ndarray) -> bool:
        if len(samples) < BLOCK_SIZE:
            samples = np.pad(samples, (0, BLOCK_SIZE - len(samples)))
        elif len(samples) > BLOCK_SIZE:
            samples = samples[:BLOCK_SIZE]

        windowed = samples * np.hanning(BLOCK_SIZE)
        spectrum = np.abs(np.fft.rfft(windowed)) ** 2
        frequencies = np.fft.rfftfreq(BLOCK_SIZE, 1 / SAMPLE_RATE)

        tone_band = np.abs(frequencies - self.config.target_hz) <= self.config.tolerance_hz
        background_band = (
            (frequencies >= self.config.target_hz - 1_100)
            & (frequencies <= self.config.target_hz + 1_100)
            & ~tone_band
        )

        tone_power = float(spectrum[tone_band].sum())
        background_power = float(spectrum[background_band].sum())
        normalization = float(np.hanning(BLOCK_SIZE).sum() ** 2)
        tone_dbfs = 10 * math.log10(max(tone_power / normalization, 1e-20))
        tone_ratio_db = 10 * math.log10(max(tone_power, 1e-20) / max(background_power, 1e-20))
        return (
            tone_dbfs >= self.config.minimum_tone_dbfs
            and tone_ratio_db >= self.config.minimum_tone_ratio_db
        )


def ffmpeg_command(source: str | None, input_device: str) -> list[str]:
    command = ["ffmpeg", "-v", "error"]
    if source:
        command.extend(["-i", source])
    elif sys.platform == "darwin":
        command.extend(["-f", "avfoundation", "-i", input_device])
    else:
        command.extend(["-f", "pulse", "-i", input_device])
    command.extend(["-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"])
    return command


def audio_blocks(source: str | None, input_device: str):
    command = ffmpeg_command(source, input_device)
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as error:
        raise RuntimeError("ffmpeg is required but was not found on PATH") from error

    assert process.stdout is not None
    block_bytes = BLOCK_SIZE * np.dtype("<f4").itemsize
    exhausted = False
    try:
        while data := process.stdout.read(block_bytes):
            samples = np.frombuffer(data, dtype="<f4")
            if len(samples):
                yield samples
        exhausted = True
    finally:
        if not exhausted and process.poll() is None:
            process.terminate()
        process.stdout.close()
        return_code = process.wait()
        assert process.stderr is not None
        error_output = process.stderr.read().decode(errors="replace").strip()
        process.stderr.close()
        if exhausted and return_code:
            detail = f": {error_output}" if error_output else ""
            raise RuntimeError(f"ffmpeg exited with status {return_code}{detail}")


def send_pushover(message: str, title: str) -> None:
    token = os.environ.get("PUSHOVER_COFFEE_TOKEN")
    user = os.environ.get("PUSHOVER_USER")
    if not token or not user:
        raise RuntimeError("PUSHOVER_COFFEE_TOKEN and PUSHOVER_USER must be set")

    payload = urllib.parse.urlencode(
        {"token": token, "user": user, "title": title, "message": message}
    ).encode()
    request = urllib.request.Request(
        "https://api.pushover.net/1/messages.json",
        data=payload,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"Pushover rejected the notification: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Pushover request failed: {error.reason}") from error
    if result.get("status") != 1:
        raise RuntimeError(f"Pushover returned an error: {result.get('errors', result)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect the coffee roaster warm-up beep and send a Pushover alert."
    )
    parser.add_argument("--file", type=Path, help="Analyze an audio file instead of a microphone")
    parser.add_argument(
        "--input-device",
        default=":MacBook Pro Microphone" if sys.platform == "darwin" else "default",
        help="ffmpeg audio input device (default: %(default)s)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print alerts instead of sending them")
    parser.add_argument("--once", action="store_true", help="Exit after the first detection")
    parser.add_argument("--cooldown", type=float, default=120, help="Seconds between alerts")
    parser.add_argument("--required-beeps", type=int, default=3, help="Beep count required for detection")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.required_beeps < 2:
        print("error: --required-beeps must be at least 2", file=sys.stderr)
        return 2
    if args.file and not args.file.is_file():
        print(f"error: audio file not found: {args.file}", file=sys.stderr)
        return 2

    detector = BeepCadenceDetector(DetectionConfig(required_beeps=args.required_beeps))
    source = str(args.file) if args.file else None
    source_name = str(args.file) if args.file else f"microphone {args.input_device}"
    print(f"Listening to {source_name} for the roaster warm-up beep...", flush=True)

    audio_time = 0.0
    last_alert = -math.inf
    try:
        for block in audio_blocks(source, args.input_device):
            if detector.observe(block, audio_time) and audio_time - last_alert >= args.cooldown:
                message = "The coffee roaster is warmed up and ready."
                if args.dry_run:
                    print(f"DRY RUN: {message}", flush=True)
                else:
                    send_pushover(message, "Coffee roaster ready")
                    print("Pushover alert sent.", flush=True)
                last_alert = audio_time
                if args.once:
                    return 0
            audio_time += len(block) / SAMPLE_RATE
        if detector.finish(audio_time) and audio_time - last_alert >= args.cooldown:
            message = "The coffee roaster is warmed up and ready."
            if args.dry_run:
                print(f"DRY RUN: {message}", flush=True)
            else:
                send_pushover(message, "Coffee roaster ready")
                print("Pushover alert sent.", flush=True)
            return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
