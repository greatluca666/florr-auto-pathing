from overlay import _format_elapsed, _format_pos, _merge_state


def test_format_elapsed_zero():
    assert _format_elapsed(0) == "00:00"


def test_format_elapsed_under_a_minute():
    assert _format_elapsed(45) == "00:45"


def test_format_elapsed_minutes_and_seconds():
    assert _format_elapsed(125) == "02:05"


def test_format_elapsed_negative_clamps_to_zero():
    assert _format_elapsed(-5) == "00:00"


def test_format_pos_none():
    assert _format_pos(None) == "-"


def test_format_pos_tuple():
    assert _format_pos((14, 45)) == "(14, 45)"


def test_merge_state_overwrites_only_provided_fields():
    current = {"state": "idle", "pos": None, "target": None, "message": "-"}
    updated = _merge_state(current, state="寻路中", pos=(1, 2))
    assert updated == {"state": "寻路中", "pos": (1, 2), "target": None, "message": "-"}


def test_merge_state_none_values_do_not_overwrite():
    current = {"state": "寻路中", "pos": (1, 2), "target": None, "message": "-"}
    updated = _merge_state(current, state=None, message="卡住了")
    assert updated == {"state": "寻路中", "pos": (1, 2), "target": None, "message": "卡住了"}


def test_merge_state_does_not_mutate_input():
    current = {"state": "idle"}
    _merge_state(current, state="移动中")
    assert current == {"state": "idle"}


import overlay as overlay_module


def test_create_overlay_falls_back_when_tk_unavailable(monkeypatch):
    def raise_tcl_error(*args, **kwargs):
        raise RuntimeError("no display")

    monkeypatch.setattr(overlay_module.tkinter, "Tk", raise_tcl_error)
    result = overlay_module.create_overlay()
    assert isinstance(result, overlay_module._NullOverlay)
    # must never raise, whatever it's called with
    result.update(state="寻路中", pos=(1, 2), target=(3, 4), message="test")
    result.close()


def test_null_overlay_update_ignores_all_args():
    stub = overlay_module._NullOverlay()
    assert stub.update(state="x", pos=(1, 1), target=(2, 2), message="y") is None
