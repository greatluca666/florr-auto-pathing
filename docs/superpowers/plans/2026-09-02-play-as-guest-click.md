# 未登录时自动点「以游客身份游玩」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** worker 停在未登录的 florr.io 登录选择页时,自动点绿色「以游客身份游玩」按钮,进到正常标题页。

**Architecture:** 照搬现有确认类按钮那套机制 —— `utils.py` 里 `_green_button_ratio()`(固定缩放坐标处采样绿色占比)判断当前画面 + `_click_button_until_gone()`(连点两下、复查画面是否消失、没消失就重试)执行点击。新增 `on_guest_screen()` / `click_play_as_guest()` 与现有 `on_start_screen()`/`click_start_game()`、`on_death_screen()`/`click_continue_after_death()` 完全同构,接进 `main.run_worker` 主循环,排在死亡/开局菜单检测之前。

**Tech Stack:** Python 3,`pyautogui`(截图 + 点击),`numpy`(颜色掩码),`pytest`(monkeypatch)。测试用仓库自带的 `venv/bin/pytest`。

## Global Constraints

- 所有写死的屏幕坐标常量以 `_REF_WIDTH, _REF_HEIGHT = 1920, 1080` 为参照,经 `scale_point()` / `scale_x()` / `scale_y()` 换算到实际分辨率。
- 不走 CDP / DOM:florr.io 标题页按钮全是 canvas 绘制,只能像素检测 + `pyautogui` 点击。
- 不碰 Discord / Apple 登录按钮,只点「以游客身份游玩」。
- 不新增 README / GUI 文案改动(内部弹性逻辑,对用户不可见)。
- 实测值(2026-09-02 浏览器 florr.io 登出态,canvas 内部 1920×1080)写死,不等真机截图:按钮中心 `(960, 498)`,填充色 `(29, 209, 41)`(≈ 现有 `_BUTTON_GREEN_RGB=(27,203,37)`,容差 25 覆盖),按钮尺寸 ≈ 207×32 px,周围背景 `(30, 167, 97)`。
- 失败分支绝不 `sys.exit` / 抛异常打断 worker —— 点不掉就返回 `False`,交主循环下一轮重试(沿用 `_click_button_until_gone` 现有风格)。

---

### Task 1: `on_guest_screen()` 检测函数 + 常量

**Files:**
- Modify: `utils.py`(在 `_CONTINUE_BUTTON_POS`(第 324 行)与 `_green_button_ratio`(第 327 行)之间插入坐标常量;在 `on_death_screen`(第 368–381 行)之后、`click_continue_after_death`(第 384 行)之前插入采样常量 + `on_guest_screen`)
- Test: `test_utils.py`(文件末尾追加)

**Interfaces:**
- Consumes:
  - `scale_point(x, y) -> tuple`(utils.py:53,已存在)
  - `_green_button_ratio(pos, half_w=15, half_h=10) -> float`(utils.py:327,已存在;内部用 `_BUTTON_GREEN_RGB` 容差 25 掩码)
- Produces:
  - `utils._PLAY_AS_GUEST_POS: tuple`  —— `scale_point(960, 498)`
  - `utils._GUEST_SCREEN_SAMPLE_HALF_W = 60`
  - `utils._GUEST_SCREEN_SAMPLE_HALF_H = 16`
  - `utils._GUEST_SCREEN_GREEN_THRESHOLD = 0.25`
  - `utils.on_guest_screen() -> bool` —— `_green_button_ratio(_PLAY_AS_GUEST_POS, half_w=60, half_h=16) > 0.25`

- [ ] **Step 1: 写失败测试**

在 `test_utils.py` 末尾追加:

