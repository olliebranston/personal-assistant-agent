"""Tests for services/nutrition.py — search() (the USDA network call) is
always monkeypatched, so nothing here hits the real USDA API."""

from __future__ import annotations

import pytest

import services.nutrition as nutrition_svc
from services.nutrition import lookup_macros


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner


@pytest.mark.asyncio
async def test_lookup_macros_uses_first_plausible_usda_candidate(monkeypatch):
    monkeypatch.setattr(
        nutrition_svc,
        "search",
        _async_return([
            {"fdcId": 1, "description": "Chicken breast, raw", "protein_per_100g": 31.0, "kcal_per_100g": 165.0},
        ]),
    )

    result = await lookup_macros("chicken breast raw", 200)

    assert result["source"] == "usda"
    assert result["protein_g"] == 62.0
    assert result["kcal"] == 330.0


@pytest.mark.asyncio
async def test_lookup_macros_skips_implausible_candidate_and_uses_next(monkeypatch):
    # A mismatched product (e.g. a supplement or seasoning) with absurd
    # protein/100g must not silently win just because it's first.
    monkeypatch.setattr(
        nutrition_svc,
        "search",
        _async_return([
            {"fdcId": 1, "description": "Bad match", "protein_per_100g": 400.0, "kcal_per_100g": 165.0},
            {"fdcId": 2, "description": "Chicken breast, raw", "protein_per_100g": 31.0, "kcal_per_100g": 165.0},
        ]),
    )

    result = await lookup_macros("chicken breast", 100)

    assert result["source"] == "usda"
    assert result["protein_g"] == 31.0


@pytest.mark.asyncio
async def test_lookup_macros_falls_through_to_fallback_table_when_all_usda_implausible(monkeypatch):
    monkeypatch.setattr(
        nutrition_svc,
        "search",
        _async_return([
            {"fdcId": 1, "description": "Bad match", "protein_per_100g": 400.0, "kcal_per_100g": 2000.0},
        ]),
    )

    result = await lookup_macros("chicken", 100)  # "chicken" matches the fallback table

    assert result["source"] == "reference"
    assert result["protein_g"] == 31.0  # "chicken" substring-matches "chicken breast" first


@pytest.mark.asyncio
async def test_lookup_macros_returns_zero_estimate_on_total_miss(monkeypatch):
    monkeypatch.setattr(nutrition_svc, "search", _async_return([]))

    result = await lookup_macros("some completely unknown food xyz", 100)

    assert result["source"] == "estimated"
    assert result["protein_g"] == 0.0
    assert result["kcal"] == 0.0


def test_is_plausible_bounds():
    assert nutrition_svc._is_plausible(31.0, 165.0) is True
    assert nutrition_svc._is_plausible(400.0, 165.0) is False
    assert nutrition_svc._is_plausible(31.0, 2000.0) is False
    assert nutrition_svc._is_plausible(0.0, 884.0) is True  # olive oil edge case
