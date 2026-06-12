"""Recipe database for Robin.

Each recipe: slug (dict key), name, serves, time_mins, protein_g, kcal (per serving),
ingredients [{item, qty, unit}], method [str].

Batch cook recipes serve 4 — quantities are for the full batch.
All others serve 1 unless noted.
"""

from __future__ import annotations

RECIPES: dict[str, dict] = {

    # ── WEEKDAY DINNERS ───────────────────────────────────────────────────────

    "miso_glazed_salmon": {
        "name": "Miso-Glazed Salmon + Roasted Sweet Potato",
        "category": "weekday_dinner",
        "serves": 1,
        "time_mins": 25,
        "protein_g": 42,
        "kcal": 520,
        "ingredients": [
            {"item": "salmon fillet", "qty": 200, "unit": "g"},
            {"item": "white miso paste", "qty": 1, "unit": "tbsp"},
            {"item": "mirin", "qty": 1, "unit": "tsp"},
            {"item": "soy sauce", "qty": 1, "unit": "tsp"},
            {"item": "sesame oil", "qty": 0.5, "unit": "tsp"},
            {"item": "sweet potato", "qty": 250, "unit": "g"},
            {"item": "olive oil", "qty": 1, "unit": "tbsp"},
            {"item": "spring onions", "qty": 2, "unit": ""},
        ],
        "method": [
            "Preheat oven to 200°C.",
            "Dice sweet potato into 2cm cubes, toss with olive oil and a pinch of salt. Roast 20 mins.",
            "Mix miso, mirin, soy, sesame oil into a glaze.",
            "Brush salmon generously with glaze. Let it sit 5 mins while potato roasts.",
            "Add salmon to tray for the last 12–15 mins. It's done when it flakes easily.",
            "Scatter spring onions over everything and serve.",
        ],
    },

    "tofu_stir_fry": {
        "name": "Tofu Stir Fry (Soy-Ginger-Sesame)",
        "category": "weekday_dinner",
        "serves": 1,
        "time_mins": 20,
        "protein_g": 40,
        "kcal": 580,
        "ingredients": [
            {"item": "extra-firm tofu", "qty": 400, "unit": "g"},
            {"item": "edamame (frozen, shelled)", "qty": 100, "unit": "g"},
            {"item": "soy sauce", "qty": 3, "unit": "tbsp"},
            {"item": "sesame oil", "qty": 1, "unit": "tbsp"},
            {"item": "fresh ginger", "qty": 2, "unit": "cm piece"},
            {"item": "garlic", "qty": 2, "unit": "cloves"},
            {"item": "chilli flakes", "qty": 0.5, "unit": "tsp"},
            {"item": "rice or noodles (cooked)", "qty": 200, "unit": "g"},
            {"item": "spring onions", "qty": 3, "unit": ""},
        ],
        "method": [
            "Press tofu between paper towels for a few mins, then cube it.",
            "Fry tofu in a hot pan with a splash of oil until golden on all sides — about 8 mins. Set aside.",
            "Fry garlic and grated ginger 30 seconds, add edamame.",
            "Return tofu to pan, add soy and sesame oil, toss everything.",
            "Serve over rice or noodles. Scatter spring onions and chilli.",
        ],
    },

    "prawn_pad_thai": {
        "name": "Prawn Pad Thai",
        "category": "weekday_dinner",
        "serves": 1,
        "time_mins": 20,
        "protein_g": 38,
        "kcal": 560,
        "ingredients": [
            {"item": "raw king prawns", "qty": 200, "unit": "g"},
            {"item": "flat rice noodles", "qty": 80, "unit": "g (dry)"},
            {"item": "tamarind paste", "qty": 2, "unit": "tbsp"},
            {"item": "fish sauce", "qty": 1, "unit": "tbsp"},
            {"item": "lime juice", "qty": 1, "unit": "lime"},
            {"item": "eggs", "qty": 2, "unit": ""},
            {"item": "bean sprouts", "qty": 80, "unit": "g"},
            {"item": "spring onions", "qty": 3, "unit": ""},
            {"item": "peanuts (crushed)", "qty": 30, "unit": "g"},
            {"item": "garlic", "qty": 2, "unit": "cloves"},
        ],
        "method": [
            "Soak noodles in boiling water 5 mins, drain.",
            "Mix tamarind, fish sauce, lime juice — taste and adjust.",
            "Fry prawns in a hot wok 2 mins each side. Set aside.",
            "Scramble eggs in same wok, push to side, add noodles.",
            "Add sauce, prawns, bean sprouts, spring onions — toss hard on high heat.",
            "Plate with crushed peanuts and a lime wedge.",
        ],
    },

    "tofu_miso_ramen": {
        "name": "Tofu Ramen (Miso-Mushroom Broth)",
        "category": "weekday_dinner",
        "serves": 1,
        "time_mins": 25,
        "protein_g": 37,
        "kcal": 510,
        "ingredients": [
            {"item": "firm tofu", "qty": 200, "unit": "g"},
            {"item": "dried shiitake mushrooms", "qty": 15, "unit": "g"},
            {"item": "white miso paste", "qty": 2, "unit": "tbsp"},
            {"item": "soy sauce", "qty": 1, "unit": "tbsp"},
            {"item": "garlic", "qty": 2, "unit": "cloves"},
            {"item": "fresh ginger", "qty": 2, "unit": "cm piece"},
            {"item": "ramen or egg noodles", "qty": 80, "unit": "g (dry)"},
            {"item": "egg", "qty": 1, "unit": ""},
            {"item": "spring onions", "qty": 3, "unit": ""},
            {"item": "sesame oil", "qty": 1, "unit": "tsp"},
            {"item": "chilli oil", "qty": 1, "unit": "tsp"},
        ],
        "method": [
            "Soak shiitakes in 500ml boiling water 15 mins. Reserve the liquid — that's your broth.",
            "Bring broth to a simmer, add garlic, ginger, miso, soy. Don't boil after miso goes in.",
            "Soft-boil the egg: 7 mins in boiling water, ice bath, peel.",
            "Pan-fry cubed tofu until golden. Cook noodles separately.",
            "Assemble: noodles in a bowl, pour over broth, add tofu, halved egg, spring onions.",
            "Finish with sesame oil and chilli oil.",
        ],
    },

    "cod_black_bean": {
        "name": "Cod with Black Bean Sauce + Bok Choi",
        "category": "weekday_dinner",
        "serves": 1,
        "time_mins": 20,
        "protein_g": 38,
        "kcal": 420,
        "ingredients": [
            {"item": "cod fillet", "qty": 200, "unit": "g"},
            {"item": "bok choi", "qty": 2, "unit": "heads"},
            {"item": "black bean sauce (jarred)", "qty": 2, "unit": "tbsp"},
            {"item": "garlic", "qty": 2, "unit": "cloves"},
            {"item": "fresh ginger", "qty": 1, "unit": "cm piece"},
            {"item": "soy sauce", "qty": 1, "unit": "tbsp"},
            {"item": "sesame oil", "qty": 1, "unit": "tsp"},
            {"item": "rice (cooked)", "qty": 150, "unit": "g"},
        ],
        "method": [
            "Pat cod dry, season with a little salt and white pepper.",
            "Heat a pan with a splash of oil on high. Sear cod 3 mins each side until opaque. Set aside.",
            "Fry garlic and ginger 30 seconds, add black bean sauce, soy, splash of water.",
            "Halve bok choi, add to pan, toss in sauce 2 mins.",
            "Plate rice, bok choi and cod. Spoon sauce over. Finish with sesame oil.",
        ],
    },

    "korean_tofu": {
        "name": "Korean Silken Tofu Stew (Sundubu-Style)",
        "category": "weekday_dinner",
        "serves": 1,
        "time_mins": 20,
        "protein_g": 33,
        "kcal": 380,
        "ingredients": [
            {"item": "silken tofu", "qty": 300, "unit": "g"},
            {"item": "gochujang", "qty": 1, "unit": "tbsp"},
            {"item": "gochugaru (Korean chilli flakes)", "qty": 1, "unit": "tsp"},
            {"item": "garlic", "qty": 3, "unit": "cloves"},
            {"item": "sesame oil", "qty": 1, "unit": "tbsp"},
            {"item": "soy sauce", "qty": 1, "unit": "tbsp"},
            {"item": "vegetable stock", "qty": 300, "unit": "ml"},
            {"item": "egg", "qty": 1, "unit": ""},
            {"item": "spring onions", "qty": 3, "unit": ""},
            {"item": "rice (cooked)", "qty": 150, "unit": "g"},
        ],
        "method": [
            "Fry garlic and gochugaru in sesame oil 1 min.",
            "Add gochujang, stock, soy sauce. Bring to a simmer.",
            "Add silken tofu in large spoonfuls directly into the broth — don't stir, it'll break.",
            "Crack egg directly into the stew, let it poach 3–4 mins.",
            "Top with spring onions. Serve straight from the pot with rice.",
        ],
    },

    "chickpea_spinach_curry": {
        "name": "Chickpea & Spinach Curry",
        "category": "weekday_dinner",
        "serves": 1,
        "time_mins": 25,
        "protein_g": 28,
        "kcal": 480,
        "ingredients": [
            {"item": "tinned chickpeas", "qty": 400, "unit": "g (1 tin)"},
            {"item": "fresh spinach", "qty": 100, "unit": "g"},
            {"item": "tinned tomatoes", "qty": 200, "unit": "g"},
            {"item": "onion", "qty": 1, "unit": ""},
            {"item": "garlic", "qty": 3, "unit": "cloves"},
            {"item": "fresh ginger", "qty": 2, "unit": "cm piece"},
            {"item": "cumin", "qty": 1, "unit": "tsp"},
            {"item": "garam masala", "qty": 1, "unit": "tsp"},
            {"item": "turmeric", "qty": 0.5, "unit": "tsp"},
            {"item": "chilli flakes", "qty": 0.5, "unit": "tsp"},
            {"item": "Greek yoghurt", "qty": 100, "unit": "g"},
            {"item": "rice or naan", "qty": 1, "unit": "serving"},
        ],
        "method": [
            "Fry onion until soft, add garlic, ginger, and spices. Cook 2 mins.",
            "Add tomatoes and chickpeas. Simmer 15 mins.",
            "Stir in spinach until wilted. Season hard — needs salt.",
            "Serve with a dollop of Greek yoghurt and rice or naan. The yoghurt adds ~10g protein.",
            "Add tempeh on the side to push protein toward 45g.",
        ],
    },

    # ── WEEKEND DINNERS ───────────────────────────────────────────────────────

    "salmon_roasted_veg": {
        "name": "Wild Salmon + Roasted Veg",
        "category": "weekend_dinner",
        "serves": 1,
        "time_mins": 35,
        "protein_g": 45,
        "kcal": 580,
        "ingredients": [
            {"item": "wild salmon fillet", "qty": 220, "unit": "g"},
            {"item": "courgette", "qty": 1, "unit": ""},
            {"item": "red pepper", "qty": 1, "unit": ""},
            {"item": "cherry tomatoes", "qty": 150, "unit": "g"},
            {"item": "red onion", "qty": 0.5, "unit": ""},
            {"item": "olive oil", "qty": 2, "unit": "tbsp"},
            {"item": "lemon", "qty": 1, "unit": ""},
            {"item": "fresh dill or parsley", "qty": 1, "unit": "small bunch"},
            {"item": "garlic", "qty": 2, "unit": "cloves"},
        ],
        "method": [
            "Preheat oven to 200°C. Chop veg into chunks, toss with olive oil, garlic, salt. Roast 20 mins.",
            "Place salmon on top of veg for the last 15 mins. Squeeze lemon over everything.",
            "Salmon is done when it flakes. Rest 2 mins.",
            "Scatter fresh herbs and serve.",
        ],
    },

    "tofu_katsu_curry": {
        "name": "Tofu Katsu Curry",
        "category": "weekend_dinner",
        "serves": 1,
        "time_mins": 40,
        "protein_g": 40,
        "kcal": 680,
        "ingredients": [
            {"item": "extra-firm tofu", "qty": 300, "unit": "g"},
            {"item": "panko breadcrumbs", "qty": 60, "unit": "g"},
            {"item": "egg", "qty": 1, "unit": ""},
            {"item": "plain flour", "qty": 3, "unit": "tbsp"},
            {"item": "Japanese curry roux (S&B Golden Curry)", "qty": 2, "unit": "cubes"},
            {"item": "onion", "qty": 1, "unit": ""},
            {"item": "carrot", "qty": 1, "unit": ""},
            {"item": "garlic", "qty": 2, "unit": "cloves"},
            {"item": "vegetable stock", "qty": 400, "unit": "ml"},
            {"item": "jasmine rice", "qty": 80, "unit": "g (dry)"},
        ],
        "method": [
            "Slice tofu into 1.5cm slabs, press dry. Coat in flour → beaten egg → panko.",
            "Fry in 1cm oil until deep golden on both sides, about 3 mins per side. Drain.",
            "Sauce: fry onion and carrot until soft. Add garlic, stock. Simmer 10 mins.",
            "Add curry roux, stir until melted and sauce thickens. 5 mins.",
            "Cook rice. Slice tofu, pour sauce alongside (not over — keeps it crispy).",
        ],
    },

    "dal_makhani": {
        "name": "Dal Makhani",
        "category": "weekend_dinner",
        "serves": 2,
        "time_mins": 60,
        "protein_g": 32,
        "kcal": 520,
        "ingredients": [
            {"item": "black lentils (urad dal)", "qty": 150, "unit": "g (dry, soaked overnight)"},
            {"item": "tinned kidney beans", "qty": 200, "unit": "g"},
            {"item": "tinned tomatoes", "qty": 400, "unit": "g"},
            {"item": "butter", "qty": 30, "unit": "g"},
            {"item": "double cream", "qty": 50, "unit": "ml"},
            {"item": "onion", "qty": 1, "unit": ""},
            {"item": "garlic", "qty": 4, "unit": "cloves"},
            {"item": "fresh ginger", "qty": 3, "unit": "cm piece"},
            {"item": "cumin seeds", "qty": 1, "unit": "tsp"},
            {"item": "garam masala", "qty": 1.5, "unit": "tsp"},
            {"item": "chilli powder", "qty": 0.5, "unit": "tsp"},
            {"item": "naan or rice", "qty": 1, "unit": "serving per person"},
        ],
        "method": [
            "Soak urad dal overnight. Pressure cook or simmer for 45 mins until completely soft.",
            "Fry cumin seeds in butter until they pop. Add onion, cook until deep golden.",
            "Add garlic, ginger, tomatoes, spices. Cook down 10 mins.",
            "Add cooked lentils and kidney beans. Simmer 20 mins — the longer the better.",
            "Stir in cream. Season. Serve with naan. Worth making double and reheating.",
        ],
    },

    "tempeh_rendang": {
        "name": "Tempeh Rendang",
        "category": "weekend_dinner",
        "serves": 2,
        "time_mins": 50,
        "protein_g": 42,
        "kcal": 620,
        "ingredients": [
            {"item": "tempeh", "qty": 400, "unit": "g"},
            {"item": "coconut milk (full fat)", "qty": 400, "unit": "ml"},
            {"item": "lemongrass stalks", "qty": 2, "unit": ""},
            {"item": "shallots", "qty": 4, "unit": ""},
            {"item": "garlic", "qty": 4, "unit": "cloves"},
            {"item": "fresh ginger", "qty": 3, "unit": "cm piece"},
            {"item": "galangal (or extra ginger)", "qty": 2, "unit": "cm piece"},
            {"item": "dried red chillies", "qty": 3, "unit": ""},
            {"item": "turmeric", "qty": 1, "unit": "tsp"},
            {"item": "lime leaves (kaffir)", "qty": 4, "unit": ""},
            {"item": "palm or brown sugar", "qty": 1, "unit": "tbsp"},
            {"item": "rice", "qty": 1, "unit": "serving per person"},
        ],
        "method": [
            "Blend shallots, garlic, ginger, galangal, chillies into a paste.",
            "Cut tempeh into large chunks. Pan-fry until golden. Set aside.",
            "Fry paste with lemongrass and lime leaves until fragrant, about 5 mins.",
            "Add coconut milk and tempeh. Simmer uncovered 30 mins — sauce will thicken and darken.",
            "Add sugar, season. Keep going until almost dry and caramelised — that's rendang.",
        ],
    },

    "shakshuka_dinner": {
        "name": "Shakshuka (Dinner Version)",
        "category": "weekend_dinner",
        "serves": 1,
        "time_mins": 25,
        "protein_g": 35,
        "kcal": 480,
        "ingredients": [
            {"item": "eggs", "qty": 4, "unit": ""},
            {"item": "tinned tomatoes", "qty": 400, "unit": "g"},
            {"item": "red pepper", "qty": 1, "unit": ""},
            {"item": "onion", "qty": 0.5, "unit": ""},
            {"item": "garlic", "qty": 3, "unit": "cloves"},
            {"item": "cumin", "qty": 1, "unit": "tsp"},
            {"item": "smoked paprika", "qty": 1, "unit": "tsp"},
            {"item": "chilli flakes", "qty": 0.5, "unit": "tsp"},
            {"item": "feta", "qty": 60, "unit": "g"},
            {"item": "fresh parsley or coriander", "qty": 1, "unit": "handful"},
            {"item": "sourdough bread", "qty": 2, "unit": "thick slices"},
        ],
        "method": [
            "Fry onion and pepper until soft. Add garlic and spices, 2 mins.",
            "Add tomatoes, simmer 10 mins. Season aggressively.",
            "Make 4 wells in the sauce. Crack eggs in. Cover and cook 6–8 mins (runny yolks).",
            "Crumble feta over, scatter herbs. Serve straight from the pan with sourdough.",
        ],
    },

    "scallops_pea_puree": {
        "name": "Scallops + Pea Purée",
        "category": "weekend_dinner",
        "serves": 1,
        "time_mins": 20,
        "protein_g": 32,
        "kcal": 420,
        "ingredients": [
            {"item": "king scallops", "qty": 6, "unit": ""},
            {"item": "frozen peas", "qty": 200, "unit": "g"},
            {"item": "butter", "qty": 30, "unit": "g"},
            {"item": "garlic", "qty": 1, "unit": "clove"},
            {"item": "fresh mint", "qty": 1, "unit": "small handful"},
            {"item": "lemon", "qty": 0.5, "unit": ""},
            {"item": "pancetta or bacon lardons", "qty": 50, "unit": "g"},
        ],
        "method": [
            "Cook peas, blend with half the butter, mint, garlic, lemon juice. Season well. Keep warm.",
            "Fry pancetta until crispy. Set aside.",
            "Pat scallops bone dry — critical. Season with salt only.",
            "Get a pan smoking hot with a tiny drop of oil. Sear scallops 90 seconds each side — no touching.",
            "Plate: pea purée base, scallops on top, pancetta scattered. Squeeze lemon.",
        ],
    },

    "mackerel_grains": {
        "name": "Mackerel + Grains",
        "category": "weekend_dinner",
        "serves": 1,
        "time_mins": 20,
        "protein_g": 40,
        "kcal": 560,
        "ingredients": [
            {"item": "fresh mackerel fillets (or tinned)", "qty": 200, "unit": "g"},
            {"item": "cooked quinoa or farro", "qty": 150, "unit": "g"},
            {"item": "cucumber", "qty": 0.5, "unit": ""},
            {"item": "cherry tomatoes", "qty": 100, "unit": "g"},
            {"item": "red onion", "qty": 0.25, "unit": ""},
            {"item": "lemon", "qty": 1, "unit": ""},
            {"item": "olive oil", "qty": 2, "unit": "tbsp"},
            {"item": "capers", "qty": 1, "unit": "tbsp"},
            {"item": "fresh parsley", "qty": 1, "unit": "handful"},
            {"item": "Dijon mustard", "qty": 1, "unit": "tsp"},
        ],
        "method": [
            "If using fresh mackerel: season and grill or pan-fry skin-side down 4 mins, flip 2 mins.",
            "Make dressing: olive oil, lemon juice, mustard, capers.",
            "Toss grains, chopped veg and dressing. Plate, flake mackerel over the top.",
            "Scatter parsley. Eat at room temp — this works as lunch the next day too.",
        ],
    },

    # ── BATCH COOK LUNCHES (4 portions) ──────────────────────────────────────

    "red_lentil_dal": {
        "name": "Red Lentil Dal",
        "category": "batch_lunch",
        "serves": 4,
        "time_mins": 30,
        "protein_g": 38,  # with yoghurt/tofu boost per portion
        "kcal": 480,
        "ingredients": [
            {"item": "red lentils", "qty": 250, "unit": "g (dry)"},
            {"item": "tinned tomatoes", "qty": 400, "unit": "g"},
            {"item": "coconut milk", "qty": 400, "unit": "ml"},
            {"item": "fresh spinach", "qty": 200, "unit": "g"},
            {"item": "onion", "qty": 1, "unit": "large"},
            {"item": "garlic", "qty": 4, "unit": "cloves"},
            {"item": "fresh ginger", "qty": 3, "unit": "cm piece"},
            {"item": "cumin", "qty": 2, "unit": "tsp"},
            {"item": "turmeric", "qty": 1, "unit": "tsp"},
            {"item": "garam masala", "qty": 2, "unit": "tsp"},
            {"item": "chilli flakes", "qty": 1, "unit": "tsp"},
            {"item": "Greek yoghurt (to serve)", "qty": 400, "unit": "g (100g per portion)"},
        ],
        "method": [
            "Fry onion until soft (5 mins). Add garlic, ginger, spices — cook 2 mins.",
            "Rinse lentils. Add with tomatoes, coconut milk, and 300ml water.",
            "Simmer 20 mins, stirring occasionally, until lentils have collapsed.",
            "Stir in spinach until wilted. Season hard — needs salt.",
            "Portion into 4 containers. Add 100g Greek yoghurt per portion when serving (+10g protein).",
            "Optional: add 150g baked tofu per portion for another +15–22g protein.",
        ],
    },

    "lentil_tofu_salad": {
        "name": "Lentil & Baked Tofu Salad",
        "category": "batch_lunch",
        "serves": 4,
        "time_mins": 40,
        "protein_g": 35,
        "kcal": 460,
        "ingredients": [
            {"item": "puy lentils", "qty": 250, "unit": "g (dry)"},
            {"item": "extra-firm tofu", "qty": 400, "unit": "g"},
            {"item": "roasted red peppers (jar)", "qty": 200, "unit": "g"},
            {"item": "cucumber", "qty": 1, "unit": ""},
            {"item": "cherry tomatoes", "qty": 200, "unit": "g"},
            {"item": "fresh parsley", "qty": 1, "unit": "large bunch"},
            {"item": "tahini", "qty": 4, "unit": "tbsp"},
            {"item": "lemon juice", "qty": 2, "unit": "lemons"},
            {"item": "soy sauce", "qty": 2, "unit": "tbsp"},
            {"item": "sesame oil", "qty": 1, "unit": "tbsp"},
            {"item": "hemp seeds (optional)", "qty": 4, "unit": "tbsp"},
        ],
        "method": [
            "Cook puy lentils in salted water 20 mins until tender but not mushy. Drain, cool.",
            "Cube tofu, toss with soy and sesame oil. Bake at 200°C for 20 mins until crispy.",
            "Whisk tahini, lemon juice, 3 tbsp water until smooth dressing.",
            "Chop all veg. Mix with lentils, tofu, parsley, dressing.",
            "Portion into 4 containers. Scatter hemp seeds to serve (+6g protein per tbsp).",
        ],
    },

    "tofu_egg_fried_rice": {
        "name": "Tofu Egg Fried Rice",
        "category": "batch_lunch",
        "serves": 4,
        "time_mins": 25,
        "protein_g": 30,
        "kcal": 520,
        "ingredients": [
            {"item": "brown rice", "qty": 300, "unit": "g (dry)"},
            {"item": "extra-firm tofu", "qty": 400, "unit": "g"},
            {"item": "eggs", "qty": 8, "unit": ""},
            {"item": "frozen edamame (shelled)", "qty": 200, "unit": "g"},
            {"item": "soy sauce", "qty": 4, "unit": "tbsp"},
            {"item": "sesame oil", "qty": 2, "unit": "tbsp"},
            {"item": "garlic", "qty": 4, "unit": "cloves"},
            {"item": "fresh ginger", "qty": 3, "unit": "cm piece"},
            {"item": "spring onions", "qty": 6, "unit": ""},
            {"item": "chilli flakes", "qty": 1, "unit": "tsp"},
        ],
        "method": [
            "Cook rice, spread on a tray to cool completely — stops clumping. Can do this the night before.",
            "Crumble tofu into a pan, fry in oil until golden and dry, about 10 mins. Set aside.",
            "Scramble eggs in the same pan, remove before fully set.",
            "Add cold rice to pan on high heat. Fry until separated. Add garlic and ginger.",
            "Add edamame, tofu, eggs back in. Season with soy and sesame oil.",
            "Scatter spring onions. Portion into 4 containers.",
        ],
    },

    "black_bean_sweet_potato_stew": {
        "name": "Black Bean & Sweet Potato Stew",
        "category": "batch_lunch",
        "serves": 4,
        "time_mins": 35,
        "protein_g": 30,  # with Greek yoghurt boost
        "kcal": 440,
        "ingredients": [
            {"item": "tinned black beans", "qty": 800, "unit": "g (2 tins)"},
            {"item": "tinned kidney beans", "qty": 400, "unit": "g (1 tin)"},
            {"item": "sweet potatoes", "qty": 600, "unit": "g"},
            {"item": "tinned tomatoes", "qty": 400, "unit": "g"},
            {"item": "onion", "qty": 1, "unit": "large"},
            {"item": "garlic", "qty": 4, "unit": "cloves"},
            {"item": "smoked paprika", "qty": 2, "unit": "tsp"},
            {"item": "cumin", "qty": 2, "unit": "tsp"},
            {"item": "chipotle paste", "qty": 1, "unit": "tbsp"},
            {"item": "limes", "qty": 2, "unit": ""},
            {"item": "fresh coriander", "qty": 1, "unit": "bunch"},
            {"item": "Greek yoghurt (to serve)", "qty": 400, "unit": "g"},
        ],
        "method": [
            "Cube sweet potato, roast at 200°C for 20 mins.",
            "Fry onion until soft. Add garlic, spices, chipotle — 2 mins.",
            "Add beans, tomatoes, 200ml water. Simmer 15 mins.",
            "Add roasted sweet potato. Squeeze in lime juice. Season.",
            "Portion into 4 containers. Serve with Greek yoghurt and hot sauce.",
        ],
    },

    "quinoa_power_bowl": {
        "name": "Quinoa Power Bowl",
        "category": "batch_lunch",
        "serves": 4,
        "time_mins": 35,
        "protein_g": 32,  # with tempeh or tofu on top
        "kcal": 490,
        "ingredients": [
            {"item": "quinoa", "qty": 300, "unit": "g (dry)"},
            {"item": "tinned chickpeas", "qty": 800, "unit": "g (2 tins)"},
            {"item": "mixed veg for roasting (peppers, courgette, red onion)", "qty": 500, "unit": "g"},
            {"item": "fresh spinach", "qty": 150, "unit": "g"},
            {"item": "garlic", "qty": 3, "unit": "cloves"},
            {"item": "tahini", "qty": 4, "unit": "tbsp"},
            {"item": "white miso paste", "qty": 2, "unit": "tbsp"},
            {"item": "rice vinegar", "qty": 2, "unit": "tbsp"},
            {"item": "sesame oil", "qty": 1, "unit": "tbsp"},
            {"item": "chilli flakes", "qty": 1, "unit": "tsp"},
            {"item": "extra-firm tofu or tempeh (to serve)", "qty": 400, "unit": "g"},
        ],
        "method": [
            "Cook quinoa in salted water 15 mins, fluff with fork, cool.",
            "Toss chickpeas and veg in olive oil, roast at 200°C for 25 mins.",
            "Wilt spinach in a pan with garlic.",
            "Whisk dressing: tahini, miso, rice vinegar, sesame oil, 4 tbsp water.",
            "Assemble bowls: quinoa base, veg, chickpeas, spinach, dressing.",
            "Top with baked tofu or sliced tempeh — push protein to 40g+.",
        ],
    },

    # ── WEEKEND BREAKFASTS ────────────────────────────────────────────────────

    "eggs_benedict_salmon": {
        "name": "Eggs Benedict with Smoked Salmon",
        "category": "weekend_breakfast",
        "serves": 1,
        "time_mins": 20,
        "protein_g": 38,
        "kcal": 520,
        "ingredients": [
            {"item": "smoked salmon", "qty": 80, "unit": "g"},
            {"item": "eggs", "qty": 2, "unit": ""},
            {"item": "English muffins", "qty": 2, "unit": "halves"},
            {"item": "butter", "qty": 20, "unit": "g"},
            {"item": "fresh spinach", "qty": 50, "unit": "g"},
            {"item": "white wine vinegar", "qty": 1, "unit": "tbsp"},
            {"item": "egg yolks (hollandaise)", "qty": 2, "unit": ""},
            {"item": "butter (hollandaise)", "qty": 80, "unit": "g"},
            {"item": "lemon juice", "qty": 0.5, "unit": "lemon"},
        ],
        "method": [
            "Quick hollandaise: whisk egg yolks + lemon in a bowl over simmering water. Slowly drizzle in melted butter, whisking constantly, until thick. Season.",
            "Poach eggs: bring water to simmer, add vinegar. Swirl water, crack egg in, 3 mins.",
            "Toast muffins. Wilt spinach quickly in a pan.",
            "Stack: muffin → spinach → salmon → poached egg → hollandaise.",
        ],
    },

    "protein_pancakes": {
        "name": "Protein Pancakes",
        "category": "weekend_breakfast",
        "serves": 1,
        "time_mins": 15,
        "protein_g": 35,
        "kcal": 480,
        "ingredients": [
            {"item": "oats (blended to flour)", "qty": 60, "unit": "g"},
            {"item": "whey protein powder (vanilla)", "qty": 1, "unit": "scoop (33g)"},
            {"item": "eggs", "qty": 2, "unit": ""},
            {"item": "banana", "qty": 1, "unit": ""},
            {"item": "baking powder", "qty": 0.5, "unit": "tsp"},
            {"item": "oat milk", "qty": 80, "unit": "ml"},
            {"item": "Greek yoghurt (to serve)", "qty": 150, "unit": "g"},
            {"item": "mixed berries", "qty": 100, "unit": "g"},
            {"item": "honey", "qty": 1, "unit": "tsp"},
        ],
        "method": [
            "Blend oats to flour. Mix with protein powder, baking powder, eggs, mashed banana, oat milk.",
            "Batter should be thick — add more milk if needed.",
            "Cook on a medium-low non-stick pan, 2 mins per side.",
            "Stack with Greek yoghurt, berries, drizzle of honey.",
        ],
    },

    "full_scramble_avocado": {
        "name": "Full Scramble + Avocado",
        "category": "weekend_breakfast",
        "serves": 1,
        "time_mins": 10,
        "protein_g": 30,
        "kcal": 540,
        "ingredients": [
            {"item": "eggs", "qty": 4, "unit": ""},
            {"item": "butter", "qty": 20, "unit": "g"},
            {"item": "wholegrain sourdough", "qty": 2, "unit": "slices"},
            {"item": "avocado", "qty": 0.5, "unit": ""},
            {"item": "hot sauce", "qty": 1, "unit": "splash"},
            {"item": "fresh chives or parsley", "qty": 1, "unit": "small handful"},
            {"item": "salt and pepper", "qty": 1, "unit": "to taste"},
        ],
        "method": [
            "Beat eggs with a pinch of salt.",
            "Low and slow: melt butter on low heat. Add eggs. Stir constantly with a spatula — pull from edges.",
            "Remove from heat while still slightly wet — residual heat finishes them.",
            "Toast sourdough, mash avocado with salt and lemon. Top with eggs, hot sauce, herbs.",
        ],
    },

    "shakshuka_breakfast": {
        "name": "Shakshuka (Breakfast Version)",
        "category": "weekend_breakfast",
        "serves": 1,
        "time_mins": 20,
        "protein_g": 28,
        "kcal": 380,
        "ingredients": [
            {"item": "eggs", "qty": 3, "unit": ""},
            {"item": "tinned tomatoes", "qty": 300, "unit": "g"},
            {"item": "red pepper", "qty": 0.5, "unit": ""},
            {"item": "garlic", "qty": 2, "unit": "cloves"},
            {"item": "cumin", "qty": 0.5, "unit": "tsp"},
            {"item": "smoked paprika", "qty": 0.5, "unit": "tsp"},
            {"item": "chilli flakes", "qty": 0.25, "unit": "tsp"},
            {"item": "feta", "qty": 40, "unit": "g"},
            {"item": "sourdough", "qty": 1, "unit": "slice"},
        ],
        "method": [
            "Fry garlic and pepper 3 mins. Add tomatoes and spices, simmer 8 mins.",
            "Make wells, crack eggs in, cover and cook 5–6 mins for runny yolks.",
            "Crumble feta over. Toast sourdough to dip.",
        ],
    },
}


