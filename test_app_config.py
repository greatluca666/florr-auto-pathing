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
