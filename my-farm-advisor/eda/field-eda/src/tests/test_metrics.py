"""Unit tests for field-EDA metric primitives.

Run from the src directory with the runtime virtualenv:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import weather  # noqa: E402


class GddTests(unittest.TestCase):
    def test_base_and_cap(self):
        tmax = pd.Series([20.0, 40.0, 5.0, 15.0])
        tmin = pd.Series([10.0, 30.0, 0.0, 5.0])
        result = weather.daily_gdd(tmax, tmin, base=10.0, cap=30.0).tolist()
        # means: 15 -> 5, 35 -> capped at 30 -> 20, 2.5 -> 0, 10 -> 0
        self.assertEqual(result, [5.0, 20.0, 0.0, 0.0])

    def test_uncapped_can_exceed_cap(self):
        tmax = pd.Series([40.0])
        tmin = pd.Series([30.0])
        capped = weather.daily_gdd(tmax, tmin, base=10.0, cap=30.0).iloc[0]
        uncapped = weather.daily_gdd(tmax, tmin, base=10.0, cap=None).iloc[0]
        self.assertAlmostEqual(capped, 20.0)
        self.assertAlmostEqual(uncapped, 25.0)

    def test_cap_below_base_is_rejected(self):
        with self.assertRaises(ValueError):
            weather.daily_gdd(pd.Series([20.0]), pd.Series([10.0]), base=10.0, cap=5.0)

    def test_negative_temperatures_do_not_generate_gdd(self):
        result = weather.daily_gdd(pd.Series([-5.0]), pd.Series([-10.0]))
        self.assertEqual(result.iloc[0], 0.0)


class SeasonTests(unittest.TestCase):
    def test_boundaries_are_inclusive(self):
        dates = pd.Series(
            pd.to_datetime(["2021-04-30", "2021-05-01", "2021-09-30", "2021-10-01"])
        )
        mask = weather.season_mask(dates, (5, 1), (9, 30)).tolist()
        self.assertEqual(mask, [False, True, True, False])

    def test_season_day_count(self):
        self.assertEqual(weather.expected_season_days(2021), 153)
        self.assertEqual(weather.expected_season_days(2024), 153)

    def test_year_day_count_handles_leap_year(self):
        self.assertEqual(weather.expected_year_days(2023), 365)
        self.assertEqual(weather.expected_year_days(2024), 366)

    def test_inverted_season_is_rejected(self):
        dates = pd.Series(pd.to_datetime(["2021-06-01"]))
        with self.assertRaises(ValueError):
            weather.season_mask(dates, (10, 1), (4, 30))

    def test_parse_mmdd(self):
        self.assertEqual(weather.parse_mmdd("5-1"), (5, 1))
        self.assertEqual(weather.parse_mmdd("09-30"), (9, 30))


class FieldMeanTests(unittest.TestCase):
    def test_percent_of_own_mean(self):
        values = pd.Series([100.0, 200.0, 300.0])
        keys = pd.Series(["a", "a", "a"])
        result = weather.percent_of_field_mean(values, keys).tolist()
        self.assertEqual(result, [50.0, 100.0, 150.0])

    def test_zero_mean_yields_nan_not_inf(self):
        values = pd.Series([0.0, 0.0])
        keys = pd.Series(["a", "a"])
        result = weather.percent_of_field_mean(values, keys)
        self.assertTrue(result.isna().all())


if __name__ == "__main__":
    unittest.main()
