import copy
import json

import pytest

import app_config


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "CONFIG_PATH", str(p))
    return p


def _v2_block(**kw):
    base = dict(id="blk-1", enabled=True, days=[0, 1, 2, 3, 4, 5, 6],
                start="00:00", end="00:00", profile="默认", map="desert",
                location=[22, 32], farming_area=[[9, 8], [51, 56]],
                farming_duration=300, consecutive_short_round_limit=2,
                enemy_ai_enabled=True, auto_switch_server=True)
    base.update(kw)
    return base


def _v2_cfg(**kw):
    c = copy.deepcopy(app_config.DEFAULTS_V2)
    c["schedule"] = [_v2_block()]
    c["profiles"] = [{"alias": "默认", "dir": "chrome-profiles/默认"}]
    c.update(kw)
    return c


class TestCoerceV2:
    def test_missing_file_returns_defaults_v2(self, cfg_path):
        assert app_config.load_config() == app_config.DEFAULTS_V2

    def test_v2_roundtrips(self, cfg_path):
        cfg = _v2_cfg()
        app_config.save_config(cfg)
        assert app_config.load_config() == app_config.load_config()  # 稳定
        got = app_config.load_config()
        assert got["schedule"][0]["id"] == "blk-1"
        assert got["profiles"] == [{"alias": "默认", "dir": "chrome-profiles/默认"}]

    def test_top_level_not_dict_falls_back(self, cfg_path):
        cfg_path.write_text("[1,2,3]", encoding="utf-8")
        assert app_config.load_config() == app_config.DEFAULTS_V2

    @pytest.mark.parametrize("bad_block", [
        {"days": []},
        {"days": [7]},
        {"start": "9:00"},
        {"start": "10:00", "end": "10:00"},   # start==end 非全天
        {"map": "nonsense"},
        {"location": "12,32"},
        {"farming_area": [[1, 2], [3]]},
        {"farming_duration": 0},
        {"enemy_ai_enabled": "yes"},
    ])
    def test_bad_block_is_dropped_whole(self, cfg_path, bad_block):
        good = _v2_block(id="good")
        bad = _v2_block(id="bad", **bad_block)
        cfg_path.write_text(json.dumps(_v2_cfg(schedule=[bad, good])), encoding="utf-8")
        got = app_config.load_config()
        assert [b["id"] for b in got["schedule"]] == ["good"]

    def test_dangling_profile_disables_block_not_drops(self, cfg_path):
        blk = _v2_block(id="x", profile="不存在")
        cfg_path.write_text(json.dumps(_v2_cfg(schedule=[blk])), encoding="utf-8")
        got = app_config.load_config()
        assert [b["id"] for b in got["schedule"]] == ["x"]
        assert got["schedule"][0]["enabled"] is False

    def test_empty_profiles_gets_default(self, cfg_path):
        cfg_path.write_text(json.dumps(_v2_cfg(profiles=[])), encoding="utf-8")
        assert app_config.load_config()["profiles"] == [
            {"alias": "默认", "dir": "chrome-profiles/默认"}]

    def test_duplicate_alias_deduped(self, cfg_path):
        cfg_path.write_text(json.dumps(_v2_cfg(profiles=[
            {"alias": "a", "dir": "chrome-profiles/a"},
            {"alias": "a", "dir": "chrome-profiles/a2"},
        ], schedule=[])), encoding="utf-8")
        got = app_config.load_config()
        assert [p["alias"] for p in got["profiles"]] == ["a"]

    def test_days_sorted_deduped(self, cfg_path):
        blk = _v2_block(days=[4, 0, 0, 2])
        cfg_path.write_text(json.dumps(_v2_cfg(schedule=[blk])), encoding="utf-8")
        assert app_config.load_config()["schedule"][0]["days"] == [0, 2, 4]

    def test_active_defaults_from_first_block_when_absent(self, cfg_path):
        c = _v2_cfg(schedule=[_v2_block(map="ocean", location=[5, 5])])
        del c["active"]
        cfg_path.write_text(json.dumps(c), encoding="utf-8")
        got = app_config.load_config()
        assert got["active"]["map"] == "ocean"
        assert got["active"]["location"] == [5, 5]


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
