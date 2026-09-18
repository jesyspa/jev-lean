import unittest

from jevlean.calibration import check, run, summarize


class CalibrationTests(unittest.TestCase):
    def test_frozen_calibration_replays(self) -> None:
        summary = summarize(run())
        check(summary)
        self.assertEqual(summary["modes"]["accumulated"]["fixtures"], 6)


if __name__ == "__main__":
    unittest.main()
