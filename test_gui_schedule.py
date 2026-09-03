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
    _off = {"enabled": False, "mod": "none", "digit": "1"}
    assert a == {
        "map": "ocean", "location": [5, 6], "farming_area": [[1, 1], [2, 2]],
        "farming_duration": 120, "consecutive_short_round_limit": 2,
        "enemy_ai_enabled": True, "auto_switch_server": True,
        "invert_attack": True, "invert_defense": False,
        "enter_game_swap": _off, "reach_area_swap": _off,
    }


def test_map_radio_state():
    assert gs._map_radio_state("desert") == "normal"
    assert gs._map_radio_state("ocean") == "disabled"
    assert gs._map_radio_state("anthell") == "disabled"


def test_validate_rejects_disabled_map():
    msg = gs.validate_block(_blk(map="ocean"), [])
    assert msg is not None and "暂不可用" in msg


def test_validate_accepts_desert_map():
    # _blk() 默认 map="desert" —— 已被 test_validate_ok 覆盖, 这里显式再钉一次
    assert gs.validate_block(_blk(map="desert"), []) is None


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


_OFF = {"enabled": False, "mod": "none", "digit": "1"}


class TestLoadoutSwapInGuiSchedule:
    def test_block_to_active_carries_chord(self):
        blk = _blk(enter_game_swap={"enabled": True, "mod": "k", "digit": "3"},
                   reach_area_swap={"enabled": True, "mod": "none", "digit": "7"})
        act = gs.block_to_active(blk)
        assert act["enter_game_swap"] == {"enabled": True, "mod": "k", "digit": "3"}
        assert act["reach_area_swap"] == {"enabled": True, "mod": "none", "digit": "7"}

    def test_block_to_active_defaults_missing_swaps(self):
        blk = _blk()
        blk.pop("enter_game_swap", None)
        blk.pop("reach_area_swap", None)
        act = gs.block_to_active(blk)
        assert act["enter_game_swap"] == _OFF
        assert act["reach_area_swap"] == _OFF

    def test_block_to_active_normalizes_bad_chord(self):
        blk = _blk(enter_game_swap={"enabled": 1, "mod": "ctrl", "digit": "x"})
        assert gs.block_to_active(blk)["enter_game_swap"] == _OFF

    def test_new_block_template_swaps_disabled(self):
        cfg = {"profiles": [{"alias": "默认", "dir": "d"}], "schedule": []}
        tpl = gs.new_block_template(cfg)
        assert tpl["enter_game_swap"] == _OFF
        assert tpl["reach_area_swap"] == _OFF

    def test_mod_label_maps_are_inverse(self):
        for k in ("none", "k", "l"):
            assert gs._SWAP_MOD_FROM_LABEL[gs._SWAP_MOD_LABELS[k]] == k

    def test_digit_values_are_1_through_0(self):
        assert gs._SWAP_DIGIT_VALUES == list("1234567890")


class TestInvertTogglesInGuiSchedule:
    def test_block_to_active_carries_invert(self):
        blk = _blk(invert_attack=False, invert_defense=True)
        act = gs.block_to_active(blk)
        assert act["invert_attack"] is False
        assert act["invert_defense"] is True

    def test_block_to_active_invert_defaults_when_missing(self):
        blk = _blk()
        blk.pop("invert_attack", None)
        blk.pop("invert_defense", None)
        act = gs.block_to_active(blk)
        assert act["invert_attack"] is True
        assert act["invert_defense"] is False

    def test_new_block_template_invert_defaults(self):
        cfg = {"profiles": [{"alias": "默认", "dir": "d"}], "schedule": []}
        tpl = gs.new_block_template(cfg)
        assert tpl["invert_attack"] is True
        assert tpl["invert_defense"] is False

