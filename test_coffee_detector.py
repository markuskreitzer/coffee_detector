import sys
import unittest
from unittest.mock import patch

import numpy as np

from coffee_detector import (
    BLOCK_SIZE,
    SAMPLE_RATE,
    BeepCadenceDetector,
    InputHealthMonitor,
    audio_blocks,
)


def tone(frequency: float, amplitude: float = 0.5) -> np.ndarray:
    elapsed = np.arange(BLOCK_SIZE) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * frequency * elapsed)).astype(np.float32)


class BeepCadenceDetectorTests(unittest.TestCase):
    def test_requires_three_beeps_at_one_second_intervals(self):
        detector = BeepCadenceDetector()
        signal = tone(4_105)
        silence = np.zeros(BLOCK_SIZE, dtype=np.float32)

        detections = []
        for second in (0.0, 1.0, 2.0):
            detections.append(detector.observe(signal, second))
            detections.append(detector.observe(signal, second + 0.128))
            detections.append(detector.observe(silence, second + 0.256))

        self.assertEqual(detections.count(True), 1)

    def test_detects_quiet_beeps(self):
        detector = BeepCadenceDetector()
        signal = tone(4_105, amplitude=0.0005)
        silence = np.zeros(BLOCK_SIZE, dtype=np.float32)

        detected = False
        for second in (0.0, 1.0, 2.0):
            detected |= detector.observe(signal, second)
            detected |= detector.observe(signal, second + 0.128)
            detected |= detector.observe(silence, second + 0.256)

        self.assertTrue(detected)

    def test_rejects_wrong_frequency(self):
        detector = BeepCadenceDetector()
        signal = tone(2_000)
        silence = np.zeros(BLOCK_SIZE, dtype=np.float32)

        for second in (0.0, 1.0, 2.0, 3.0):
            self.assertFalse(detector.observe(signal, second))
            self.assertFalse(detector.observe(signal, second + 0.128))
            self.assertFalse(detector.observe(silence, second + 0.256))

    def test_rejects_irregular_cadence(self):
        detector = BeepCadenceDetector()
        signal = tone(4_105)
        silence = np.zeros(BLOCK_SIZE, dtype=np.float32)

        for second in (0.0, 0.4, 2.4):
            self.assertFalse(detector.observe(signal, second))
            self.assertFalse(detector.observe(signal, second + 0.128))
            self.assertFalse(detector.observe(silence, second + 0.256))


class InputHealthMonitorTests(unittest.TestCase):
    def observe_window(
        self, monitor: InputHealthMonitor, block: np.ndarray
    ) -> bool | None:
        result = None
        for _ in range(10):
            result = monitor.observe(block)
            if result is not None:
                return result
        return result

    def test_accepts_continuous_microphone_signal(self):
        monitor = InputHealthMonitor(window_seconds=0.5)
        signal = np.full(BLOCK_SIZE, 0.0001, dtype=np.float32)

        self.assertTrue(self.observe_window(monitor, signal))

    def test_rejects_digital_silence(self):
        monitor = InputHealthMonitor(window_seconds=0.5)
        silence = np.zeros(BLOCK_SIZE, dtype=np.float32)

        self.assertFalse(self.observe_window(monitor, silence))

    def test_rejects_sparse_clipped_artifacts(self):
        monitor = InputHealthMonitor(window_seconds=0.5)
        artifacts = np.zeros(BLOCK_SIZE, dtype=np.float32)
        artifacts[: BLOCK_SIZE // 10] = 1.0

        self.assertFalse(self.observe_window(monitor, artifacts))


class AudioBlocksTests(unittest.TestCase):
    @patch(
        "coffee_detector.ffmpeg_command",
        return_value=[sys.executable, "-c", "import time; time.sleep(1)"],
    )
    def test_times_out_when_input_produces_no_frames(self, _mock_command):
        blocks = audio_blocks(None, "default", read_timeout=0.05)

        with self.assertRaisesRegex(RuntimeError, "produced no data"):
            next(blocks)


if __name__ == "__main__":
    unittest.main()
