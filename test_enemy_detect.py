import os

import numpy as np

from enemy_detect import sample_rarity, RARITY_COLORS, _hex_to_bgr, classify_action, priority_score, aim_mouse_target, flee_mouse_target


def test_sample_rarity_matches_known_colors():
    for name, hexcode in RARITY_COLORS.items():
        # Skip "Eternal" (placeholder using Super's color; would always match Super)
        if name == "Eternal":
            continue
        bgr = _hex_to_bgr(hexcode)
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        bbox = (40, 60, 60, 80)
        # sample_rarity looks in a patch above the bbox's top-center; paint
        # that exact patch so the test doesn't depend on sample_rarity's
        # internal offsets matching some other guess.
        image[40:52, 30:70] = bgr
        assert sample_rarity(image, bbox) == name


def test_sample_rarity_eternal_placeholder_reads_as_super():
    # Eternal aliases Super's color until real calibration data exists (see
    # RARITY_COLORS' comment) — sampling Eternal's color deterministically
    # resolves to "Super" (first match in RARITY_ORDER). This is intentional
    # and harmless: classify_action() treats every rarity above Ultra
    # (Super/Eternal/Unique) identically (AVOID fallback), so this doesn't
    # change bot behavior — asserting it here so the collision is a tested,
    # documented fact rather than a silent gap.
    bgr = _hex_to_bgr(RARITY_COLORS["Eternal"])
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    bbox = (40, 60, 60, 80)
    image[40:52, 30:70] = bgr
    assert sample_rarity(image, bbox) == "Super"


def test_sample_rarity_falls_back_to_common_when_no_match():
    image = np.zeros((100, 100, 3), dtype=np.uint8)  # pure black
    bbox = (40, 60, 60, 80)
    assert sample_rarity(image, bbox) == "Common"


# Background BGR for the partial-coverage tests below: picked to NOT fall
# within `tolerance` of any RARITY_COLORS entry itself (a plain gray, for
# example, would spuriously match "Unique"'s 555555 and dominate the count).
# Verified: none of the 10 rarity colors' ±40 tolerance boxes contain this.
_BG_BGR = (0, 200, 0)


def test_sample_rarity_detects_partial_coverage_above_floor():
    # Realistic case: name tag glyph covers a minority of the sampled patch,
    # rest is background — not a flat 100% fill like the tests above.
    # bbox (40,60,60,80) -> sampled patch is image[40:52, 30:70] = 12 rows x
    # 40 cols = 480 px total (see sample_rarity's y0:y1s/x0:x1s derivation).
    image = np.full((100, 100, 3), _BG_BGR, dtype=np.uint8)
    bbox = (40, 60, 60, 80)
    bgr = _hex_to_bgr(RARITY_COLORS["Ultra"])
    image[40:45, 30:70] = bgr  # 5 of 12 rows = 200/480 ≈ 41.7% of the patch, clears the 8% floor
    assert sample_rarity(image, bbox) == "Ultra"


def test_sample_rarity_falls_back_to_common_below_coverage_floor():
    # Glyph covers only a tiny sliver of the patch — below the min_pixel_ratio
    # floor — should NOT be trusted even though it's the closest color present.
    image = np.full((100, 100, 3), _BG_BGR, dtype=np.uint8)
    bbox = (40, 60, 60, 80)
    bgr = _hex_to_bgr(RARITY_COLORS["Ultra"])
    image[40:41, 30:32] = bgr  # 1 row x 2 px = 2/480 ≈ 0.4%, well under the 8% (38.4px) floor
    assert sample_rarity(image, bbox) == "Common"


_ALL_SPECIES = [
    "scorpion", "beetle", "cactus", "sandstorm",
    "sand_centipede", "soldier_fire_ant",
]
_BELOW_ULTRA = ["Common", "Unusual", "Rare", "Epic", "Legendary", "Mythic"]
_ABOVE_ULTRA = ["Super", "Eternal", "Unique"]


def test_classify_action_engage_below_ultra_any_species():
    for species in _ALL_SPECIES:
        for rarity in _BELOW_ULTRA:
            assert classify_action(species, rarity) == "ENGAGE"


def test_classify_action_ultra_avoid_species():
    assert classify_action("scorpion", "Ultra") == "AVOID"
    assert classify_action("beetle", "Ultra") == "AVOID"


def test_classify_action_ultra_cautious_species():
    for species in ["sandstorm", "cactus", "sand_centipede", "soldier_fire_ant"]:
        assert classify_action(species, "Ultra") == "CAUTIOUS"


def test_classify_action_above_ultra_falls_back_to_avoid():
    for species in _ALL_SPECIES:
        for rarity in _ABOVE_ULTRA:
            assert classify_action(species, rarity) == "AVOID"


def test_priority_score_rarity_dominates_species():
    # Rare sand_centipede(物种优先级最低)该压过Common sandstorm(物种优先级最高) ——
    # 稀有度是第一比较项, 碾压式的.
    assert priority_score("sand_centipede", "Rare") > priority_score("sandstorm", "Common")


def test_priority_score_species_tiebreak_within_same_rarity():
    assert priority_score("sandstorm", "Common") > priority_score("cactus", "Common")
    assert priority_score("cactus", "Common") > priority_score("beetle", "Common")
    assert priority_score("beetle", "Common") > priority_score("scorpion", "Common")
    assert priority_score("scorpion", "Common") > priority_score("sand_centipede", "Common")
    assert priority_score("sand_centipede", "Common") == priority_score("soldier_fire_ant", "Common")


