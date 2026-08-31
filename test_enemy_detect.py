import os

import numpy as np

from enemy_detect import (
    sample_rarity, _find_hp_bar, RARITY_COLORS, RARITY_ORDER, RARITY_RANK,
    _hex_to_bgr, classify_action, priority_score, aim_mouse_target, flee_mouse_target,
)

# Sandy background BGR — not green (so _find_hp_bar's green mask ignores it: its
# R=210 fails the mask's r<205), and outside every rarity color's ±40 cube.
_BG_BGR = (120, 170, 210)
# Health-bar green, measured off a real florr.io screenshot (屏幕截图 220017.png):
# BGR ≈ (48, 208, 112). Passes _find_hp_bar's green mask.
_BAR_BGR = (48, 208, 112)


def _name_tag_image(rarity_bgr, word_rows=10, word_cols=50):
    """Synthesise a florr.io mob name tag the way the game lays it out: a long
    thin green HP bar under the mob, with the rarity word (rarity-coloured)
    just below the bar, right-aligned to the bar's right end. Returns
    (image, bbox). `word_rows`/`word_cols` control how much of sample_rarity's
    word region the coloured glyphs actually fill (rest stays background)."""
    img = np.full((200, 300, 3), _BG_BGR, dtype=np.uint8)
    bbox = (60, 20, 160, 90)          # mob box, h = 70
    bar_y, bar_x0, bar_x1, thick = 110, 70, 190, 5
    img[bar_y:bar_y + thick, bar_x0:bar_x1] = _BAR_BGR
    # sample_rarity's word region for this bar: ry0 = bar_y + thick//2 + 1 = 113,
    # rx1 ≈ bar_x1, extending left. Paint the glyphs inside it.
    gy = bar_y + thick // 2 + 1 + 1
    gx1 = bar_x1 - 4
    if rarity_bgr is not None:
        img[gy:gy + word_rows, gx1 - word_cols:gx1] = rarity_bgr
    return img, bbox


def test_sample_rarity_matches_known_colors():
    # Only Common..Ultra are scanned (Super/Eternal/Unique don't spawn in this
    # zone and their colours false-match the bar green / outline black).
    for name in RARITY_ORDER[:RARITY_RANK["Ultra"] + 1]:
        img, bbox = _name_tag_image(_hex_to_bgr(RARITY_COLORS[name]))
        assert sample_rarity(img, bbox) == name, name


def test_sample_rarity_above_ultra_colors_read_as_common():
    # Super/Eternal/Unique are deliberately NOT scanned — feeding their colour
    # resolves to Common (below floor), never a spurious high-rarity read that
    # would wrongly trip AVOID on a normal mob.
    for name in ("Super", "Eternal", "Unique"):
        img, bbox = _name_tag_image(_hex_to_bgr(RARITY_COLORS[name]))
        assert sample_rarity(img, bbox) == "Common", name


def test_sample_rarity_falls_back_to_common_when_no_hp_bar():
    image = np.zeros((100, 100, 3), dtype=np.uint8)  # pure black, no green bar
    assert sample_rarity(image, (40, 60, 60, 80)) == "Common"


def test_sample_rarity_falls_back_to_common_below_coverage_floor():
    # HP bar present (so an anchor is found) but the rarity glyphs cover only a
    # sliver of the word region — under min_pixel_ratio, so not trusted.
    img, bbox = _name_tag_image(_hex_to_bgr(RARITY_COLORS["Ultra"]), word_rows=1, word_cols=2)
    assert sample_rarity(img, bbox) == "Common"


def test_find_hp_bar_locates_bar_and_rejects_blob():
    img, bbox = _name_tag_image(_hex_to_bgr(RARITY_COLORS["Rare"]))
    bar = _find_hp_bar(img, bbox)
    assert bar is not None
    bar_x0, bar_y, bar_x1, thick = bar
    assert abs(bar_y - 110) <= 1 and thick <= 8 and (bar_x1 - bar_x0) >= 90

    # A big green blob (translucent cactus/sandstorm body) is not bar-shaped:
    # fails the run >= 4*thick aspect check -> None.
    blob = np.full((200, 300, 3), _BG_BGR, dtype=np.uint8)
    blob[40:110, 70:170] = _BAR_BGR
    assert _find_hp_bar(blob, (60, 20, 160, 90)) is None


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


def test_aim_mouse_target_no_repel_positions_is_unchanged():
    # repel_positions 为 None/空 -> 跟没这个参数时结果完全一致
    a = aim_mouse_target((1060, 540), hold_px=None, center=(960, 540), max_extend=500)
    b = aim_mouse_target((1060, 540), hold_px=None, center=(960, 540), max_extend=500,
                         repel_positions=[])
    assert a == b == (1060, 540)


def test_aim_mouse_target_bends_away_from_danger_on_the_path():
    # 目标正右方, 一只危险怪在右上方近处 -> 瞄点应被往下压 (远离危险), 但整体
    # 仍朝右 (还在追)
    result = aim_mouse_target((1460, 540), hold_px=None, center=(960, 540), max_extend=500,
                              repel_positions=[(1160, 440)], repel_px=400)
    assert result[0] > 960          # 仍在朝目标方向 (右)
    assert result[1] > 540          # 被危险怪 (在上方, y 小) 往下推