```python
# ── on_guest_screen: 未登录标题页的「以游客身份游玩」绿按钮检测 ──────────────

def _stub_guest_ratio(monkeypatch, value):
    """把 _green_button_ratio 打桩成定值, 只测 on_guest_screen 的阈值判定."""
    seen = {}

    def fake_ratio(pos, half_w=15, half_h=10):
        seen["pos"] = pos
        seen["half_w"] = half_w
        seen["half_h"] = half_h
        return value

    monkeypatch.setattr(utils, "_green_button_ratio", fake_ratio)
    return seen


def test_on_guest_screen_true_when_green_ratio_above_threshold(monkeypatch):
    seen = _stub_guest_ratio(monkeypatch, 0.5)
    assert utils.on_guest_screen() is True
    # 采样的是游客按钮坐标, 用的是宽框(不是 _green_button_ratio 的默认 15x10)
    assert seen["pos"] == utils._PLAY_AS_GUEST_POS
    assert seen["half_w"] == utils._GUEST_SCREEN_SAMPLE_HALF_W
    assert seen["half_h"] == utils._GUEST_SCREEN_SAMPLE_HALF_H


def test_on_guest_screen_false_at_exact_threshold(monkeypatch):
    _stub_guest_ratio(monkeypatch, utils._GUEST_SCREEN_GREEN_THRESHOLD)
    assert utils.on_guest_screen() is False          # 严格 >


def test_on_guest_screen_false_when_mostly_background(monkeypatch):
    _stub_guest_ratio(monkeypatch, 0.05)
    assert utils.on_guest_screen() is False


def test_play_as_guest_pos_is_scaled_from_reference(monkeypatch):
    # 参照分辨率下就是 (960, 498) 原值
    assert utils._PLAY_AS_GUEST_POS == utils.scale_point(960, 498)
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `venv/bin/pytest test_utils.py -k "guest or play_as_guest" -v`
Expected: FAIL —— `AttributeError: module 'utils' has no attribute 'on_guest_screen'`(以及 `_PLAY_AS_GUEST_POS` 等)

- [ ] **Step 3: 加常量 + 函数**

在 `utils.py` 第 324 行 `_CONTINUE_BUTTON_POS = ...` 之后、第 327 行 `def _green_button_ratio` 之前插入:

```python
# 未登录的 Chrome profile 打开 florr.io 时, 标题页先是个登录选择页 —— 绿色
# 「以游客身份游玩」按钮 + Discord/Apple 登录. 得先点掉游客按钮才到正常的
# 「开始」菜单. 这个页面是否出现取决于账号(登录过的直接跳过). 坐标/颜色:
# 2026-09-02 浏览器 florr.io 登出态实测, canvas 内部 1920x1080 下量的
# (docs/superpowers/specs/2026-09-02-play-as-guest-click-design.md).
_PLAY_AS_GUEST_POS = scale_point(960, 498)
```

在 `utils.py` 第 381 行 `return ratio > _DEATH_SCREEN_GREEN_THRESHOLD`(`on_death_screen` 函数体结束)之后、第 384 行 `def click_continue_after_death` 之前插入:

```python
# 「以游客身份游玩」按钮 ~207x32, 中间横一条白字. 跟死亡页「继续」一样, 默认
# 15x10 采样框会正好卡在白字上、测不出纯绿 —— 用宽框(半宽 60 / 半高 16)把整个
# 按钮高度 + 两侧纯绿都框进来, 白字只占中间一小块, 绿占比回到 0.4~0.65.
_GUEST_SCREEN_SAMPLE_HALF_W = 60
_GUEST_SCREEN_SAMPLE_HALF_H = 16
_GUEST_SCREEN_GREEN_THRESHOLD = 0.25


def on_guest_screen():
    """检测屏幕上是不是正显示着未登录标题页的绿色「以游客身份游玩」按钮.

    只有没登录过的 Chrome profile 才会卡在这一页(登录过的直接进正常标题页).
    正常标题页 / 游戏内 / 死亡结算页在 _PLAY_AS_GUEST_POS 处都不是这个亮绿,
    返回 False —— 不会误点. 点掉这一页之后由 on_start_screen() / click_start_game()
    接手真正进游戏.
    """
    ratio = _green_button_ratio(
        _PLAY_AS_GUEST_POS,
        half_w=_GUEST_SCREEN_SAMPLE_HALF_W,
        half_h=_GUEST_SCREEN_SAMPLE_HALF_H,
    )
    return ratio > _GUEST_SCREEN_GREEN_THRESHOLD
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `venv/bin/pytest test_utils.py -k "guest or play_as_guest" -v`
Expected: PASS(4 个用例)

- [ ] **Step 5: 跑整个 utils 测试,确认没回归**

Run: `venv/bin/pytest test_utils.py -q`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add utils.py test_utils.py
git commit -m "feat(utils): on_guest_screen — detect florr's not-logged-in login-choice page

Canvas-drawn green '以游客身份游玩' button at (960,498) in 1920x1080 ref
space, same _green_button_ratio mechanism as on_start_screen/on_death_screen
but with a wider sample box to clear the white label text.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `click_play_as_guest()` 点击函数

**Files:**
- Modify: `utils.py`(在 `click_start_game`(第 437–440 行)之后追加)
- Test: `test_utils.py`(小改 `_patch_click`,第 258–272 行;追加 3 个用例)

