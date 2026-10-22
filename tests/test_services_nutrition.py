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


# ── Traps found in the live food log (22 Oct 2026 audit) ────────────────────


def test_is_plausible_rejects_a_candidate_with_no_protein_and_no_energy():
    # VM: "250g chicken breast" was logged at 0g protein / 0 kcal from USDA —
    # a nutrient-less record passed the bounds because 0 is "in range".
    assert nutrition_svc._is_plausible(0.0, 0.0) is False


def test_is_plausible_rejects_more_protein_energy_than_total_energy():
    # Protein is 4 kcal/g, so protein can't supply more energy than the food
    # has. VM: a "salmon" entry came out at 400g protein vs 60 kcal per 100g.
    assert nutrition_svc._is_plausible(45.0, 60.0) is False
    # Real lean, high-protein foods still pass.
    for protein, kcal in [(31.0, 165.0), (78.0, 352.0), (24.0, 99.0), (11.0, 52.0), (90.0, 370.0)]:
        assert nutrition_svc._is_plausible(protein, kcal) is True, (protein, kcal)


@pytest.mark.asyncio
async def test_lookup_macros_skips_a_zero_nutrient_usda_record(monkeypatch):
    monkeypatch.setattr(
        nutrition_svc,
        "search",
        _async_return([
            {"fdcId": 1, "description": "Chicken, breast (no nutrients)", "protein_per_100g": 0.0, "kcal_per_100g": 0.0},
            {"fdcId": 2, "description": "Chicken breast, raw", "protein_per_100g": 31.0, "kcal_per_100g": 165.0},
        ]),
    )

    result = await lookup_macros("chicken breast", 250)

    assert result["protein_g"] == 77.5


def test_parse_usda_food_reads_foundation_energy_when_kcal_id_missing():
    # Foundation-type USDA foods report energy under the Atwater IDs
    # (2047/2048) rather than 1008, which left kcal at 0.
    food = {
        "fdcId": 9, "description": "Chicken, breast, raw",
        "foodNutrients": [
            {"nutrientId": 1003, "value": 22.5},
            {"nutrientId": 2047, "value": 120.0},
        ],
    }

    parsed = nutrition_svc._parse_usda_food(food, "chicken")

    assert parsed["protein_per_100g"] == 22.5
    assert parsed["kcal_per_100g"] == 120.0


def test_pre_lookup_does_not_match_inside_other_words():
    # "oat" in "goat cheese" and "egg" in "eggplant" were matched as plain
    # substrings, giving goat's cheese oat macros and aubergine egg macros.
    assert nutrition_svc._pre_lookup("goat cheese") is None
    assert nutrition_svc._pre_lookup("eggplant") is None
    assert nutrition_svc._pre_lookup("licorice") is None
    # Genuine matches still work.
    assert nutrition_svc._pre_lookup("oats") == (13.0, 389.0)
    assert nutrition_svc._pre_lookup("porridge oats") == (13.0, 389.0)
    assert nutrition_svc._pre_lookup("scrambled eggs") == (12.0, 140.0)
    assert nutrition_svc._pre_lookup("basmati rice") == (2.7, 130.0)


def test_fallback_lookup_does_not_match_inside_other_words():
    assert nutrition_svc._fallback_lookup("eggplant") is None
    assert nutrition_svc._fallback_lookup("licorice") is None
    assert nutrition_svc._fallback_lookup("boiled eggs") == (13.0, 143.0)


def test_whey_fallback_is_per_100g_not_per_scoop():
    # The table had whey at 25g protein / 120 kcal — a per-scoop label value,
    # not per 100g — so any whey logged via the fallback was under-counted by
    # about two thirds. USDA's own whey record in Ollie's log: 78.2g / 352 kcal.
    assert nutrition_svc._fallback_lookup("whey protein powder") == (78.0, 352.0)
    assert nutrition_svc._fallback_lookup("protein powder") == (78.0, 352.0)
