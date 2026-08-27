import os
import time

import afk_watch


def _reset(monkeypatch, log_path):
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(log_path))
    monkeypatch.setattr(afk_watch, "_last_offset", 0)
    monkeypatch.setattr(afk_watch, "_pause_until", 0.0)
    # 默认模拟"之前已经poll过, offset合法为0"这种常规状态(不是模块刚加载的
    # 首次poll) —— 首次poll跳到文件末尾的特殊逻辑单独用测试覆盖, 见下面
    # test_poll_afk_pause_ignores_preexisting_history_on_first_ever_poll.
    monkeypatch.setattr(afk_watch, "_initialized", True)
    monkeypatch.setattr(afk_watch, "_warned_unreadable", False)


def test_poll_afk_pause_false_when_log_file_missing(tmp_path, monkeypatch):
    _reset(monkeypatch, tmp_path / "does_not_exist.log")
    assert afk_watch.poll_afk_pause() is False


def test_poll_afk_pause_ignores_unrelated_lines(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:1> <afk_thread()> EVENT: something else\n"
    )
    _reset(monkeypatch, log_path)
    assert afk_watch.poll_afk_pause() is False


def test_poll_afk_pause_true_after_marker_line_written(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:127> <afk_thread()> EVENT: Found AFK window\n"
    )
    _reset(monkeypatch, log_path)
    assert afk_watch.poll_afk_pause() is True


def test_poll_afk_pause_expires_after_pause_seconds(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:127> <afk_thread()> EVENT: Found AFK window\n"
    )
    _reset(monkeypatch, log_path)
    monkeypatch.setattr(afk_watch, "PAUSE_SECONDS", 0.05)
    assert afk_watch.poll_afk_pause() is True
    time.sleep(0.1)
    assert afk_watch.poll_afk_pause() is False


def test_poll_afk_pause_does_not_retrigger_from_already_read_line(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:127> <afk_thread()> EVENT: Found AFK window\n"
    )
    _reset(monkeypatch, log_path)
    assert afk_watch.poll_afk_pause() is True
    # 暂停窗口手动过期(模拟时间流逝), 日志文件没有新行 —— 不该重新触发.
    monkeypatch.setattr(afk_watch, "_pause_until", 0.0)
    assert afk_watch.poll_afk_pause() is False


def test_poll_afk_pause_rereads_from_start_after_truncation(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:1> <afk_thread()> EVENT: " + ("x" * 200) + "\n"
    )
    _reset(monkeypatch, log_path)
    assert afk_watch.poll_afk_pause() is False
    # 模拟florr-auto-afk重启/日志轮转: 文件被换成更短的新内容.
    log_path.write_text(
        "[2026-08-11 00:00:01] <segment.py:127> <afk_thread()> EVENT: Found AFK window\n"
    )
    assert afk_watch.poll_afk_pause() is True


def test_poll_afk_pause_false_when_path_is_directory(tmp_path, monkeypatch):
    # 模拟LATEST_LOG_PATH指向目录而非文件(权限错误或误配置).
    # open()会抛IsADirectoryError, 应被捕获且返回False.
    _reset(monkeypatch, tmp_path)
    assert afk_watch.poll_afk_pause() is False


def test_poll_afk_pause_detects_marker_appended_after_a_poll(tmp_path, monkeypatch):
    # 复现真实运行方式: 先poll一次(什么都没有), 文件被外部程序追加内容,
    # 再poll一次才检测到 —— 不是像其它测试那样在第一次poll前就把完整内容写好.
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:1> <afk_thread()> EVENT: something else\n"
    )
    _reset(monkeypatch, log_path)
    assert afk_watch.poll_afk_pause() is False
    with open(log_path, "a") as f:
        f.write(
            "[2026-08-11 00:00:01] <segment.py:127> <afk_thread()> EVENT: Found AFK window\n"
        )
    assert afk_watch.poll_afk_pause() is True


def test_poll_afk_pause_detects_marker_split_across_two_writes(tmp_path, monkeypatch):
    # 模拟florr-auto-afk的write()调用被我们的poll撞到一半: 第一次poll时文件里
    # 只有这一行的前半部分(还没写完, 没有结尾换行符). 没有finding 1的修复,
    # _last_offset会越过这半行, 后半部分写完后也永远拼不回去, 标记就漏检了.
    log_path = tmp_path / "latest.log"
    prefix = "[2026-08-11 00:00:00] <segment.py:127> <afk_thread()> EVENT: Found AFK "
    log_path.write_text(prefix)
    _reset(monkeypatch, log_path)
    assert afk_watch.poll_afk_pause() is False
    with open(log_path, "a") as f:
        f.write("window\n")
    assert afk_watch.poll_afk_pause() is True


def test_poll_afk_pause_ignores_preexisting_history_on_first_ever_poll(tmp_path, monkeypatch):
    # 模拟main.py启动前florr-auto-afk已经跑了一段时间, 日志里躺着一条老早以前
    # 就处理完的标记行. 模块从没poll过时的第一次调用不该把这段历史当成新事件,
    # 得直接跳到文件末尾. 之后真的新写一条, 才应该触发.
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:127> <afk_thread()> EVENT: Found AFK window\n"
    )
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(log_path))
    monkeypatch.setattr(afk_watch, "_last_offset", 0)
    monkeypatch.setattr(afk_watch, "_pause_until", 0.0)
    monkeypatch.setattr(afk_watch, "_initialized", False)
    monkeypatch.setattr(afk_watch, "_warned_unreadable", False)

    assert afk_watch.poll_afk_pause() is False

    with open(log_path, "a") as f:
        f.write(
            "[2026-08-11 00:00:01] <segment.py:127> <afk_thread()> EVENT: Found AFK window\n"
        )
    assert afk_watch.poll_afk_pause() is True


def test_poll_afk_pause_warns_once_when_log_unreadable(tmp_path, monkeypatch, capsys):
    _reset(monkeypatch, tmp_path / "does_not_exist.log")
    assert afk_watch.poll_afk_pause() is False
    assert afk_watch.poll_afk_pause() is False
    assert afk_watch.poll_afk_pause() is False
    captured = capsys.readouterr()
    assert captured.out.count("⚠️") == 1


def test_latest_log_path_is_computed_from_install_dir():
    assert afk_watch.LATEST_LOG_PATH == os.path.join(afk_watch._INSTALL_DIR, "latest.log")


def test_install_dir_is_named_after_the_release_folder():
    assert afk_watch._INSTALL_DIR == os.path.join(
        afk_watch._INSTALL_ROOT, "florr-auto-afk-v1.1.1-auto"
    )


def test_exe_path_uses_the_real_executable_name_not_florr_auto_afk_exe():
    # 实测过release zip内部结构确认的真实文件名 —— 不是"florr-auto-afk.exe"
    # 这种直觉猜测的名字, 写死一个测试防止以后被改错.
    assert afk_watch._EXE_PATH == os.path.join(afk_watch._INSTALL_DIR, "segment.exe")