**Interfaces:**
- Consumes:
  - `_click_button_until_gone(button_pos, still_showing, label) -> bool`(utils.py:419,已存在:鼠标移到 `button_pos`,连点两下,等 `_CONFIRM_CLICK_SETTLE_SECONDS`,调 `still_showing()` 复查;没离开就重试,最多 `_CONFIRM_CLICK_MAX_ATTEMPTS`(=10)次;返回 `True`=确认离开,`False`=试满还在)
  - `_PLAY_AS_GUEST_POS`、`on_guest_screen`(Task 1)
- Produces:
  - `utils.click_play_as_guest() -> bool`

- [ ] **Step 1: 小改 `_patch_click` 让它能挑复查函数**

`test_utils.py` 第 258–272 行现在是:

```python
def _patch_click(monkeypatch, on_screen_sequence):
    """on_screen_sequence: 每次复查画面时依次返回的值; 用完后保持最后一个值."""
    spy = _ClickSpy()
    monkeypatch.setattr(utils, "pyautogui", spy)
    monkeypatch.setattr(utils.time, "sleep", lambda *_a, **_kw: None)
    seq = list(on_screen_sequence)
    calls = {"n": 0}

    def fake_on_start_screen():
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(utils, "on_start_screen", fake_on_start_screen)
    return spy
```

改成(加一个默认参数,现有两个调用点不受影响):

```python
def _patch_click(monkeypatch, on_screen_sequence, screen_attr="on_start_screen"):
    """on_screen_sequence: 每次复查画面时依次返回的值; 用完后保持最后一个值.
    screen_attr: _click_button_until_gone 传进来的复查函数名 (on_start_screen /
    on_death_screen / on_guest_screen)."""
    spy = _ClickSpy()
    monkeypatch.setattr(utils, "pyautogui", spy)
    monkeypatch.setattr(utils.time, "sleep", lambda *_a, **_kw: None)
    seq = list(on_screen_sequence)
    calls = {"n": 0}

    def fake_screen_check():
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(utils, screen_attr, fake_screen_check)
    return spy
```

- [ ] **Step 2: 写失败测试**

`test_utils.py` 末尾(Task 1 追加的用例之后)追加:

```python
# ── click_play_as_guest: 点掉登录选择页 ──────────────────────────────────

def test_click_play_as_guest_stops_after_page_gone_on_first_try(monkeypatch):
    spy = _patch_click(monkeypatch, [False], screen_attr="on_guest_screen")
    assert utils.click_play_as_guest() is True
    assert spy.clicks == 2                       # 一轮 = connect click + 真点


def test_click_play_as_guest_retries_until_page_gone(monkeypatch):
    spy = _patch_click(monkeypatch, [True, True, False], screen_attr="on_guest_screen")
    assert utils.click_play_as_guest() is True
    assert spy.clicks == 2 * 3


def test_click_play_as_guest_gives_up_after_max_attempts_without_crashing(monkeypatch):
    spy = _patch_click(monkeypatch, [True], screen_attr="on_guest_screen")
    assert utils.click_play_as_guest() is False
    assert spy.clicks == 2 * utils._CONFIRM_CLICK_MAX_ATTEMPTS
```

- [ ] **Step 3: 跑测试,确认失败**

Run: `venv/bin/pytest test_utils.py -k "click_play_as_guest" -v`
Expected: FAIL —— `AttributeError: module 'utils' has no attribute 'click_play_as_guest'`

- [ ] **Step 4: 加函数**

`utils.py` 第 440 行 `return _click_button_until_gone(_START_BUTTON_POS, on_start_screen, "开始")`(`click_start_game` 函数体结束)之后追加:

```python


def click_play_as_guest():
    """点「以游客身份游玩」, 让 florr 从未登录的登录选择页进到正常标题页.
    连点两下 + 复查 on_guest_screen() 确认这一页消失, 没消失就重试(理由见
    _click_button_until_gone 上面的注释). 之后由 on_start_screen() /
    click_start_game() 接手真正进游戏.
    """
    return _click_button_until_gone(_PLAY_AS_GUEST_POS, on_guest_screen, "以游客身份游玩")
```

- [ ] **Step 5: 跑测试,确认通过**

Run: `venv/bin/pytest test_utils.py -k "click_play_as_guest" -v`
Expected: PASS(3 个用例)

- [ ] **Step 6: 跑整个 utils 测试,确认现有 click 测试没被 `_patch_click` 改动打挂**

Run: `venv/bin/pytest test_utils.py -q`
Expected: 全绿(特别是 `test_click_start_game_*` 三个)

