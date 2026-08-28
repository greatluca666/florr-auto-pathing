import io
import os
import time
import zipfile
from unittest.mock import MagicMock, patch

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


def test_prompt_download_confirm_returns_true_on_enter(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert afk_watch._prompt_download_confirm() is True


def test_prompt_download_confirm_returns_false_on_n(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert afk_watch._prompt_download_confirm() is False


def test_prompt_download_confirm_returns_false_on_n_case_insensitive_with_whitespace(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "  N  ")
    assert afk_watch._prompt_download_confirm() is False


def test_prompt_download_confirm_prints_url_size_and_destination(monkeypatch):
    # input()的提示文本是作为参数传给input(...)的, 不是print()出来的 —— capsys
    # 抓不到. 换个把prompt参数记下来的假input, 直接断言参数内容.
    captured_prompt = {}

    def fake_input(prompt):
        captured_prompt["text"] = prompt
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    afk_watch._prompt_download_confirm()
    assert afk_watch._DOWNLOAD_URL in captured_prompt["text"]
    assert afk_watch._INSTALL_DIR in captured_prompt["text"]


def _fake_zip_bytes():
    """造一个真实的、内存里的zip文件内容, 里面有一个占位文件 —— 用来让
    zipfile.ZipFile(真实的模块, 不mock)在测试里真的能解压出东西, 断言解压
    后的文件确实落在了预期目录, 而不是只断言"函数被调用过"这种空心测试."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("florr-auto-afk-v1.1.1-auto/segment.exe", b"fake exe content")
    return buf.getvalue()


def _fake_response(body):
    """假的urlopen返回值: 支持with语句, 第一次read()给全部内容, 第二次给空
    表示读完."""
    resp = MagicMock()
    resp.headers = {"Content-Length": str(len(body))}
    resp.read.side_effect = [body, b""]
    resp.__enter__ = lambda self: resp
    resp.__exit__ = lambda self, *a: False
    return resp


def test_download_and_extract_success(tmp_path, monkeypatch):
    monkeypatch.setattr(afk_watch, "_INSTALL_ROOT", str(tmp_path))
    monkeypatch.setattr(
        afk_watch, "_INSTALL_DIR", str(tmp_path / "florr-auto-afk-v1.1.1-auto")
    )

    with patch(
        "afk_watch.urllib.request.urlopen", return_value=_fake_response(_fake_zip_bytes())
    ):
        result = afk_watch._download_and_extract()

    assert result is True
    extracted_exe = tmp_path / "florr-auto-afk-v1.1.1-auto" / "segment.exe"
    assert extracted_exe.read_bytes() == b"fake exe content"
    # 临时zip用完就删, 不该留在目标目录里.
    assert not (tmp_path / "florr-auto-afk-v1.1.1-auto.zip.download").exists()


def test_download_and_extract_returns_false_and_cleans_up_on_network_error(tmp_path, monkeypatch):
    monkeypatch.setattr(afk_watch, "_INSTALL_ROOT", str(tmp_path))
    monkeypatch.setattr(
        afk_watch, "_INSTALL_DIR", str(tmp_path / "florr-auto-afk-v1.1.1-auto")
    )

    with patch("afk_watch.urllib.request.urlopen", side_effect=OSError("network unreachable")):
        result = afk_watch._download_and_extract()

    assert result is False
    assert not (tmp_path / "florr-auto-afk-v1.1.1-auto.zip.download").exists()


def test_download_and_extract_returns_false_on_corrupt_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(afk_watch, "_INSTALL_ROOT", str(tmp_path))
    monkeypatch.setattr(
        afk_watch, "_INSTALL_DIR", str(tmp_path / "florr-auto-afk-v1.1.1-auto")
    )

    with patch(
        "afk_watch.urllib.request.urlopen",
        return_value=_fake_response(b"this is not a zip file"),
    ):
        result = afk_watch._download_and_extract()

    assert result is False
    assert not (tmp_path / "florr-auto-afk-v1.1.1-auto.zip.download").exists()


def test_ensure_florr_auto_afk_running_skips_entirely_on_non_windows(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "darwin")
    with patch("builtins.input") as mock_input, \
         patch("afk_watch.subprocess.Popen") as mock_popen, \
         patch("afk_watch.urllib.request.urlopen") as mock_urlopen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_input.assert_not_called()
    mock_popen.assert_not_called()
    mock_urlopen.assert_not_called()


def test_ensure_florr_auto_afk_running_opens_directly_when_already_installed(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=True), \
         patch("afk_watch._is_florr_auto_afk_running", return_value=False), \
         patch("builtins.input") as mock_input, \
         patch("afk_watch.subprocess.Popen") as mock_popen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_input.assert_not_called()  # 已经装了, 不该问下载
    # cwd必须是它自己的安装目录 —— segment.exe内部读的是相对路径'./config.json',
    # 它写的latest.log也落在CWD; 继承我们的CWD会让它一启动就FileNotFoundError.
    mock_popen.assert_called_once_with([afk_watch._EXE_PATH], cwd=afk_watch._INSTALL_DIR)


def test_ensure_florr_auto_afk_running_skips_when_user_declines_download(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=False), \
         patch("afk_watch._is_florr_auto_afk_running", return_value=False), \
         patch("afk_watch._prompt_download_confirm", return_value=False), \
         patch("afk_watch._download_and_extract") as mock_download, \
         patch("afk_watch.subprocess.Popen") as mock_popen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_download.assert_not_called()
    mock_popen.assert_not_called()


def test_ensure_florr_auto_afk_running_downloads_then_opens_when_confirmed(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=False), \
         patch("afk_watch._is_florr_auto_afk_running", return_value=False), \
         patch("afk_watch._prompt_download_confirm", return_value=True), \
         patch("afk_watch._download_and_extract", return_value=True) as mock_download, \
         patch("afk_watch.subprocess.Popen") as mock_popen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_download.assert_called_once()
    mock_popen.assert_called_once_with([afk_watch._EXE_PATH], cwd=afk_watch._INSTALL_DIR)


def test_ensure_florr_auto_afk_running_does_not_open_when_download_fails(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=False), \
         patch("afk_watch._is_florr_auto_afk_running", return_value=False), \
         patch("afk_watch._prompt_download_confirm", return_value=True), \
         patch("afk_watch._download_and_extract", return_value=False), \
         patch("afk_watch.subprocess.Popen") as mock_popen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_popen.assert_not_called()


def test_ensure_florr_auto_afk_running_does_not_crash_when_popen_raises(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=True), \
         patch("afk_watch._is_florr_auto_afk_running", return_value=False), \
         patch("afk_watch.subprocess.Popen", side_effect=OSError("no permission")):
        afk_watch.ensure_florr_auto_afk_running()  # 不该抛异常


def _fake_tasklist_result(stdout):
    result = MagicMock()
    result.stdout = stdout
    return result


def test_is_florr_auto_afk_running_true_when_tasklist_lists_the_exe():
    listed = (
        "segment.exe                   9128 Console                    1    271,204 K\n"
    )
    with patch("afk_watch.subprocess.run", return_value=_fake_tasklist_result(listed)):
        assert afk_watch._is_florr_auto_afk_running() is True


def test_is_florr_auto_afk_running_false_when_tasklist_reports_no_tasks():
    # tasklist找不到匹配进程时退出码照样是0, 只是往stdout印这句 —— 不能靠
    # returncode判断, 只能看输出里有没有那个进程名.
    no_tasks = 'INFO: No tasks are running which match the specified criteria.\n'
    with patch("afk_watch.subprocess.run", return_value=_fake_tasklist_result(no_tasks)):
        assert afk_watch._is_florr_auto_afk_running() is False


def test_is_florr_auto_afk_running_false_when_tasklist_unavailable():
    with patch("afk_watch.subprocess.run", side_effect=OSError("tasklist not found")):
        assert afk_watch._is_florr_auto_afk_running() is False


def test_ensure_florr_auto_afk_running_does_not_open_second_instance_when_already_running(
    monkeypatch, capsys
):
    # 用户报的bug: 不检测就无条件Popen —— florr-auto-afk已经在跑时会开出第二个
    # 实例, 两个都在做YOLO拖拽, 互相抢鼠标.
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch._is_florr_auto_afk_running", return_value=True), \
         patch("builtins.input") as mock_input, \
         patch("afk_watch.subprocess.Popen") as mock_popen, \
         patch("afk_watch._download_and_extract") as mock_download:
        afk_watch.ensure_florr_auto_afk_running()
    mock_popen.assert_not_called()
    mock_input.assert_not_called()    # 在跑就说明装过了, 更不该问下载
    mock_download.assert_not_called()
    assert "已经在跑" in capsys.readouterr().out
