"""USDA FoodData Central client with retry logic and hardcoded fallback table.

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

# Hardcoded fallback for 30 common foods Ollie eats.
# Values are (protein_per_100g, kcal_per_100g) from USDA Standard Reference.
# Used when the USDA API returns no results (e.g. rate limit or vague query).
_FALLBACK: dict[str, tuple[float, float]] = {
    "greek yoghurt":    (10.0,  59.0),
    "greek yogurt":     (10.0,  59.0),
    "oats":             (17.0, 389.0),
    "porridge":         (13.0, 375.0),
    "dates":            ( 2.5, 282.0),
    "chicken breast":   (31.0, 165.0),
    "chicken":          (27.0, 165.0),
    "salmon":           (25.0, 208.0),
    "eggs":             (13.0, 143.0),
    "egg":              (13.0, 143.0),
    "tofu":             (12.0, 100.0),
    "rice":             ( 2.7, 130.0),
    "sweet potato":     ( 1.6,  86.0),
    "black beans":      ( 8.9, 132.0),
    "lentils":          ( 9.0, 116.0),
    "lentil":           ( 9.0, 116.0),
    "avocado":          ( 2.0, 160.0),
    "banana":           ( 1.1,  89.0),
    "apple":            ( 0.3,  52.0),
    "broccoli":         ( 2.8,  34.0),
    "spinach":          ( 2.9,  23.0),
    "cottage cheese":   (11.0,  98.0),
    "milk":             ( 3.2,  61.0),
    "cheese":           (25.0, 402.0),
    "pasta":            ( 5.0, 158.0),
    "bread":            ( 9.0, 247.0),
    "sourdough":        ( 8.0, 250.0),
    "olive oil":        ( 0.0, 884.0),
    "almonds":          (21.0, 579.0),
    "almond":           (21.0, 579.0),
    "walnuts":          (15.0, 654.0),
    "walnut":           (15.0, 654.0),
    "tuna":             (28.0, 132.0),
    "cod":              (18.0,  82.0),
    "prawns":           (20.0,  99.0),
    "prawn":            (20.0,  99.0),
    "shrimp":           (20.0,  99.0),
    "scallops":         (15.0,  88.0),
    "scallop":          (15.0,  88.0),
    "venison":          (26.0, 158.0),
    "whey protein":     (25.0, 120.0),
    "protein powder":   (25.0, 120.0),
    "peanut butter":    (25.0, 588.0),
    "edamame":          (11.0, 122.0),
    "tempeh":           (19.0, 193.0),
    "mackerel":         (19.0, 205.0),
    "quinoa":           ( 4.4, 120.0),
}


def _pre_lookup(query: str) -> tuple[float, float] | None:
    """Rule-based override that runs BEFORE the USDA API for common foods with frequent
    API mismatches. Returns (protein_per_100g, kcal_per_100g) or None.
    Rules are checked in order — more specific rules must come first."""
    q = query.lower().strip()
    if "oat" in q and "milk" not in q and "flour" not in q:
        return (13.0, 389.0)
    if "greek yog" in q:
        return (10.0, 59.0)
    if "egg" in q and "white" not in q and "noodle" not in q and "fried" not in q and "pasta" not in q and "powder" not in q:
        return (12.0, 140.0)
    if "brown rice" in q:
        return (2.6, 123.0)
    if "rice" in q and "brown" not in q and "fried" not in q and "wild" not in q:
        return (2.7, 130.0)
    if "lentil" in q:
        return (9.0, 116.0)
    return None


_MAX_PLAUSIBLE_PROTEIN_PER_100G = 100.0
_MAX_PLAUSIBLE_KCAL_PER_100G = 900.0


def _is_plausible(protein_per_100g: float, kcal_per_100g: float) -> bool:
    """Sanity bound on a USDA candidate — covers virtually all real foods
    (oils top out around 884 kcal/100g, isolated protein powders around
    90g/100g) and rules out a mismatched product being picked silently."""
    return (
        0.0 <= protein_per_100g <= _MAX_PLAUSIBLE_PROTEIN_PER_100G
        and 0.0 <= kcal_per_100g <= _MAX_PLAUSIBLE_KCAL_PER_100G
    )


def _fallback_lookup(query: str) -> tuple[float, float] | None:
    """Check the hardcoded fallback table. Returns (protein_per_100g, kcal_per_100g) or None."""
    q = query.lower().strip()
    for key, vals in _FALLBACK.items():
        if key in q or q in key:
            return vals
    return None


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

    Priority: USDA → hardcoded fallback table → zero estimate.
    Source field: 'usda' | 'reference' | 'estimated'.

    Returns:
        {description, quantity_g, protein_g, kcal, source}
    """
    pre = _pre_lookup(query)
    if pre:
        protein_per_100g, kcal_per_100g = pre
        scale = quantity_g / 100.0
        logger.info("Pre-lookup hit for '%s' — skipping USDA", query)
        return {
            "description": query,
            "quantity_g": quantity_g,
            "protein_g": round(protein_per_100g * scale, 1),
            "kcal": round(kcal_per_100g * scale, 0),
            "source": "reference",
        }

    results = await search(query)

    best = next(
        (r for r in results if _is_plausible(r["protein_per_100g"], r["kcal_per_100g"])),
        None,
    )
    if best:
        scale = quantity_g / 100.0
        return {
            "description": best["description"],
            "quantity_g": quantity_g,
            "protein_g": round(best["protein_per_100g"] * scale, 1),
            "kcal": round(best["kcal_per_100g"] * scale, 0),
            "source": "usda",
        }
    if results:
        logger.warning(
            "USDA results for '%s' all failed the plausibility bounds — falling through",
            query,
        )

    fallback = _fallback_lookup(query)
    if fallback:
        protein_per_100g, kcal_per_100g = fallback
        scale = quantity_g / 100.0
        logger.info("USDA miss for '%s' — using reference table", query)
        return {
            "description": query,
            "quantity_g": quantity_g,
            "protein_g": round(protein_per_100g * scale, 1),
            "kcal": round(kcal_per_100g * scale, 0),
            "source": "reference",
        }

    return {
        "description": query,
        "quantity_g": quantity_g,
        "protein_g": 0.0,
        "kcal": 0.0,
        "source": "estimated",
    }


def _extract_nutrient(nutrients: list[dict], nutrient_id: int) -> float:
    """Pull a single nutrient value from a USDA foodNutrients list by ID."""
    for n in nutrients:
        if n.get("nutrientId") == nutrient_id:
            return float(n.get("value", 0.0))
    return 0.0
