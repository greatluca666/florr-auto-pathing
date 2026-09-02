import pytest

import gui_schedule as gs


def _blk(**kw):
    base = dict(id="b", enabled=True, days=[0], start="09:00", end="12:00",
               profile="默认", map="desert", location=[1, 2],
               farming_area=[[0, 0], [9, 9]], farming_duration=300,
               consecutive_short_round_limit=2, enemy_ai_enabled=True,
               auto_switch_server=True)
    base.update(kw)
    return base


@pytest.mark.parametrize("raw, out", [
    ("小号2", "小号2"),
    ("main account", "main_account"),
    ("a/b\\c", "a_b_c"),
    ("  x  ", "x"),
    ("***", ""),
])
def test_safe_dirname(raw, out):
    assert gs._safe_dirname(raw) == out


def test_block_to_active_shapes():
    a = gs.block_to_active(_blk(map="ocean", location=(5, 6),
                               farming_area=[(1, 1), (2, 2)], farming_duration="120"))
    assert a == {
        "map": "ocean", "location": [5, 6], "farming_area": [[1, 1], [2, 2]],
        "farming_duration": 120, "consecutive_short_round_limit": 2,
        "enemy_ai_enabled": True, "auto_switch_server": True,
        "enter_game_swap": "none", "reach_area_swap": "none",
    }


def test_validate_ok():
    assert gs.validate_block(_blk(), []) is None


def test_validate_no_days():
    assert "星期" in gs.validate_block(_blk(days=[]), [])


def test_validate_bad_time():
    assert gs.validate_block(_blk(start="9am"), [])


def test_validate_equal_times():
    assert gs.validate_block(_blk(start="09:00", end="09:00"), [])


def test_validate_all_day_equal_times_ok():
    assert gs.validate_block(_blk(start="00:00", end="00:00"), []) is None


def test_validate_no_point_no_area():
    assert gs.validate_block(_blk(location=None, farming_area=None), [])


def test_validate_bad_numbers():
    assert gs.validate_block(_blk(farming_duration=0), [])
    assert gs.validate_block(_blk(consecutive_short_round_limit=-1), [])


def test_validate_overlap_reports_other_id():
    other = _blk(id="blk-9", start="11:00", end="13:00")
    msg = gs.validate_block(_blk(id="mine"), [other])
    assert "blk-9" in msg


def test_validate_ignores_self_in_others():
    me = _blk(id="mine")
    assert gs.validate_block(me, [me]) is None


class TestLoadoutSwapInGuiSchedule:
    def test_block_to_active_carries_swaps(self):
        blk = _blk(enter_game_swap="k", reach_area_swap="digits")
        act = gs.block_to_active(blk)
        assert act["enter_game_swap"] == "k"
        assert act["reach_area_swap"] == "digits"

    def test_block_to_active_defaults_missing_swaps_to_none(self):
        blk = _blk()
        blk.pop("enter_game_swap", None)
        blk.pop("reach_area_swap", None)
        act = gs.block_to_active(blk)
        assert act["enter_game_swap"] == "none"
        assert act["reach_area_swap"] == "none"

    def test_block_to_active_coerces_illegal_present_value_to_none(self):
        # 键在, 但值不在合法集合里 —— 回落 none, 不是原样带过
        blk = _blk(enter_game_swap="xyz")
        assert gs.block_to_active(blk)["enter_game_swap"] == "none"

    def test_new_block_template_has_none_swaps(self):
        cfg = {"profiles": [{"alias": "默认", "dir": "d"}], "schedule": []}
        tpl = gs.new_block_template(cfg)
        assert tpl["enter_game_swap"] == "none"
        assert tpl["reach_area_swap"] == "none"

    def test_label_maps_are_inverse(self):
        for k in ("none", "digits", "k", "l"):
            assert gs._SWAP_FROM_LABEL[gs._SWAP_LABELS[k]] == k