- [ ] **Step 7: Commit**

```bash
git add utils.py test_utils.py
git commit -m "feat(utils): click_play_as_guest — dismiss the login-choice page

Reuses _click_button_until_gone (double-click + re-check + retry up to
_CONFIRM_CLICK_MAX_ATTEMPTS). Generalised the test helper _patch_click to
target any screen-check function.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: 接进 `main.run_worker`

**Files:**
- Modify: `main.py`(`run_worker`:第 704 行 `if _reassert_invert_attack() == "failed":` 之前插一段;主循环第 721 行 `if on_death_screen():` 之前插一段)
- Test: `test_main_worker.py`(文件末尾追加)

**Interfaces:**
- Consumes:
  - `utils.on_guest_screen()` / `utils.click_play_as_guest()`(Task 1、2)—— main.py 第 1 行 `from utils import *` 已把它们带进 `main` 命名空间,`main.on_guest_screen` / `main.click_play_as_guest` 可直接用、可被 monkeypatch
  - `main.overlay`(`create_overlay()` 的返回值,`run_worker` 第 698 行赋值;`_StubOverlay` 在 test 里替代)
- Produces: 无新符号,仅行为变更

- [ ] **Step 1: 写失败测试**

`test_main_worker.py` 末尾追加(复用文件里已有的 `_stub_run_worker_env`、`_StubOverlay`):

```python
# ── 未登录标题页: run_worker 自动点「以游客身份游玩」──────────────────────

def test_run_worker_clicks_play_as_guest_when_on_guest_screen(monkeypatch):
    """停在未登录登录选择页时, 启动阶段 + 第 1 轮各点一次「以游客身份游玩」."""
    _stub_run_worker_env(monkeypatch)
    monkeypatch.setattr(main.florr_settings, "ensure_invert_attack_on",
                        lambda ej, *a, **k: ("on_already", ""))
    monkeypatch.setattr(main, "on_guest_screen", lambda: True)
    clicks = []
    monkeypatch.setattr(main, "click_play_as_guest", lambda: clicks.append(1))
    # 掐在寻路 —— 启动那次 + 第 1 轮那次都已经跑过了
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
    assert len(clicks) == 2                       # 启动前 1 次 + 第 1 轮顶部 1 次


def test_run_worker_never_clicks_guest_when_not_on_guest_screen(monkeypatch):
    """登录过的 profile 的常态: on_guest_screen 恒 False, 一次都不点."""
    _stub_run_worker_env(monkeypatch)
    monkeypatch.setattr(main.florr_settings, "ensure_invert_attack_on",
                        lambda ej, *a, **k: ("on_already", ""))
    monkeypatch.setattr(main, "on_guest_screen", lambda: False)
    monkeypatch.setattr(main, "click_play_as_guest",
                        lambda: pytest.fail("不在游客页不该点「以游客身份游玩」"))
    monkeypatch.setattr(main, "lazy_theta_pathing",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})
```

同时更新 `_stub_run_worker_env`(第 280–291 行)加一行默认桩,免得其它已有 `run_worker` 测试(`test_run_worker_reasserts_invert_attack_*`、`test_run_worker_survives_invert_attack_failure`)因为新代码路径去调真的 `on_guest_screen`(会截屏):

第 289–290 行现在是:

```python
    monkeypatch.setattr(main, "on_death_screen", lambda: False)
    monkeypatch.setattr(main, "on_start_screen", lambda: False)
```

之后补一行:

```python
    monkeypatch.setattr(main, "on_guest_screen", lambda: False)
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `venv/bin/pytest test_main_worker.py -k "guest" -v`
Expected: FAIL —— `test_run_worker_clicks_play_as_guest_when_on_guest_screen` 里 `len(clicks) == 2` 断言不成立(当前 `run_worker` 根本没调 `click_play_as_guest`,`clicks` 是 `[]`)

- [ ] **Step 3: 加接入代码**

`main.py` 第 704 行现在是:

```python
    if _reassert_invert_attack() == "failed":
        overlay.update(message="⚠️ 反转攻击键未确认, 见日志")
```

在它**之前**插入:

```python
    # 没登录过的 Chrome profile 停在 florr 的登录选择页(绿色「以游客身份游玩」
    # + Discord/Apple). 先点掉它, 让 florr 开始加载游戏 —— 否则下面的
    # _reassert_invert_attack() 走 CDP 读 window.Module 时 WASM 还没就绪, 白报
    # 一次 failed. 登录过的号 on_guest_screen() 恒 False, 这段是 no-op.
    if on_guest_screen():
        print("👤 未登录标题页, 先点『以游客身份游玩』进正常标题页...")
        click_play_as_guest()
        time.sleep(2)
```