def format_recipe(slug: str) -> str:
    """Return a formatted recipe card as a Telegram-ready string."""
    recipe = RECIPES.get(slug)
    if not recipe:
        return ""

    serves_note = f" — serves {recipe['serves']}" if recipe["serves"] > 1 else ""
    lines = [
        f"*{recipe['name'].upper()}*",
        f"_{recipe['time_mins']} mins · {recipe['protein_g']}g protein · {recipe['kcal']} kcal{serves_note}_",
        "",
        "*INGREDIENTS*",
    ]
    for ing in recipe["ingredients"]:
        qty = f"{ing['qty']:g}" if isinstance(ing["qty"], float) and ing["qty"] == int(ing["qty"]) else str(ing["qty"])
        unit = f"{ing['unit']} " if ing["unit"] else ""
        lines.append(f"• {qty} {unit}{ing['item']}")

    lines.append("")
    lines.append("*METHOD*")
    for i, step in enumerate(recipe["method"], 1):
        lines.append(f"{i}. {step}")

    return "\n".join(lines)


def find_recipe(query: str) -> tuple[str, dict] | None:
    """Find the closest recipe match for a free-text query. Returns (slug, recipe) or None."""
    query_lower = query.lower()

    # Exact slug match first
    if query_lower.replace(" ", "_") in RECIPES:
        slug = query_lower.replace(" ", "_")
        return slug, RECIPES[slug]

    # Name substring match
    for slug, recipe in RECIPES.items():
        if query_lower in recipe["name"].lower():
            return slug, recipe

    # Keyword match — check if any significant word from query appears in name
    keywords = [w for w in query_lower.split() if len(w) > 3]
    for slug, recipe in RECIPES.items():
        name_lower = recipe["name"].lower()
        if any(kw in name_lower for kw in keywords):
            return slug, recipe

    return None


def get_recipes_by_category(category: str) -> list[tuple[str, dict]]:
    """Return all recipes matching a category."""
    return [(slug, r) for slug, r in RECIPES.items() if r.get("category") == category]


# Pantry staples assumed to always be in stock — excluded from shopping list generation
PANTRY_STAPLES = {
    "olive oil", "sesame oil", "soy sauce", "fish sauce", "miso paste",
    "white miso paste", "rice vinegar", "tamarind paste", "coconut milk",
    "tinned tomatoes", "tinned chickpeas", "tinned black beans", "tinned kidney beans",
    "cumin", "cumin seeds", "turmeric", "garam masala", "smoked paprika",
    "chilli flakes", "gochujang", "gochugaru", "five spice", "cardamom",
    "black pepper", "salt", "garlic", "fresh ginger", "chilli",
    "rice", "quinoa", "oats", "brown rice", "dried pasta",
    "peanut butter", "tahini", "hemp seeds",
    "sriracha", "hot sauce", "lemon juice", "lime juice",
    "baking powder", "plain flour", "vegetable stock",
    "butter", "olive oil",
}