def test_aim_mouse_target_points_toward_target_beyond_hold():
    result = aim_mouse_target((1460, 540), hold_px=None, center=(960, 540), max_extend=500)
    assert result[0] > 960
    assert abs(result[1] - 540) < 1e-6


def test_aim_mouse_target_stops_at_hold_distance():
    result = aim_mouse_target((1200, 540), hold_px=250, center=(960, 540))
    assert result == (960, 540)


def test_aim_mouse_target_clamps_to_max_extend():
    result = aim_mouse_target((3000, 540), hold_px=None, center=(960, 540), max_extend=500)
    assert result == (1460, 540)


def test_aim_mouse_target_chases_to_actual_distance_when_within_max_extend():
    # dist=100 is well inside max_extend=500 — the mouse should land exactly
    # at the target's offset from center, not jump all the way to max_extend.
    result = aim_mouse_target((1060, 540), hold_px=None, center=(960, 540), max_extend=500)
    assert result == (1060, 540)


def test_flee_mouse_target_points_away_from_single_threat():
    result = flee_mouse_target([(1460, 540)], center=(960, 540), extend=400)
    assert result[0] < 960
    assert abs(result[1] - 540) < 1e-6


def test_flee_mouse_target_returns_center_when_forces_cancel():
    result = flee_mouse_target([(1460, 540), (460, 540)], center=(960, 540))
    assert result == (960, 540)


from enemy_detect import select_action, chase_is_stalled


def _det(species, rarity, screen_pos):
    return {
        "species": species, "rarity": rarity, "screen_pos": screen_pos,
        "bbox": (0, 0, 0, 0), "confidence": 0.9,
    }


def test_select_action_flees_when_avoid_mob_in_range():
    detections = [
        _det("scorpion", "Ultra", (1100, 540)),   # 160px from center, 在触发半径内
        _det("sandstorm", "Common", (960, 700)),  # 优先级再高也不该盖过flee
    ]
    action, payload = select_action(detections, avoid_trigger_px=400)
    assert action == "flee"
    assert (1100, 540) in payload


def test_select_action_ignores_avoid_mob_outside_trigger_radius():
    detections = [
        _det("scorpion", "Ultra", (2000, 540)),   # 1040px, 远超触发半径
        _det("sandstorm", "Common", (1000, 560)),
    ]
    action, target, hold_px = select_action(detections, avoid_trigger_px=400)
    assert action == "chase"
    assert target["species"] == "sandstorm"
    assert hold_px is None


def test_select_action_chases_best_priority_candidate():
    detections = [
        _det("scorpion", "Common", (1000, 540)),
        _det("sand_centipede", "Rare", (1010, 540)),  # 稀有度更高, 该选它
    ]
    action, target, hold_px = select_action(detections)
    assert action == "chase"
    assert target["species"] == "sand_centipede"


def test_select_action_holds_distance_for_cautious_target():
    detections = [_det("cactus", "Ultra", (1000, 540))]
    action, target, hold_px = select_action(detections, cautious_hold_px=250)
    assert action == "chase"
    assert hold_px == 250


def test_select_action_wanders_with_no_relevant_detections():
    action, payload = select_action([])
    assert action == "wander"
    assert payload is None


def test_select_action_flee_excludes_out_of_range_avoid_mobs():
    detections = [
        _det("scorpion", "Ultra", (1060, 540)),  # 100px, in range
        _det("beetle", "Ultra", (60, 540)),       # 900px, out of range — must not dilute the flee vector
    ]
    action, payload = select_action(detections, avoid_trigger_px=400)
    assert action == "flee"
    assert payload == [(1060, 540)]


def test_chase_is_stalled_resets_on_movement():
    count, yield_now = chase_is_stalled((10, 10), (20, 20), stall_count=5)
    assert count == 0
    assert yield_now is False


def test_chase_is_stalled_increments_on_no_progress():
    count, yield_now = chase_is_stalled((10, 10), (10.5, 10.2), stall_count=3)
    assert count == 4
    assert yield_now is False


def test_chase_is_stalled_yields_at_limit():
    count, yield_now = chase_is_stalled((10, 10), (10, 10), stall_count=14, stall_limit=15)
    assert count == 15
    assert yield_now is True


def test_chase_is_stalled_handles_none_positions():
    count, yield_now = chase_is_stalled(None, (10, 10), stall_count=9)
    assert count == 0
    assert yield_now is False


import pytest

from enemy_detect import load_enemy_model, scan_enemies

_HAS_MODEL = os.path.exists("models/desert.pt")
_SKIP_REASON = "models/desert.pt not present locally (gitignored, user-provided)"


@pytest.mark.skipif(not _HAS_MODEL, reason=_SKIP_REASON)
def test_load_enemy_model_exposes_expected_classes():
    model = load_enemy_model()
    expected = {
        "scorpion", "beetle", "cactus",
        "sandstorm", "sand_centipede", "soldier_fire_ant",
    }
    assert set(model.names.values()) == expected


@pytest.mark.skipif(not _HAS_MODEL, reason=_SKIP_REASON)
def test_scan_enemies_returns_empty_list_for_blank_image():
    blank = np.zeros((640, 640, 3), dtype=np.uint8)
    assert scan_enemies(image=blank) == []