def test_aim_mouse_target_repels_even_while_holding_distance():
    # 已进 CAUTIOUS 保持距离内, 平时返回 center; 但半路有危险怪 -> 仍往远离方向挪
    held = aim_mouse_target((1100, 540), hold_px=250, center=(960, 540))
    assert held == (960, 540)
    with_danger = aim_mouse_target((1100, 540), hold_px=250, center=(960, 540),
                                   repel_positions=[(960, 440)], repel_px=400)
    assert with_danger != (960, 540)
    assert with_danger[1] > 540     # 危险怪在上方 -> 往下挪


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
    action, payload = select_action(detections, avoid_trigger_px=400, center=(960, 540))
    assert action == "flee"
    assert (1100, 540) in payload


def test_select_action_ignores_avoid_mob_outside_trigger_radius():
    detections = [
        _det("scorpion", "Ultra", (2000, 540)),   # 1040px, 远超触发半径
        _det("sandstorm", "Common", (1000, 560)),
    ]
    action, target, hold_px, repel = select_action(detections, avoid_trigger_px=400, center=(960, 540))
    assert action == "chase"
    assert target["species"] == "sandstorm"
    assert hold_px is None
    # AVOID怪没触发flee, 但还是进repel列表让追击路径绕开它
    assert (2000, 540) in repel


def test_select_action_chases_best_priority_candidate():
    detections = [
        _det("scorpion", "Common", (1000, 540)),
        _det("sand_centipede", "Rare", (1010, 540)),  # 稀有度更高, 该选它
    ]
    action, target, hold_px, repel = select_action(detections, center=(960, 540))
    assert action == "chase"
    assert target["species"] == "sand_centipede"
    assert repel == []   # 没有AVOID/别的CAUTIOUS, 没什么要绕的


def test_select_action_holds_distance_for_cautious_target():
    detections = [_det("cactus", "Ultra", (1000, 540))]
    action, target, hold_px, repel = select_action(detections, cautious_hold_px=250, center=(960, 540))
    assert action == "chase"
    assert hold_px == 250
    assert repel == []   # 目标本身是CAUTIOUS, 不该把自己放进repel


def test_select_action_chase_repels_around_other_danger_mobs():
    detections = [
        _det("sandstorm", "Ultra", (1100, 540)),   # CAUTIOUS, 物种优先级最高 -> 目标
        _det("cactus", "Ultra", (900, 400)),        # CAUTIOUS, 不是目标 -> 要绕开
        _det("scorpion", "Ultra", (300, 540)),      # AVOID, 660px>400 不触发flee -> 也要绕开
    ]
    action, target, hold_px, repel = select_action(detections, avoid_trigger_px=400, center=(960, 540))
    assert action == "chase"
    assert target["species"] == "sandstorm"
    assert hold_px == 250                           # 目标是 CAUTIOUS
    assert (900, 400) in repel and (300, 540) in repel
    assert (1100, 540) not in repel                 # 目标本身不进 repel


def test_select_action_wanders_with_no_relevant_detections():
    action, payload = select_action([])
    assert action == "wander"
    assert payload is None


def test_select_action_flee_excludes_out_of_range_avoid_mobs():
    detections = [
        _det("scorpion", "Ultra", (1060, 540)),  # 100px, in range
        _det("beetle", "Ultra", (60, 540)),       # 900px, out of range — must not dilute the flee vector
    ]
    action, payload = select_action(detections, avoid_trigger_px=400, center=(960, 540))
    assert action == "flee"
    assert payload == [(1060, 540)]


def test_chase_is_stalled_false_until_window_full():
    # 样本还没攒满一个 window -> 不判 (返回 False)
    hist = [(0.0, 0.0)] * 10
    assert chase_is_stalled(hist, window=25) is False


def test_chase_is_stalled_true_when_net_displacement_below_threshold():
    # 攒满 window, 首尾净位移几乎为 0 (贴墙被顶住) -> 卡住
    hist = [(5.0 + 0.1 * (i % 2), 5.0) for i in range(25)]   # 只在 0.1 之间抖
    assert chase_is_stalled(hist, min_progress=4.0, window=25) is True


def test_chase_is_stalled_false_when_circling_but_making_progress():
    # 追一个走位的目标: 每 tick 挪一点点 (相邻差 < 1.5, 旧写法会误判卡住),
    # 但一个 window 下来净位移累积过阈值 -> 不算卡住
    hist = [(i * 0.5, 0.0) for i in range(25)]   # 24*0.5 = 12 净位移 > 4.0
    assert chase_is_stalled(hist, min_progress=4.0, window=25) is False


def test_chase_is_stalled_uses_last_window_of_a_longer_history():
    # history 比 window 长时只看最后 window 个样本
    hist = [(i * 5.0, 0.0) for i in range(20)]           # 早期大位移
    hist += [(95.0 + 0.1 * (i % 2), 0.0) for i in range(25)]  # 最近 25 tick 停住
    assert chase_is_stalled(hist, min_progress=4.0, window=25) is True


def test_chase_is_stalled_handles_none_and_empty():
    assert chase_is_stalled(None) is False
    assert chase_is_stalled([]) is False


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
