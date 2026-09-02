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


def test_plan_transition_noop_same_block():
    blk = {"id": "b1", "profile": "默认"}
    assert gui_app.plan_transition("b1", blk, "默认") == {"action": "noop"}


def test_plan_transition_idle_when_leaving_to_gap():
    assert gui_app.plan_transition("b1", None, "默认") == {"action": "idle"}


def test_plan_transition_noop_when_already_idle():
    assert gui_app.plan_transition(None, None, None) == {"action": "noop"}


def test_plan_transition_run_same_profile_no_relaunch():
    blk = {"id": "b2", "profile": "默认"}
    assert gui_app.plan_transition("b1", blk, "默认") == {
        "action": "run", "relaunch_chrome": False, "profile": "默认"}


def test_plan_transition_run_other_profile_relaunches():
    blk = {"id": "b2", "profile": "小号2"}
    assert gui_app.plan_transition("b1", blk, "默认") == {
        "action": "run", "relaunch_chrome": True, "profile": "小号2"}


def test_plan_transition_run_from_idle_after_worker_crash():
    blk = {"id": "b1", "profile": "默认"}
    # worker 崩了 -> _running_block_id 清成 None, chrome 还在『默认』
    assert gui_app.plan_transition(None, blk, "默认") == {
        "action": "run", "relaunch_chrome": False, "profile": "默认"}


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


def test_start_afk_already_running():
    assert gui_app.start_afk(exe_exists=True, running=True,
                             confirm_download=lambda: pytest.fail()) == "already"


def test_start_afk_missing_exe_declined():
    assert gui_app.start_afk(exe_exists=False, running=False,
                             confirm_download=lambda: False) == "declined"


def test_start_afk_missing_exe_download_ok(monkeypatch):
    monkeypatch.setattr(gui_app.afk_watch, "download_florr_auto_afk", lambda: True)
    assert gui_app.start_afk(exe_exists=False, running=False,
                             confirm_download=lambda: True) == "downloaded"


def test_start_afk_missing_exe_download_fails(monkeypatch):
    monkeypatch.setattr(gui_app.afk_watch, "download_florr_auto_afk", lambda: False)
    assert gui_app.start_afk(exe_exists=False, running=False,
                             confirm_download=lambda: True) == "download_failed"


def test_start_afk_exe_present_not_running():
    assert gui_app.start_afk(exe_exists=True, running=False,
                             confirm_download=lambda: pytest.fail()) == "started"


def test_resolve_point_and_area_both_given_unchanged():
    p, a = gui_app.resolve_point_and_area((20, 30), [(5, 5), (40, 40)])
    assert p == (20, 30)
    assert a == [(5, 5), (40, 40)]


def test_resolve_point_and_area_neither():
    assert gui_app.resolve_point_and_area(None, None) == (None, None)


def test_resolve_point_and_area_only_area_derives_center():
    p, a = gui_app.resolve_point_and_area(None, [(10, 20), (30, 60)])
    assert p == (20, 40)          # ((10+30)//2, (20+60)//2)
    assert a == [(10, 20), (30, 60)]


def test_resolve_point_and_area_only_point_derives_box():
    p, a = gui_app.resolve_point_and_area((100, 120), None)
    assert p == (100, 120)
    h = gui_app._DERIVED_AREA_HALF
    assert a == [(100 - h, 120 - h), (100 + h, 120 + h)]


def test_resolve_point_and_area_only_point_clamps_to_map_edges():
    p, a = gui_app.resolve_point_and_area((2, 297), None)
    assert a == [(0, 297 - gui_app._DERIVED_AREA_HALF), (2 + gui_app._DERIVED_AREA_HALF, 299)]
