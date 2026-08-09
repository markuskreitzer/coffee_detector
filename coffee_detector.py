#!/usr/bin/env python3

import argparse
import json
import math
import os
import selectors
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
INPUT_ACTIVITY_FLOOR = 1e-7
MINIMUM_ACTIVE_SAMPLE_FRACTION = 0.20


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


@dataclass(frozen=True)
class ToneMeasurement:
    peak_hz: float
    tone_dbfs: float
    tone_ratio_db: float


class BeepCadenceDetector:
    def __init__(self, config: DetectionConfig | None = None):
        self.config = config if config is not None else DetectionConfig()
        self._tone_started_at: float | None = None
        self._beeps: deque[float] = deque(maxlen=self.config.required_beeps)

    def observe(self, samples: np.ndarray, timestamp: float) -> bool:
        tone_present = self._is_tone_present(samples)
        if tone_present and self._tone_started_at is None:
            self._tone_started_at = timestamp
        elif not tone_present and self._tone_started_at is not None:
            duration = timestamp - self._tone_started_at
            started_at = self._tone_started_at
            self._tone_started_at = None
            if (
                self.config.minimum_beep_seconds
                <= duration
                <= self.config.maximum_beep_seconds
            ):
                return self._record_beep(started_at)
        return False

    def finish(self, timestamp: float) -> bool:
        if self._tone_started_at is None:
            return False
        duration = timestamp - self._tone_started_at
        started_at = self._tone_started_at
        self._tone_started_at = None
        if (
            self.config.minimum_beep_seconds
            <= duration
            <= self.config.maximum_beep_seconds
        ):
            return self._record_beep(started_at)
        return False

    def _record_beep(self, started_at: float) -> bool:
        if self._beeps:
            gap = started_at - self._beeps[-1]
            if (
                not self.config.minimum_gap_seconds
                <= gap
                <= self.config.maximum_gap_seconds
            ):
                self._beeps.clear()
        self._beeps.append(started_at)
        if len(self._beeps) < self.config.required_beeps:
            return False
        self._beeps.clear()
        return True

    def _is_tone_present(self, samples: np.ndarray) -> bool:
        measurement = measure_tone(samples, self.config)
        return (
            measurement.tone_dbfs >= self.config.minimum_tone_dbfs
            and measurement.tone_ratio_db >= self.config.minimum_tone_ratio_db
        )


def measure_tone(
    samples: np.ndarray, config: DetectionConfig | None = None
) -> ToneMeasurement:
    config = config if config is not None else DetectionConfig()
    if len(samples) < BLOCK_SIZE:
        samples = np.pad(samples, (0, BLOCK_SIZE - len(samples)))
    elif len(samples) > BLOCK_SIZE:
        samples = samples[:BLOCK_SIZE]

    window = np.hanning(BLOCK_SIZE)
    spectrum = np.abs(np.fft.rfft(samples * window)) ** 2
    frequencies = np.fft.rfftfreq(BLOCK_SIZE, 1 / SAMPLE_RATE)

    tone_band = np.abs(frequencies - config.target_hz) <= config.tolerance_hz
    background_band = (
        (frequencies >= config.target_hz - 1_100)
        & (frequencies <= config.target_hz + 1_100)
        & ~tone_band
    )
    analysis_band = (frequencies >= 2_000) & (frequencies <= 6_000)

    tone_power = float(spectrum[tone_band].sum())
    background_power = float(spectrum[background_band].sum())
    normalization = float(window.sum() ** 2)
    tone_dbfs = 10 * math.log10(max(tone_power / normalization, 1e-20))
    tone_ratio_db = 10 * math.log10(
        max(tone_power, 1e-20) / max(background_power, 1e-20)
    )
    analysis_frequencies = frequencies[analysis_band]
    analysis_spectrum = spectrum[analysis_band]
    peak_hz = float(analysis_frequencies[int(np.argmax(analysis_spectrum))])
    return ToneMeasurement(peak_hz, tone_dbfs, tone_ratio_db)


