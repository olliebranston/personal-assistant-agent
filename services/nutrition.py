"""USDA FoodData Central client with retry logic.

All macro lookups go through here. The meal agent must never hardcode
protein or kcal values — brand variation (especially tofu) makes that unreliable.

API docs: https://fdc.nal.usda.gov/api-guide.html
Free API key: https://fdc.nal.usda.gov/api-key-signup.html
"""

from __future__ import annotations

import asyncio
import logging

import httpx

import config

logger = logging.getLogger(__name__)

_BASE = "https://api.nal.usda.gov/fdc/v1"
_PROTEIN_ID = 1003
_KCAL_ID = 1008


async def search(query: str, page_size: int = 5) -> list[dict]:
    """Search USDA for a food by name. Retries up to 3 times on network failure.

    Each result dict contains:
      fdcId, description, protein_per_100g, kcal_per_100g

    Returns an empty list (rather than raising) after exhausting retries so the
    meal agent can fall back to an 'estimated' source flag gracefully.
    """
    params = {
        "query": query,
        "api_key": config.USDA_API_KEY,
        "dataType": "Survey (FNDDS),SR Legacy,Foundation",
        "pageSize": page_size,
    }

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_BASE}/foods/search", params=params, timeout=10
                )
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

        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt < 2:
                wait = 2 ** attempt
                logger.warning(
                    "USDA attempt %d/3 failed (%s), retrying in %ds",
                    attempt + 1, type(exc).__name__, wait,
                )
                await asyncio.sleep(wait)

    logger.error("USDA search failed after 3 attempts for query '%s': %s", query, last_exc)
    return []


async def lookup_macros(query: str, quantity_g: float) -> dict:
    """Return scaled protein and kcal for a food at a given quantity.

    Searches USDA, picks the best match (first result), scales from per-100g
    to quantity_g. Falls back to zeros with source='estimated' if search fails,
    so the caller can warn Ollie rather than silently logging bad data.

    Returns:
        {description, quantity_g, protein_g, kcal, source: "usda"|"estimated"}
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
    """Pull a single nutrient value from a USDA foodNutrients list by ID."""
    for n in nutrients:
        if n.get("nutrientId") == nutrient_id:
            return float(n.get("value", 0.0))
    return 0.0
