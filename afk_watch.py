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
# 还没做过第一次真实读取 —— 用来区分"模块刚加载, 从没poll过"和"poll过, offset
# 合法为0"这两种情况, 好让下面的截断重置逻辑(size < _last_offset)只在后一种
# 情况下生效. 参见_read_new_lines()里的用法.
_initialized = False
# 日志读取失败(路径没配对/文件不存在/是目录/权限不够)只在第一次发生时打印一次
# 警告, 别每次poll(一秒好几次)都刷屏.
_warned_unreadable = False


def _read_new_lines():
    """读取上次读到的位置之后新增的行. 文件比上次记录的offset还小(轮转/程序
    重启)就当成新文件, 从头重读.

    模块刚加载、还从没poll过的第一次调用是特例: 不从文件开头读, 直接跳到当前
    文件末尾. florr-auto-afk可能已经跑了一段时间, 从0读会把老早以前就处理完的
    "Found AFK window"历史事件当成新事件, 触发一次没必要的暂停. 代价是main.py
    启动前几毫秒内刚好写的标记可能被跳过, 比回放整段历史划得来.
    """
    global _last_offset, _initialized, _warned_unreadable
    try:
        size = os.path.getsize(LATEST_LOG_PATH)
        if not _initialized:
            _last_offset = size
            _initialized = True
        elif size < _last_offset:
            _last_offset = 0
        with open(LATEST_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(_last_offset)
            lines = f.readlines()
            _last_offset = f.tell()
            if lines and not lines[-1].endswith("\n"):
                # 最后一行是对方进程正在写、还没写完的残行(我们刚好在它两次write
                # 之间poll到了). 扔掉它, offset退回到这行开头 —— 不然下次它写完
                # 剩下的部分时, 两半永远拼不到一起, 里面的标记行就永久漏检了.
                partial = lines.pop()
                _last_offset -= len(partial.encode("utf-8"))
        return lines
    except Exception:
        if not _warned_unreadable:
            _warned_unreadable = True
            print(
                f"⚠️ 读取florr-auto-afk日志失败(LATEST_LOG_PATH配置可能有误): {LATEST_LOG_PATH}"
            )
        return []


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