def diagnose_tone_input(
    input_device: str, seconds: float, read_timeout: float
) -> int:
    target_samples = round(seconds * SAMPLE_RATE)
    sample_count = 0
    measurements: list[ToneMeasurement] = []
    config = DetectionConfig()

    for block in audio_blocks(None, input_device, read_timeout):
        remaining = target_samples - sample_count
        samples = block[:remaining]
        sample_count += len(samples)
        measurements.append(measure_tone(samples, config))
        if sample_count >= target_samples:
            break

    if sample_count < target_samples:
        raise RuntimeError("audio input ended before tone diagnosis completed")
    strongest = max(measurements, key=lambda item: item.tone_dbfs)
    best_ratio = max(item.tone_ratio_db for item in measurements)
    matching_blocks = sum(
        item.tone_dbfs >= config.minimum_tone_dbfs
        and item.tone_ratio_db >= config.minimum_tone_ratio_db
        for item in measurements
    )
    print(
        f"Tone diagnosis: {seconds:g}s, strongest peak {strongest.peak_hz:.1f} Hz, "
        f"target level {strongest.tone_dbfs:.1f} dBFS, "
        f"best target ratio {best_ratio:.1f} dB, "
        f"matching blocks {matching_blocks}/{len(measurements)}"
    )
    return 0


class InputHealthMonitor:
    def __init__(
        self,
        window_seconds: float = 10.0,
        activity_floor: float = INPUT_ACTIVITY_FLOOR,
        minimum_active_fraction: float = MINIMUM_ACTIVE_SAMPLE_FRACTION,
    ):
        self.window_samples = max(1, round(window_seconds * SAMPLE_RATE))
        self.activity_floor = activity_floor
        self.minimum_active_fraction = minimum_active_fraction
        self._blocks: deque[tuple[int, int]] = deque()
        self._sample_count = 0
        self._active_count = 0

    def observe(self, samples: np.ndarray) -> bool | None:
        sample_count = len(samples)
        active_count = int(np.count_nonzero(np.abs(samples) > self.activity_floor))
        self._blocks.append((sample_count, active_count))
        self._sample_count += sample_count
        self._active_count += active_count

        while (
            self._blocks
            and self._sample_count - self._blocks[0][0] >= self.window_samples
        ):
            removed_samples, removed_active = self._blocks.popleft()
            self._sample_count -= removed_samples
            self._active_count -= removed_active

        if self._sample_count < self.window_samples:
            return None
        return self._active_count / self._sample_count >= self.minimum_active_fraction


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


def audio_blocks(source: str | None, input_device: str, read_timeout: float = 10.0):
    command = ffmpeg_command(source, input_device)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except FileNotFoundError as error:
        raise RuntimeError("ffmpeg is required but was not found on PATH") from error

    assert process.stdout is not None
    block_bytes = BLOCK_SIZE * np.dtype("<f4").itemsize
    pending = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    exhausted = False
    try:
        while True:
            if not selector.select(read_timeout):
                raise RuntimeError(
                    f"audio input produced no data for {read_timeout:g} seconds"
                )
            data = os.read(process.stdout.fileno(), block_bytes - len(pending))
            if not data:
                exhausted = True
                if pending:
                    yield np.frombuffer(bytes(pending), dtype="<f4")
                break
            pending.extend(data)
            if len(pending) == block_bytes:
                yield np.frombuffer(bytes(pending), dtype="<f4")
                pending.clear()
    finally:
        selector.close()
        if not exhausted and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
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
        raise RuntimeError(
            f"Pushover returned an error: {result.get('errors', result)}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect the coffee roaster warm-up beep and send a Pushover alert."
    )
    parser.add_argument(
        "--file", type=Path, help="Analyze an audio file instead of a microphone"
    )
    parser.add_argument(
        "--input-device",
        default=":MacBook Pro Microphone" if sys.platform == "darwin" else "default",
        help="ffmpeg audio input device (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print alerts instead of sending them"
    )
    parser.add_argument(
        "--once", action="store_true", help="Exit after the first detection"
    )
    parser.add_argument(
        "--cooldown", type=float, default=120, help="Seconds between alerts"
    )
    parser.add_argument(
        "--required-beeps",
        type=int,
        default=3,
        help="Beep count required for detection",
    )
    parser.add_argument(
        "--audio-read-timeout",
        type=float,
        default=10,
        help="Seconds to wait for microphone data (default: %(default)s)",
    )
    parser.add_argument(
        "--input-health-window",
        type=float,
        default=10,
        help="Seconds used to reject silent microphone input (default: %(default)s)",
    )
    parser.add_argument(
        "--check-input",
        type=float,
        metavar="SECONDS",
        help="Measure microphone health for a fixed duration and exit",
    )
    parser.add_argument(
        "--diagnose-tone",
        type=float,
        metavar="SECONDS",
        help="Measure the live target tone without recording audio",
    )
    return parser.parse_args()


