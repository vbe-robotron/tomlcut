from __future__ import annotations

import math
import unittest

from tomlcut.timecode import (
    TimecodeError,
    format_ffmpeg_time,
    format_timecode,
    parse_timecode,
)


class ParseTimecodeTests(unittest.TestCase):
    def test_number(self) -> None:
        self.assertEqual(parse_timecode(12), 12.0)
        self.assertEqual(parse_timecode(12.5), 12.5)

    def test_seconds_string(self) -> None:
        self.assertEqual(parse_timecode("12.25"), 12.25)
        self.assertEqual(parse_timecode(" 0 "), 0.0)

    def test_minutes_seconds(self) -> None:
        self.assertEqual(parse_timecode("01:23.5"), 83.5)
        self.assertEqual(parse_timecode("90:00"), 5400.0)

    def test_hours_minutes_seconds(self) -> None:
        self.assertEqual(parse_timecode("01:02:03.25"), 3723.25)
        self.assertEqual(parse_timecode("100:00:00"), 360000.0)

    def test_invalid_values(self) -> None:
        invalid = [
            True,
            None,
            -1,
            "-1",
            float("nan"),
            float("inf"),
            "",
            "abc",
            "1e3",
            "1:60",
            "1:2:60",
            "1:2:3:4",
            "1::2",
            "1.5:2",
            "01:00.000.1",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(TimecodeError):
                parse_timecode(value)

    def test_large_number_is_finite(self) -> None:
        self.assertTrue(math.isfinite(parse_timecode(1e9)))


class FormatTimecodeTests(unittest.TestCase):
    def test_formats(self) -> None:
        self.assertEqual(format_timecode(0), "00:00")
        self.assertEqual(format_timecode(10), "00:10")
        self.assertEqual(format_timecode(83.5), "01:23.5")
        self.assertEqual(format_timecode(3723.25), "01:02:03.25")
        self.assertEqual(format_timecode(0.005), "00:00.005")

    def test_rounding_carries(self) -> None:
        self.assertEqual(format_timecode(59.9999), "01:00")

    def test_ffmpeg_format(self) -> None:
        self.assertEqual(format_ffmpeg_time(0), "0")
        self.assertEqual(format_ffmpeg_time(12.5), "12.5")
        self.assertEqual(format_ffmpeg_time(1 / 3), "0.333333")


if __name__ == "__main__":
    unittest.main()
