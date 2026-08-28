"""florr-auto-afk协同 —— 监听它写的latest.log, 检测到"发现AFK弹窗"事件后,
让本项目的寻路循环暂停一段时间, 别跟它的YOLO拖拽方案抢鼠标.

florr-auto-afk只在检测到弹窗那一刻写一条会落盘的日志(`log_ret`默认save=True):
    ... EVENT: Found AFK window
它清场后的"No AFK window found"硬编码save=False, 不管verbose开不开都不落盘,
没法拿来当"解除暂停"信号用 —— 这里只能是触发器, 不是起止对: 看到触发行就暂停
固定时长, 时间到自动恢复, 不去猜它到底解完没解完. 详见
docs/superpowers/specs/2026-08-11-afk-check-coexistence-design.md.

florr-auto-afk本身是完全独立的另一个程序(不是这个repo的一部分), 用户得自己
有一份能跑. ensure_florr_auto_afk_running()负责在Windows上自动确保它在跑
(已经在跑就不动, 没装就问要不要下, 装了没跑就打开它); LATEST_LOG_PATH跟着它
实际的安装位置算出来, 不再是写死的个人路径. 详见
docs/superpowers/specs/2026-08-27-afk-auto-bootstrap-design.md.
"""
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request
import zipfile

import certifi

# florr-auto-afk发行包解压后自带这个顶层目录名, 直接沿用不改名.
_INSTALL_DIR_NAME = "florr-auto-afk-v1.1.1-auto"
# 实测过release zip内部结构确认的真实可执行文件名 —— 不是"florr-auto-afk.exe"
# 这种直觉猜测的名字.
_EXE_NAME = "segment.exe"
_DOWNLOAD_URL = (
    "https://github.com/sunluca668/auto-afk/releases/download/"
    "123er4/florr-auto-afk-v1.1.1-auto.zip"
)

# 跟cdp_bridge.py的_CHROME_PROFILE_DIR同一个套路: 打包成exe后是exe自己所在
# 目录, 脚本模式下是main.py所在目录 —— 两种场景下"跟可执行文件同级"语义一致,
# 不用sys.executable(脚本模式下那是python解释器路径, 跟main.py不在同一目录).
_INSTALL_ROOT = os.path.dirname(os.path.abspath(sys.argv[0]))
_INSTALL_DIR = os.path.join(_INSTALL_ROOT, _INSTALL_DIR_NAME)
_EXE_PATH = os.path.join(_INSTALL_DIR, _EXE_NAME)

# florr-auto-afk.exe(现在自动下载安装到_INSTALL_DIR了)双击时CWD是它自己所在
# 目录, latest.log就落在这个目录下.
LATEST_LOG_PATH = os.path.join(_INSTALL_DIR, "latest.log")
# 覆盖YOLO检测+分割+拖拽执行的时间; 若在florr-auto-afk配置里关掉moveAfterAFK可以调低.
PAUSE_SECONDS = 12

_FOUND_MARKER = "EVENT: Found AFK window"

# server_lookup.py的_SSL_CONTEXT/_USER_AGENT原样复制 —— Windows上urllib默认
# 不读系统证书链, 显式传certifi的证书链才不会CERTIFICATE_VERIFY_FAILED(见
# venv-setup-deps项目memory的certifi那条). 两个都是模块私有常量, 不跨模块
# import, 沿用这个repo"平台/职责专属模块各自小段重复"的既有约定.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
_USER_AGENT = "florr-auto-pathing (github.com/greatluca666/florr-auto-pathing)"

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


def _prompt_download_confirm():
    """问用户要不要下载florr-auto-afk. 回车/任何不是'n'的输入都算同意;
    输入n(不分大小写, 允许前后空白)算跳过."""
    answer = input(
        f"\n🤖 没检测到florr-auto-afk(AFK弹窗自动处理用). 现在下载吗?\n"
        f"   来源: {_DOWNLOAD_URL}\n"
        f"   大小: 约260MB, 解压到: {_INSTALL_DIR}\n"
        f"   (回车=下载, 输入n=跳过, 之后AFK弹窗不会自动处理): "
    )
    return answer.strip().lower() != "n"


def _download_and_extract():
    """流式下载到临时文件+zipfile解压, 完了删掉临时zip. 网络失败/zip损坏都
    不抛异常出去 —— 返回False, 让调用方(ensure_florr_auto_afk_running())
    决定怎么继续, 主程序不受影响."""
    tmp_path = os.path.join(_INSTALL_ROOT, f"{_INSTALL_DIR_NAME}.zip.download")
    try:
        req = urllib.request.Request(_DOWNLOAD_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)  # 1MB一块
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        print(
                            f"\r   下载中... {downloaded / 1e6:.0f}MB / {total / 1e6:.0f}MB",
                            end="",
                        )
            print()  # 结束下载进度那行, 换行

        with zipfile.ZipFile(tmp_path) as zf:
            # 解压到_INSTALL_DIR而不是_INSTALL_ROOT: 官方release zip没有顶层
            # 目录(它的workflow用`Compress-Archive -Path ./dist/segment/*`),
            # 解压到ROOT会把4500个文件直接铺在main.py旁边, _EXE_PATH永远不存在.
            zf.extractall(_INSTALL_DIR)

        if not os.path.isfile(_EXE_PATH):
            # 上游换了打包布局(比如哪天套了层顶层目录)时不能假装装好了 —— 这里
            # 报出来, 比拖到Popen那步炸掉容易看懂.
            print(
                f"⚠️ florr-auto-afk解压完没找到{_EXE_NAME}(zip结构跟预期不符), "
                f"之后AFK弹窗不会自动处理: {_INSTALL_DIR}"
            )
            return False

        print(f"✅ florr-auto-afk已下载解压到 {_INSTALL_DIR}")
        return True
    except Exception as e:
        print(f"⚠️ 下载/解压florr-auto-afk失败(不影响主程序, 之后AFK弹窗不会自动处理): {e}")
        return False
    finally:
        if os.path.isfile(tmp_path):
            os.remove(tmp_path)