def check_audio_input(input_device: str, seconds: float, read_timeout: float) -> int:
    target_samples = round(seconds * SAMPLE_RATE)
    sample_count = 0
    active_count = 0
    sum_squares = 0.0
    peak = 0.0

    for block in audio_blocks(None, input_device, read_timeout):
        remaining = target_samples - sample_count
        samples = block[:remaining]
        sample_count += len(samples)
        active_count += int(np.count_nonzero(np.abs(samples) > INPUT_ACTIVITY_FLOOR))
        float_samples = samples.astype(np.float64)
        sum_squares += float(np.dot(float_samples, float_samples))
        peak = max(peak, float(np.max(np.abs(samples))))
        if sample_count >= target_samples:
            break

    if sample_count < target_samples:
        raise RuntimeError("audio input ended before the input check completed")
    active_fraction = active_count / sample_count
    rms = math.sqrt(sum_squares / sample_count)
    rms_dbfs = 20 * math.log10(max(rms, 1e-10))
    print(
        f"Input check: {seconds:g}s, RMS {rms_dbfs:.1f} dBFS, "
        f"peak {peak:.6f}, active samples {active_fraction:.1%}"
    )
    if active_fraction < MINIMUM_ACTIVE_SAMPLE_FRACTION:
        raise RuntimeError("microphone input is silent or invalid")
    return 0


def main() -> int:
    args = parse_args()
    if args.required_beeps < 2:
        print("error: --required-beeps must be at least 2", file=sys.stderr)
        return 2
    if args.file and not args.file.is_file():
        print(f"error: audio file not found: {args.file}", file=sys.stderr)
        return 2
    if args.audio_read_timeout <= 0 or args.input_health_window <= 0:
        print("error: audio timeout values must be positive", file=sys.stderr)
        return 2
    if args.check_input is not None and args.check_input <= 0:
        print("error: --check-input must be positive", file=sys.stderr)
        return 2
    if args.diagnose_tone is not None and args.diagnose_tone <= 0:
        print("error: --diagnose-tone must be positive", file=sys.stderr)
        return 2
    if args.file and args.check_input is not None:
        print("error: --check-input cannot be combined with --file", file=sys.stderr)
        return 2
    if args.file and args.diagnose_tone is not None:
        print("error: --diagnose-tone cannot be combined with --file", file=sys.stderr)
        return 2
    if args.check_input is not None and args.diagnose_tone is not None:
        print(
            "error: --check-input cannot be combined with --diagnose-tone",
            file=sys.stderr,
        )
        return 2

    detector = BeepCadenceDetector(DetectionConfig(required_beeps=args.required_beeps))
    source = str(args.file) if args.file else None
    source_name = str(args.file) if args.file else f"microphone {args.input_device}"

    audio_time = 0.0
    last_alert = -math.inf
    try:
        if args.check_input is not None:
            return check_audio_input(
                args.input_device,
                args.check_input,
                args.audio_read_timeout,
            )
        if args.diagnose_tone is not None:
            return diagnose_tone_input(
                args.input_device,
                args.diagnose_tone,
                args.audio_read_timeout,
            )

        print(f"Listening to {source_name} for the roaster warm-up beep...", flush=True)
        input_health = (
            InputHealthMonitor(window_seconds=args.input_health_window)
            if source is None
            else None
        )
        for block in audio_blocks(source, args.input_device, args.audio_read_timeout):
            if input_health is not None and input_health.observe(block) is False:
                raise RuntimeError(
                    f"microphone input remained silent or invalid for "
                    f"{args.input_health_window:g} seconds"
                )
            if (
                detector.observe(block, audio_time)
                and audio_time - last_alert >= args.cooldown
            ):
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
