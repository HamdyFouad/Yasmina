"""Golden-dataset evaluation for DeepSeek specification extraction.

The offline integrity test always runs. The accuracy gate runs only when
``DEEPSEEK_API_KEY`` is present so prompt drift is measured against the same
baseline in staging/production CI without making the default suite flaky.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from src.agents.deepseek import DeepSeekClient, RawItemSpecs
from src.config import Settings
from src.models.enums import MaterialType, StandardizedColor
from src.parsers.normalizers import (
    normalize_color,
    normalize_glove_size,
    normalize_material,
    normalize_pack_size,
)

GOLDEN_PATH = Path(__file__).parent / "golden" / "extraction_cases.json"
BASELINE_FIELD_ACCURACY = 0.85


def load_cases() -> list[dict[str, Any]]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def normalize_specs(specs: RawItemSpecs) -> dict[str, Any]:
    material = normalize_material(specs.material)
    color = normalize_color(specs.color)
    return {
        "material": material.value if material is not MaterialType.NOT_APPLICABLE else "NOT_APPLICABLE",
        "color": color.value if color is not StandardizedColor.UNKNOWN else "UNKNOWN",
        "glove_size": normalize_glove_size(specs.glove_size).value,
        "pack_size": normalize_pack_size(specs.pack_size),
    }


def make_settings() -> Settings:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    return Settings(
        _env_file=None,
        database_url=None,
        app_env="test",
        jwt_secret_key="eval-jwt-secret-with-at-least-32-bytes",
        api_admin_password="eval-admin-password",
        deepseek_api_key=api_key,
        deepseek_enabled=True,
    )


def test_golden_extraction_dataset_is_well_formed() -> None:
    cases = load_cases()

    assert len(cases) >= 12
    assert len({case["id"] for case in cases}) == len(cases)
    categories = {case["category"] for case in cases}
    assert {"paper", "gloves", "waste"} <= categories
    for category in ("paper", "gloves", "waste"):
        assert sum(1 for case in cases if case["category"] == category) >= 4
    for case in cases:
        assert case["text"].strip()
        assert set(case["expected"]) == {"material", "color", "glove_size", "pack_size"}
        expected = case["expected"]
        assert expected["material"] in {member.value for member in MaterialType}
        assert expected["color"] in {member.value for member in StandardizedColor}
        assert expected["glove_size"] in {"XS", "S", "M", "L", "XL", "XXL", "NOT_APPLICABLE"}
        assert isinstance(expected["pack_size"], int) and expected["pack_size"] > 0


@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not configured")
def test_deepseek_extraction_accuracy_against_golden_baseline() -> None:
    client = DeepSeekClient.from_settings(make_settings())
    cases = load_cases()
    correct = 0
    total = 0
    failures: list[str] = []

    for case in cases:
        actual = normalize_specs(client.extract_item_specs(case["text"]))
        for field, expected in case["expected"].items():
            total += 1
            if actual[field] == expected:
                correct += 1
            else:
                failures.append(f"{case['id']}.{field}: expected {expected!r}, got {actual[field]!r}")

    accuracy = correct / total if total else 0.0
    print(f"DeepSeek extraction field accuracy: {accuracy:.3f} ({correct}/{total})")
    if failures:
        print("Mismatches:\n" + "\n".join(failures))
    assert accuracy >= BASELINE_FIELD_ACCURACY
