import copy
import json

import pytest

import app_config


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "CONFIG_PATH", str(p))
    return p


def test_load_missing_file_returns_defaults(cfg_path):
    assert app_config.load_config() == app_config.DEFAULTS
    # 返回的必须是副本, 改它不能污染 DEFAULTS
    got = app_config.load_config()
    got["farming_duration"] = 999
    assert app_config.DEFAULTS["farming_duration"] != 999


def test_save_then_load_roundtrips(cfg_path):
    cfg = copy.deepcopy(app_config.DEFAULTS)
    cfg["map"] = "ocean"
    cfg["location"] = [100, 120]
    cfg["farming_area"] = [[10, 10], [40, 40]]
    cfg["enemy_ai_enabled"] = False
    app_config.save_config(cfg)
    assert app_config.load_config() == cfg


def test_bad_json_falls_back_to_defaults(cfg_path):
    cfg_path.write_text("{ not json", encoding="utf-8")
    assert app_config.load_config() == app_config.DEFAULTS


def test_partial_file_fills_missing_keys(cfg_path):
    cfg_path.write_text(json.dumps({"map": "anthell"}), encoding="utf-8")
    got = app_config.load_config()
    assert got["map"] == "anthell"
    assert got["farming_duration"] == app_config.DEFAULTS["farming_duration"]


@pytest.mark.parametrize("bad", [
    {"map": "nonsense"},
    {"map": 123},
    {"location": [1, 2, 3]},
    {"location": "12,32"},
    {"farming_area": [[1, 2], [3, 4], [5, 6]]},
    {"farming_area": [[1, 2], [3]]},
    {"farming_duration": -5},
    {"farming_duration": "300"},
    {"consecutive_short_round_limit": 0},
    {"enemy_ai_enabled": "yes"},
])
def test_bad_value_reverts_that_key_to_default(cfg_path, bad):
    key = next(iter(bad))
    cfg_path.write_text(json.dumps(bad), encoding="utf-8")
    got = app_config.load_config()
    assert got[key] == app_config.DEFAULTS[key]


def test_top_level_not_dict_falls_back(cfg_path):
    cfg_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert app_config.load_config() == app_config.DEFAULTS


def test_unknown_keys_are_dropped(cfg_path):
    cfg_path.write_text(json.dumps({"map": "desert", "bogus": 1}), encoding="utf-8")
    assert "bogus" not in app_config.load_config()


class TestScheduleMath:
    def _blk(self, **kw):
        base = dict(id="b", enabled=True, days=[0], start="09:00", end="12:00",
                    profile="默认", map="desert", location=[1, 2],
                    farming_area=[[0, 0], [9, 9]], farming_duration=300,
                    consecutive_short_round_limit=2, enemy_ai_enabled=True,
                    auto_switch_server=True)
        base.update(kw)
        return base

    def test_hhmm_to_min(self):
        assert app_config._hhmm_to_min("00:00") == 0
        assert app_config._hhmm_to_min("09:30") == 570
        assert app_config._hhmm_to_min("23:59") == 1439

    @pytest.mark.parametrize("s, ok", [
        ("09:00", True), ("00:00", True), ("23:59", True),
        ("9:00", False), ("24:00", False), ("12:60", False), ("", False), (None, False),
    ])
    def test_valid_time(self, s, ok):
        assert app_config._valid_time(s) is ok

    def test_expand_plain(self):
        assert app_config.expand_block_days(self._blk(days=[0, 3])) == [
            (0, 540, 720), (3, 540, 720)]

    def test_expand_all_day(self):
        assert app_config.expand_block_days(
            self._blk(days=[2], start="00:00", end="00:00")) == [(2, 0, 1440)]

    def test_expand_cross_midnight(self):
        assert app_config.expand_block_days(
            self._blk(days=[0], start="22:00", end="02:00")) == [
            (0, 1320, 1440), (1, 0, 120)]

    def test_overlap_same_day_intersect(self):
        assert app_config.blocks_overlap(
            self._blk(start="09:00", end="12:00"),
            self._blk(start="11:00", end="13:00")) is True

    def test_overlap_different_day(self):
        assert app_config.blocks_overlap(
            self._blk(days=[0]), self._blk(days=[1])) is False

    def test_overlap_touching_edges_not_overlap(self):
        assert app_config.blocks_overlap(
            self._blk(start="09:00", end="12:00"),
            self._blk(start="12:00", end="15:00")) is False

    def test_overlap_cross_midnight_spills_into_next_day(self):
        assert app_config.blocks_overlap(
            self._blk(days=[0], start="22:00", end="02:00"),
            self._blk(days=[1], start="01:00", end="03:00")) is True

    def test_active_block_hit_and_miss(self):
        sched = [self._blk(id="x", days=[0], start="09:00", end="12:00")]
        assert app_config.active_block(sched, 0, "10:00")["id"] == "x"
        assert app_config.active_block(sched, 0, "12:00") is None   # 半开区间
        assert app_config.active_block(sched, 1, "10:00") is None

    def test_active_block_skips_disabled(self):
        sched = [self._blk(id="x", enabled=False, days=[0], start="09:00", end="12:00")]
        assert app_config.active_block(sched, 0, "10:00") is None

    def test_active_block_cross_midnight_belongs_to_next_day(self):
        sched = [self._blk(id="x", days=[0], start="22:00", end="02:00")]
        assert app_config.active_block(sched, 1, "01:00")["id"] == "x"

    def test_next_start_same_day(self):
        sched = [self._blk(days=[0], start="18:00", end="20:00")]
        assert app_config.next_start(sched, 0, "09:00") == (0, "18:00")

    def test_next_start_wraps_week(self):
        sched = [self._blk(days=[2], start="09:00", end="12:00")]
        assert app_config.next_start(sched, 3, "10:00") == (2, "09:00")

    def test_next_start_none_when_empty(self):
        assert app_config.next_start([], 0, "09:00") is None
