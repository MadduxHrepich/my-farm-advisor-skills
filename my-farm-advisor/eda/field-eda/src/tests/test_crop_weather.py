"""Unit tests for the crop-weather join and pairing.

Run from the src directory with the runtime virtualenv:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import crop_weather  # noqa: E402


def _weather(
    field: str,
    year: int,
    precip: float,
    gdd: float,
    grower: str = "g1",
    state: str = "IA",
) -> dict[str, object]:
    return {
        "grower": grower,
        "state": state,
        "farm": "farm",
        "field_id": field,
        "year": year,
        "weather_cell": f"{state}-cell",
        "precip_mm": precip,
        "gdd_c": gdd,
        "heat_days_35c": 1,
    }


def _cdl(field: str, year: int, crop: str, share: float = 80.0) -> dict[str, object]:
    return {
        "field_id": field,
        "year": year,
        "dominant_crop": crop,
        "dominant_pct": share,
    }


class JoinTests(unittest.TestCase):
    def test_join_preserves_field_years_and_attaches_crop(self):
        weather = pd.DataFrame(
            [_weather("f1", 2021, 100.0, 1000.0), _weather("f1", 2022, 120.0, 1200.0)]
        )
        cdl = pd.DataFrame(
            [_cdl("f1", 2021, "Corn"), _cdl("f1", 2022, "Soybeans")]
        )
        joined = crop_weather.join_crop_weather(weather, cdl)
        self.assertEqual(len(joined), 2)
        self.assertEqual(joined["dominant_crop"].tolist(), ["Corn", "Soybeans"])
        self.assertEqual(joined["crop_share_pct"].tolist(), [80.0, 80.0])


class PairingTests(unittest.TestCase):
    def _paired_frames(self):
        weather = pd.DataFrame(
            [
                _weather("f1", 2021, 100.0, 1000.0),
                _weather("f1", 2022, 120.0, 1200.0),
                _weather("f1", 2023, 80.0, 900.0),
                _weather("f1", 2024, 100.0, 1100.0),
            ]
        )
        cdl = pd.DataFrame(
            [
                _cdl("f1", 2021, "Corn"),
                _cdl("f1", 2022, "Corn"),
                _cdl("f1", 2023, "Soybeans"),
                _cdl("f1", 2024, "Soybeans"),
            ]
        )
        return weather, cdl

    def test_difference_is_mean_corn_year_minus_mean_soybean_year(self):
        weather, cdl = self._paired_frames()
        paired = crop_weather.paired_differences(
            crop_weather.join_crop_weather(weather, cdl)
        )
        self.assertEqual(len(paired), 1)
        row = paired.iloc[0]
        # corn precip mean 110, soybean mean 90; corn GDD mean 1100, soybean 1000
        self.assertAlmostEqual(row["delta_precip_mm"], 20.0)
        self.assertAlmostEqual(row["delta_gdd_c"], 100.0)
        self.assertEqual(row["years_corn"], 2)
        self.assertEqual(row["years_soybeans"], 2)

    def test_field_with_one_crop_is_not_paired(self):
        weather = pd.DataFrame([_weather("f2", 2021, 100.0, 1000.0)])
        cdl = pd.DataFrame([_cdl("f2", 2021, "Corn")])
        paired = crop_weather.paired_differences(
            crop_weather.join_crop_weather(weather, cdl)
        )
        self.assertTrue(paired.empty)


class CheckTests(unittest.TestCase):
    def test_valid_pairing_passes_every_check(self):
        weather = pd.DataFrame(
            [
                _weather("f1", 2021, 100.0, 1000.0),
                _weather("f1", 2022, 110.0, 1100.0),
            ]
        )
        cdl = pd.DataFrame(
            [_cdl("f1", 2021, "Corn"), _cdl("f1", 2022, "Soybeans")]
        )
        bundle = crop_weather.build(weather, cdl)
        self.assertTrue(all(item.ok for item in bundle.checks), bundle.checks)

    def test_unexpected_crop_label_fails_the_crop_check(self):
        weather = pd.DataFrame(
            [
                _weather("f1", 2021, 100.0, 1000.0),
                _weather("f1", 2022, 110.0, 1100.0),
            ]
        )
        cdl = pd.DataFrame(
            [_cdl("f1", 2021, "Wheat"), _cdl("f1", 2022, "Soybeans")]
        )
        bundle = crop_weather.build(weather, cdl)
        crop_check = next(
            item for item in bundle.checks if item.name == "crop labels are corn or soybean"
        )
        self.assertFalse(crop_check.ok)

    def test_missing_crop_label_fails_join_check(self):
        weather = pd.DataFrame([_weather("f1", 2021, 100.0, 1000.0)])
        cdl = pd.DataFrame([_cdl("f9", 2021, "Corn")])
        bundle = crop_weather.build(weather, cdl)
        join_check = next(
            item for item in bundle.checks if item.name == "crop-weather join complete"
        )
        self.assertFalse(join_check.ok)


if __name__ == "__main__":
    unittest.main()
