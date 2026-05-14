"""USDA FoodData Central client.

All macro lookups go through here. The meal agent must never hardcode
protein or kcal values — brand variation (especially tofu) makes that unreliable.

API docs: https://fdc.nal.usda.gov/api-guide.html
Free API key: https://fdc.nal.usda.gov/api-key-signup.html
"""

from __future__ import annotations

import httpx

import config

_BASE = "https://api.nal.usda.gov/fdc/v1"
_PROTEIN_ID = 1003
_KCAL_ID = 1008


async def search(query: str, page_size: int = 5) -> list[dict]:
    """Search USDA for a food by name and return the top results.

    Each result dict contains:
      fdcId, description, protein_per_100g, kcal_per_100g

    Filters to 'Survey (FNDDS)' and 'SR Legacy' data types — these have
    the most complete nutrient profiles for common foods.
    """
    params = {
        "query": query,
        "api_key": config.USDA_API_KEY,
        "dataType": "Survey (FNDDS),SR Legacy,Foundation",
        "pageSize": page_size,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{_BASE}/foods/search", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

    results = []
    for food in data.get("foods", []):
        nutrients = food.get("foodNutrients", [])
        results.append({
            "fdcId": food.get("fdcId"),
            "description": food.get("description", query),
            "protein_per_100g": _extract_nutrient(nutrients, _PROTEIN_ID),
            "kcal_per_100g": _extract_nutrient(nutrients, _KCAL_ID),
        })
    return results


async def lookup_macros(query: str, quantity_g: float) -> dict:
    """Return scaled protein and kcal for a food at a given quantity.

    This is the main function the meal agent calls. It searches USDA,
    picks the best match (first result), scales from per-100g to quantity_g,
    and returns a dict ready to populate a FoodLog.

    If USDA returns no results, falls back to estimated zeros and flags source
    as 'estimated' so the agent can warn the user.

    Returns:
        {
            "description": str,      # USDA food name
            "quantity_g": float,
            "protein_g": float,
            "kcal": float,
            "source": "usda" | "estimated",
        }
    """
    results = await search(query)

    if not results:
        return {
            "description": query,
            "quantity_g": quantity_g,
            "protein_g": 0.0,
            "kcal": 0.0,
            "source": "estimated",
        }

    best = results[0]
    scale = quantity_g / 100.0
    return {
        "description": best["description"],
        "quantity_g": quantity_g,
        "protein_g": round(best["protein_per_100g"] * scale, 1),
        "kcal": round(best["kcal_per_100g"] * scale, 0),
        "source": "usda",
    }


def _extract_nutrient(nutrients: list[dict], nutrient_id: int) -> float:
    """Pull a single nutrient value from a USDA foodNutrients list by nutrient ID."""
    for n in nutrients:
        if n.get("nutrientId") == nutrient_id:
            return float(n.get("value", 0.0))
    return 0.0
