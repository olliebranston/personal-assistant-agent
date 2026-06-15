"""Meal rotation data (from Mealplan-CONTEXT.md) — shared by agents/meal.py and tools/meal.py."""

from __future__ import annotations

BREAKFAST_ROTATION: dict[int, str] = {
    0: "Protein smoothie — 2 scoops whey + frozen berries + 2 tbsp peanut butter + oat milk (~50g protein). 3 mins.",
    1: "4-egg omelette — mushrooms, peppers, spinach, chilli flakes (~28g protein). Add 150g Greek yoghurt on the side for +15g.",
    2: "Protein smoothie — same as Monday (~50g protein). Pre-portion bags help.",
    3: "Protein overnight oats — 80g oats + 1 scoop protein + 150g Greek yoghurt + frozen berries. Prep tonight. (~42g protein)",
    4: "4 scrambled eggs on wholegrain sourdough + hot sauce (~30g protein). Greek yoghurt side if needed.",
    5: "Weekend — rotate: eggs Benedict with smoked salmon (~35–40g) / protein pancakes + Greek yoghurt (~35g) / full scramble + avocado (~30g)",
    6: "Weekend — rotate: shakshuka 4 eggs + feta + tofu (~30–38g) / eggs Benedict (~35–40g) / full scramble (~30g)",
}

LUNCH_ROTATIONS: list[str] = [
    "Rotation A — Red Lentil Dal: red lentils, tinned tomatoes, coconut milk, spinach. ~20–25g protein. Boost with Greek yoghurt or baked tofu to hit 35–47g. 30 mins batch.",
    "Rotation B — Lentil & Baked Tofu Salad: puy lentils, roasted peppers, cucumber, cherry tomatoes, baked tofu, tahini-lemon dressing. ~30–35g protein. 40 mins batch.",
    "Rotation C — Tofu Egg Fried Rice: brown rice, 2 eggs/portion, firm tofu, edamame, soy-ginger sauce. ~28–33g protein. 25 mins batch.",
    "Rotation D — Black Bean & Sweet Potato Stew: black beans, kidney beans, sweet potato, tinned tomatoes, chipotle. ~22–26g protein. Boost with tempeh or Greek yoghurt to 32–46g. 35 mins batch.",
    "Rotation E — Quinoa Power Bowl: quinoa, roasted veg, chickpeas/lentils, wilted spinach, tahini-miso dressing. ~25–32g protein. Add tofu or tempeh to push 40g+. 35 mins batch.",
]

# Pescatarian-first. Meat max once a week. Fish, eggs, seafood are primary proteins.
WEEKDAY_DINNERS: list[str] = [
    "Miso-glazed salmon + roasted sweet potato — 40–45g protein. Brush fillet with miso-mirin, roast at 200°C 15 mins. Simple.",
    "Tofu stir fry (soy-ginger-sesame) + rice/noodles — 35–45g protein. Full 400g block firm tofu + edamame.",
    "Prawn pad thai — tamarind + fish sauce + lime + peanuts — 35–40g protein. 200g prawns.",
    "Tofu ramen (miso-mushroom broth) — 35–40g protein. Dried shiitake + miso + soy + soft-boiled egg.",
    "Cod with black bean sauce + bok choi — 35–40g protein. 200g fillet, steamed or pan-fried.",
    "Korean tofu (sundubu-style) — gochujang + silken tofu + egg + spring onion — 30–38g protein.",
    "Chickpea & spinach curry + Greek yoghurt raita — 25–30g protein. Add tempeh to push to 40g.",
]

WEEKEND_DINNERS: list[str] = [
    "Wild salmon or trout + roasted veg — 40–50g protein. Generous fillet (~200g). Miso glaze or lemon-herb.",
    "Mackerel (fresh or tinned) + grains — 35–45g protein. Underrated, cheap, high omega-3.",
    "Tofu katsu curry — panko-crusted tofu, Japanese curry sauce, rice — 35–45g protein.",
    "Dal makhani — slow-cooked black lentils + kidney beans. Worth the overnight soak — 28–35g protein.",
    "Tempeh rendang — coconut + lemongrass + galangal + chilli — 38–45g protein.",
    "Shakshuka (dinner) — 4 eggs + feta + sourdough — 30–38g protein.",
    "Scallops + pea purée + crispy pancetta — 30–35g protein. Weekend treat.",
]
