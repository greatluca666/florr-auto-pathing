"""florr-auto-afk协同 —— 监听它写的latest.log, 检测到"发现AFK弹窗"事件后,
让本项目的寻路循环暂停一段时间, 别跟它的YOLO拖拽方案抢鼠标.

florr-auto-afk只在检测到弹窗那一刻写一条会落盘的日志(`log_ret`默认save=True):
    ... EVENT: Found AFK window
它清场后的"No AFK window found"硬编码save=False, 不管verbose开不开都不落盘,
没法拿来当"解除暂停"信号用 —— 这里只能是触发器, 不是起止对: 看到触发行就暂停
固定时长, 时间到自动恢复, 不去猜它到底解完没解完. 详见
docs/superpowers/specs/2026-08-11-afk-check-coexistence-design.md.
"""
import os
import time

# 部署时改成florr-auto-afk实际launch目录下的latest.log绝对路径(VM里那个程序的工作目录).
LATEST_LOG_PATH = "./latest.log"
# 覆盖YOLO检测+分割+拖拽执行的时间; 若在florr-auto-afk配置里关掉moveAfterAFK可以调低.
PAUSE_SECONDS = 12

_FOUND_MARKER = "EVENT: Found AFK window"

_last_offset = 0
_pause_until = 0.0


def _read_new_lines():
    """读取上次读到的位置之后新增的行. 文件比上次记录的offset还小(轮转/程序
    重启)就当成新文件, 从头重读."""
    global _last_offset
    try:
        size = os.path.getsize(LATEST_LOG_PATH)
    except OSError:
        return []
    if size < _last_offset:
        _last_offset = 0
    with open(LATEST_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(_last_offset)
        lines = f.readlines()
        _last_offset = f.tell()
    return lines


def poll_afk_pause():
    """轮询一次. 发现新的"Found AFK window"事件就开一段暂停窗口; 返回当前是否
    还在暂停中. 日志文件不存在(florr-auto-afk还没启动, 或者LATEST_LOG_PATH没配对)
    时永远返回False, 不抛异常 —— 这个探测器绝不能把主程序带崩.
    """
    global _pause_until
    for line in _read_new_lines():
        if _FOUND_MARKER in line:
            _pause_until = time.time() + PAUSE_SECONDS
            print(f"⏸️  检测到florr-auto-afk发现AFK弹窗, 暂停操作{PAUSE_SECONDS}秒...")
            break
    return time.time() < _pause_until
