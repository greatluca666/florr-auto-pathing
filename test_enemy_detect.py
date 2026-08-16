import numpy as np

from enemy_detect import sample_rarity, RARITY_COLORS, _hex_to_bgr


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
