"""进游戏 / 到刷怪区时按一组键切换 florr loadout.

跟 florr_settings.py / server_lookup.py 一样: 小、单一职责、不 import GUI、
不 import main. press / sleep 通过参数注入, 方便单测.
"""
import time

import pyautogui

_DIGITS = "1234567890"


def press_swap(spec, *, press=pyautogui.press, sleep=time.sleep):
    """按一组切换 loadout 的键.

    spec:
      - "none" / None / "" / 未知值: 什么都不做.
      - "k" / "l": 按一下 (florr 里绑的 loadout 预设键).
      - "digits": 顺序点按 1..0, 每键之间 ~40ms —— 把每个槽位在主/副行之间
        对调一遍 = 换整套.

    任何异常吞掉 + 打日志, 绝不抛给调用方: 切装备是附加动作, 不能打断刷怪轮次
    (对齐 main._reassert_invert_attack 的 warn-only).
    """
    if spec not in ("k", "l", "digits"):
        return
    try:
        if spec in ("k", "l"):
            press(spec)
        else:
            for k in _DIGITS:
                press(k)
                sleep(0.04)
    except Exception as e:
        print(f"⚠️ 装备切换按键失败 ({spec}): {e}")
