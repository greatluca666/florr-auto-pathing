import time

import afk_watch


def _reset(monkeypatch, log_path):
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(log_path))
    monkeypatch.setattr(afk_watch, "_last_offset", 0)
    monkeypatch.setattr(afk_watch, "_pause_until", 0.0)


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
