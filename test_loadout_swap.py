import pytest

import loadout_swap


def _recorder():
    calls = []
    return calls, lambda k: calls.append(k)


@pytest.mark.parametrize("spec", ["none", None, "", "zzz", "1", "digit"])
def test_noop_specs_never_press(spec):
    calls, rec = _recorder()
    loadout_swap.press_swap(spec, press=rec, sleep=lambda _s: None)
    assert calls == []


def test_k_presses_k_once():
    calls, rec = _recorder()
    loadout_swap.press_swap("k", press=rec, sleep=lambda _s: None)
    assert calls == ["k"]


def test_l_presses_l_once():
    calls, rec = _recorder()
    loadout_swap.press_swap("l", press=rec, sleep=lambda _s: None)
    assert calls == ["l"]


def test_digits_presses_1_through_0_with_sleeps():
    calls, rec = _recorder()
    slept = []
    loadout_swap.press_swap("digits", press=rec, sleep=slept.append)
    assert calls == list("1234567890")
    assert len(slept) == 10
    assert all(s == pytest.approx(0.04) for s in slept)


def test_press_exception_is_swallowed(capsys):
    def boom(_k):
        raise RuntimeError("no focus")
    loadout_swap.press_swap("k", press=boom, sleep=lambda _s: None)   # 不抛
    assert "装备切换按键失败" in capsys.readouterr().out