`main.py` 主循环第 721 行现在是:

```python
        if on_death_screen():
            print("💀 检测到死亡结算画面, 点击继续...")
            overlay.update(state="重新开始", message="死亡, 点击继续...")
            click_continue_after_death()
            time.sleep(2)
```

在它**之前**(`round_start_time`/`print(第 X 轮)` 之后、`if on_death_screen():` 之前)插入:

```python
        if on_guest_screen():
            print("👤 检测到未登录标题页, 点击『以游客身份游玩』...")
            overlay.update(state="重新开始", message="点击游客登录...")
            click_play_as_guest()
            time.sleep(2)
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `venv/bin/pytest test_main_worker.py -k "guest" -v`
Expected: PASS(2 个用例)

- [ ] **Step 5: 跑整个 worker 测试,确认没回归**

Run: `venv/bin/pytest test_main_worker.py -q`
Expected: 全绿(尤其 `test_run_worker_reasserts_invert_attack_at_startup_and_each_round` —— 它断言 `ensure_invert_attack_on` 恰好 2 次;新代码在它前面加的是 `on_guest_screen`/`click_play_as_guest`,不碰 invert-attack 计数)

- [ ] **Step 6: 跑全套测试**

Run: `venv/bin/pytest -q`
Expected: 全绿

- [ ] **Step 7: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "feat(main): run_worker auto-clicks 'play as guest' on not-logged-in profiles

on_guest_screen()/click_play_as_guest() run at worker startup (before the
invert-attack CDP probe) and at the top of each round (before the
death/start-screen checks). No-op on logged-in profiles.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**

| Spec 要点 | 对应任务 |
|---|---|
| `on_guest_screen()` —— `_green_button_ratio` + 宽采样框 + 阈值 | Task 1 |
| `_PLAY_AS_GUEST_POS = scale_point(960, 498)`、`_GUEST_SCREEN_SAMPLE_HALF_W/H`、`_GUEST_SCREEN_GREEN_THRESHOLD` | Task 1 |
| `click_play_as_guest()` —— 复用 `_click_button_until_gone` | Task 2 |
| main.py 接入①:主循环每轮顶部、death/start 之前 | Task 3 Step 3 |
| main.py 接入②:进循环前、`_reassert_invert_attack()` 之前 | Task 3 Step 3 |
| 登录过的 profile `on_guest_screen()` 恒 False、零副作用 | Task 3 `test_run_worker_never_clicks_guest_when_not_on_guest_screen` |
| `test_utils.py` 扩:`on_guest_screen` 阈值边界 + `click_play_as_guest` 三用例 | Task 1 Step 1、Task 2 Step 2 |
| `test_main_worker.py` 扩:启动调用一次 / 恒 False 不调 | Task 3 Step 1 |
| 失败不打断 worker | `_click_button_until_gone` 现有行为(返回 `False`),Task 2 `test_..._gives_up_...without_crashing` 覆盖 |

无缺口。

**2. Placeholder scan:** 无 TBD/TODO;每个代码步都有完整代码块;失败测试、实现、命令、预期输出都写全了。

**3. Type consistency:**
- `on_guest_screen` / `click_play_as_guest`:Task 1、2 定义,Task 3 消费,名字一致。
- `_PLAY_AS_GUEST_POS`(Task 1 产出)在 Task 2 `click_play_as_guest` 里引用,一致。
- `_GUEST_SCREEN_SAMPLE_HALF_W` / `_HALF_H` / `_GUEST_SCREEN_GREEN_THRESHOLD`:Task 1 定义并在同任务测试里按此名断言,一致。
- `_patch_click(monkeypatch, on_screen_sequence, screen_attr="on_start_screen")`:Task 2 Step 1 改签名,Step 2 用 `screen_attr="on_guest_screen"` 调用,一致;现有两个调用点(`test_click_start_game_*`)不传第三参,默认值保持旧行为。
- `_click_button_until_gone(button_pos, still_showing, label)`:三处调用(`click_start_game`、`click_continue_after_death`、新 `click_play_as_guest`)签名一致。

无不一致。

**4. 分辨率局限(已知,不在本 plan 解决):** `scale_point` 按宽/高独立轴缩放,水平居中的按钮在非 16:9 屏上 x 会偏 —— 跟现有 `_START_BUTTON_POS` / `_CONTINUE_BUTTON_POS` 同款局限,spec Non-goals 已列。