# 我们依赖的配置键 —— 只覆盖这几个, config.json里其余键(用户自己调的mouseSpeed
# 之类)原样保留. autoStart是fork里加的开关(见
# docs/superpowers/specs/2026-08-28-afk-autostart-design.md第一部分), 其余四个的
# 理由见 docs/superpowers/specs/2026-08-11-afk-check-coexistence-design.md.
_REQUIRED_CONFIG = {
    "runs": {
        "autoStart": True,              # 启动即开始检测, 不用手点"run"
        "autoTakeOverWhenIdle": False,  # 我们一直在动鼠标, 它的idle门永远不触发
        "moveAfterAFK": False,          # 它解完题的WASD乱走会跟我们的移动打架
    },
    "advanced": {
        "verbose": True,                # 保证事件真的落进latest.log
        "skipUpdate": True,             # 免得启动时联网查模型更新, 拖时间/失败
    },
}


def _write_afk_config():
    """把我们依赖的几个键写进florr-auto-afk自己的config.json, 其它键原样保留.
    文件不存在/JSON坏了就从空的开始, 写一份只含这几个键的最小config —— 它的
    get_config()对其余键有默认值兜底. 失败只打印警告返回False, 不抛 —— 配置没写上
    最多是"还得手点run", 不该拦住寻路/刷怪."""
    config_path = os.path.join(_INSTALL_DIR, "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        if not isinstance(config, dict):
            config = {}
    except Exception:
        config = {}

    for section, values in _REQUIRED_CONFIG.items():
        section_config = config.get(section)
        if not isinstance(section_config, dict):
            section_config = {}
        section_config.update(values)
        config[section] = section_config

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"⚠️ 写florr-auto-afk的config.json失败(可能还需要手动点run): {e}")
        return False


def _is_florr_auto_afk_running():
    """查segment.exe是不是已经有实例在跑(只在Windows上有意义, 调用方保证).

    tasklist找不到匹配进程时退出码照样是0, 只是往stdout印一句"INFO: No tasks
    are running which match the specified criteria." —— 不能靠returncode判断,
    只能看输出里有没有那个进程名. 查不了(tasklist不存在/输出解码失败)就当"没在
    跑", 保持加这个检测之前的行为: 顶多多开一个, 不会因为探测失败就不开.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_EXE_NAME}", "/NH"],
            capture_output=True,
            text=True,
            errors="ignore",  # 中文Windows的tasklist输出不是UTF-8, 别因为解码炸掉
        )
        return _EXE_NAME.lower() in (result.stdout or "").lower()
    except Exception:
        return False


def ensure_florr_auto_afk_running():
    """确保florr-auto-afk在跑 —— 已经在跑就什么都不做, 没装就问要不要下, 装了
    (不管刚下的还是本来就有的)就打开它. 只在Windows上做, 其余平台整个跳过
    (florr-auto-afk是Windows专属GUI程序). 全程不阻塞主流程 —— 这是可选增强,
    不是寻路/刷怪的前提, 任何一步失败/用户跳过都只打印一句提示, main.py照常
    往下走."""
    if sys.platform != "win32":
        return

    # 在跑就直接返回, 连"装没装"都不用查(能跑起来说明装过了 —— 哪怕是用户自己
    # 另一份拷贝, 也不该再开一个). 多开两个实例会各自做YOLO拖拽, 互相抢鼠标.
    if _is_florr_auto_afk_running():
        print("✅ florr-auto-afk已经在跑, 不重复打开.")
        return

    if not os.path.isfile(_EXE_PATH):
        if not _prompt_download_confirm():
            print("   跳过florr-auto-afk, 之后AFK弹窗不会自动处理.")
            return
        if not _download_and_extract():
            return  # 失败原因已经在_download_and_extract()里打印过了

    try:
        # cwd=_INSTALL_DIR是必须的, 不能让它继承我们的CWD: segment.exe内部读的是
        # 相对路径'./config.json'(get_config()), 继承我们的CWD时它一启动就
        # FileNotFoundError: './config.json'挂掉; 而且它写的latest.log也落在CWD,
        # 不指定的话LATEST_LOG_PATH指向的位置永远不会有文件, AFK检测静默失效.
        subprocess.Popen([_EXE_PATH], cwd=_INSTALL_DIR)
        print(
            "🪟 已打开florr-auto-afk, 请在它的界面里点\"run\"按钮开启AFK弹窗"
            "自动处理(不点也不影响寻路/刷怪, 只是不会自动处理AFK弹窗)."
        )
    except Exception as e:
        print(f"⚠️ 打开florr-auto-afk失败(不影响主程序): {e}")
