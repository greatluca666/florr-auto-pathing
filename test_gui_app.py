import os
import sys

import pytest

import gui_app


def test_worker_command_script_mode(monkeypatch):
    monkeypatch.setattr(gui_app.sys, "frozen", False, raising=False)
    cmd = gui_app.worker_command()
    assert cmd[0] == sys.executable
    assert cmd[1] == "-u"
    assert cmd[-1] == "--worker"
    assert cmd[-2].endswith("main.py")
    assert os.path.isabs(cmd[-2])


def test_worker_command_frozen_mode(monkeypatch):
    monkeypatch.setattr(gui_app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui_app.sys, "executable", "/opt/florr/florr-auto-pathing")
    assert gui_app.worker_command() == ["/opt/florr/florr-auto-pathing", "--worker"]


def test_build_worker_config_shapes_values():
    cfg = gui_app.build_worker_config(
        map_name="ocean", location=(11, 22), area=[(1, 2), (3, 4)],
        duration=120, short_limit=3, enemy_ai=False, auto_switch=True, afk=True,
    )
    assert cfg == {
        "map": "ocean",
        "location": [11, 22],
        "farming_area": [[1, 2], [3, 4]],
        "farming_duration": 120,
        "consecutive_short_round_limit": 3,
        "enemy_ai_enabled": False,
        "auto_switch_server": True,
        "afk_enabled": True,
    }


@pytest.mark.parametrize("args, expected", [
    (("300", "2"), [300, 2]),
    (("0", "2"), None),
    (("-5", "2"), None),
    (("", "2"), None),
    (("3.5", "2"), None),
    (("  5  ", "2"), [5, 2]),  # int() 会自己 strip 两边空白
])
def test_parse_positive_ints(args, expected):
    assert gui_app.parse_positive_ints(*args) == expected
