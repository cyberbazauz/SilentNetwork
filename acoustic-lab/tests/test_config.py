"""Проверки конфигурации профилей BFSK."""

from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError, replace

import pytest

from snlab.config import (
    DEFAULT_PROFILE,
    PROFILES,
    BfskProfile,
    ProfileError,
    get_profile,
)


def test_default_profile_is_consistent() -> None:
    profile = DEFAULT_PROFILE
    assert profile.samples_per_symbol == 160
    assert profile.bit_rate_bps == pytest.approx(300.0)
    assert profile.mark_hz != profile.space_hz


def test_known_profiles_are_indexed() -> None:
    assert set(PROFILES) == {"bfsk-16k", "bfsk-18k"}
    assert get_profile("bfsk-18k") is PROFILES["bfsk-18k"]


def test_unknown_profile_raises() -> None:
    with pytest.raises(ProfileError, match="неизвестный профиль"):
        get_profile("ofdm-42")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mark_hz", 19_000.0, "различаться"),
        ("mark_hz", 30_000.0, "вне исследуемого диапазона"),
        ("mark_hz", math.nan, "конечным числом"),
        ("sample_rate", 8_000, "Найквиста"),
        ("symbol_rate", 700.0, "делиться на symbol_rate"),
        ("symbol_rate", 0.5, "symbol_rate должен быть в диапазоне"),
        ("amplitude", 0.0, "amplitude"),
        ("amplitude", 1.5, "amplitude"),
    ],
)
def test_invalid_profiles_rejected(field: str, value: float, message: str) -> None:
    with pytest.raises(ProfileError, match=message):
        replace(DEFAULT_PROFILE, **{field: value})


def test_empty_name_rejected() -> None:
    with pytest.raises(ProfileError, match="имя профиля"):
        BfskProfile(name="   ")


def test_profile_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        DEFAULT_PROFILE.mark_hz = 1.0  # type: ignore[misc]


def test_as_dict_is_json_serializable() -> None:
    payload = DEFAULT_PROFILE.as_dict()
    assert payload["samples_per_symbol"] == 160
    assert json.loads(json.dumps(payload))["name"] == DEFAULT_PROFILE.name
