# 未登录时自动点「以游客身份游玩」— design

## Problem

调度器按时块切 florr 账号,每个账号一个 Chrome profile(`chrome-profiles/<别名>/`)。**没登录过的 profile** 打开 florr.io 时,标题页显示的不是正常的绿色「开始」菜单,而是一个前置的登录选择页:

```
                florr.io
        ▶  以游客身份游玩          ← 绿色, 必须先点这个
           Discord登录            ← 深色
           Apple登录              ← 深色
   如果在别的设备上已有一个游戏账号, 登录会关联你的账号并保存你的游戏进度。
```

点了「以游客身份游玩」之后 florr 才进入正常标题页(教程弹窗 + 绿色「开始」按钮),现有 `on_start_screen()` / `click_start_game()` 从这里接手。

现状:worker 主循环只认「死亡结算画面」和「开局菜单」两个状态。落在这个游客选择页时,`on_death_screen()` 和 `on_start_screen()` 都返回 False → `lazy_theta_pathing()` 对着标题页立刻失败 → `round_elapsed` 极小 → 连续短局 → 误触发换服务器,永远进不去。

**这个页面是否出现取决于账号:** 用户确认「有些账号有,有些没有」。登录过的 profile 直接到正常标题页,不经过这一步。所以检测逻辑必须在游客页不存在时是干净的 no-op。

## Goal

worker 识别出「当前停在游客选择页」时,点一次绿色「以游客身份游玩」按钮,让 florr 进到正常标题页,后续交给现有的 `on_start_screen()` / `click_start_game()`。

沿用现有确认类按钮那套完全一样的机制:固定缩放坐标 + `_green_button_ratio()` 颜色占比检测 + `_click_button_until_gone()` 连点复查。

## Non-goals

- **不走 CDP / DOM。** 浏览器实测确认(2026-09-02,登出状态):整个 florr.io 页面只有一个 `<canvas>` + 一个 `#loading` span,「以游客身份游玩」「Discord登录」「Apple登录」全是 canvas 画的,`document.querySelector` 查不到任何按钮元素。只能像素检测 + `pyautogui` 点击,跟 `click_start_game` / `click_continue_after_death` 同一条路。[[switch-server-cdp-not-clicks]] 记的是「canvas 控件不吃合成点击、要走 CDP」——那是对 *游戏内* 控件(换服下拉框)而言;标题页这几个按钮 `pyautogui.click()` 实测有效(浏览器里点了就进了下一步),跟 start / continue 按钮一样。
- **不碰 Discord / Apple 登录。** 只点「以游客身份游玩」。真正的账号登录是用户在登录引导里手动做的一次性操作,不是 bot 的事。
- **不在 GUI 登录引导 / 调度器启动路径里单独加。** worker 主循环每轮本来就跑 `on_start_screen()`,在它前面加一个 `on_guest_screen()` 检测就够——两条启动路径最后都是拉起 worker,第 1 轮立刻命中,天然覆盖「启动时」。
- **不做「已经登录了还去点游客」的保护逻辑。** `on_guest_screen()` 在正常标题页 / 游戏内 / 死亡页都返回 False(那些位置 `(960,498)` 处不是那个特定的亮绿),不会误点。
- **暂不用真机截图校准坐标。** 用户选择先用浏览器实测值写死(现有 `_START_BUTTON_POS` / `_CONTINUE_BUTTON_POS` 也是这么硬编码的)。florr 改标题页布局或实测值偏了,再照 death-screen 常量那样调。

## 实测数据(2026-09-02,浏览器,florr.io 登出,视口 1920×1080)

canvas 内部分辨率 = 视口尺寸(`devicePixelRatio = 1`)。扫描 canvas 像素、匹配 florr 确认绿(容差 ±22/±22/±28 around `(29,209,41)`):

| 项 | 值 |
|---|---|
| 按钮包围盒 | x `856 → 1063`,y `482 → 514` |
| 按钮中心 | **(959.5, 498)** ≈ `(960, 498)` |
| 中心占比 | cx/W = 0.4997(水平正居中),cy/H = 0.4611 |
| 按钮尺寸 | ≈ 207 × 32 px(占屏 0.108 W × 0.030 H) |
| 按钮填充色 | `(29, 209, 41)` — florr 标准确认绿,现有 `_BUTTON_GREEN_RGB=(27,203,37)` 容差 25 直接覆盖 |
| 按钮上下描边/斜面 | `(23,169,33)`、`(26,189,37)` |
| 周围背景色 | `(30, 167, 97)` — 更暗、蓝通道 97(按钮是 41),`_green_button_ratio` 容差 25 下不匹配 |
| 按钮上白色文字 | 中间约 120 × 14 px 一片 `(255,255,255)`,占按钮面积不小 |

**采样框要用宽的。** 按钮只有 32px 高,中间横一条白字。`_green_button_ratio()` 默认半径 15×10 的框会正好卡在白字带上,纯绿占比被挤到很低——`on_death_screen` 踩过一样的坑(见 [utils.py](../../../utils.py) `_DEATH_SCREEN_*` 注释)。用半宽 60 / 半高 16 的框(x 900→1020,y 482→514),把整个按钮高度 + 两侧纯绿都框进来,白字只占中间一小块,绿占比回到 0.4~0.65。

**跟 `_START_BUTTON_POS` 不冲突。** `_START_BUTTON_POS = scale_point(1059, 527)`。1920×1080 下即 `(1059, 527)`,默认采样框 y 517→537,整个落在游客按钮下边缘(y=514)之下 = 背景绿 `(30,167,97)` → `_green_button_ratio` 判 0 → `on_start_screen()` 在游客页返回 False。两个检测互不干扰,顺序上先查游客页、点掉、再进正常菜单检测。

**点击后行为(实测):** 点「以游客身份游玩」→ florr 弹教程提示「只需点击绿色的[开始]按钮即可进入游戏」+ 进入正常标题页(`连接中……` → 绿色「开始」按钮)。即交回 `on_start_screen()` / `click_start_game()` 的地盘。

## `utils.py` 新增

紧挨现有 `_CONTINUE_BUTTON_POS` / `on_death_screen` 那一段:

```python
# 未登录的 profile 打开 florr.io 时, 标题页先是个登录选择页 —— 绿色「以游客身份
# 游玩」按钮 + Discord/Apple 登录. 得先点掉游客按钮才到正常的「开始」菜单. 这个
# 页面是否出现取决于账号(登录过的直接跳过). 坐标/颜色: 2026-09-02 浏览器
# florr.io 登出态实测, canvas 内部 1920x1080 下量的(见 spec).
_PLAY_AS_GUEST_POS = scale_point(960, 498)

# 按钮 ~207x32, 中间横一条白字. 跟死亡页「继续」一样, 默认 15x10 采样框会卡在
# 白字上测不出纯绿 —— 用宽框(半宽 60 / 半高 16)把整个按钮高度 + 两侧纯绿框进来.
_GUEST_SCREEN_SAMPLE_HALF_W = 60
_GUEST_SCREEN_SAMPLE_HALF_H = 16
_GUEST_SCREEN_GREEN_THRESHOLD = 0.25


def on_guest_screen():
    """检测屏幕上是不是正显示着未登录标题页的绿色「以游客身份游玩」按钮.

    只有没登录过的 Chrome profile 才会卡在这一页(登录过的直接进正常标题页).
    正常标题页 / 游戏内 / 死亡结算页在 _PLAY_AS_GUEST_POS 处都不是这个亮绿,
    返回 False —— 不会误点.
    """
    ratio = _green_button_ratio(
        _PLAY_AS_GUEST_POS,
        half_w=_GUEST_SCREEN_SAMPLE_HALF_W,
        half_h=_GUEST_SCREEN_SAMPLE_HALF_H,
    )
    return ratio > _GUEST_SCREEN_GREEN_THRESHOLD


def click_play_as_guest():
    """点「以游客身份游玩」, 让 florr 从登录选择页进到正常标题页. 连点两下 +
    复查 on_guest_screen() 确认这一页消失, 没消失就重试(理由见
    _click_button_until_gone 上面的注释). 之后由 on_start_screen() /
    click_start_game() 接手真正进游戏.
    """
    return _click_button_until_gone(_PLAY_AS_GUEST_POS, on_guest_screen, "以游客身份游玩")
```

`_green_button_ratio` / `_click_button_until_gone` / `scale_point` 都是 utils 里现成的,不改。

## `main.py` 接入

`run_worker` 里两处,都在现有 `on_death_screen()` 检测**之前**:

### 1. 主循环每轮顶部(main.py 现在 ~721 行 `if on_death_screen():` 之前)

```python
if on_guest_screen():
    print("👤 检测到未登录标题页, 点击『以游客身份游玩』...")
    overlay.update(state="重新开始", message="点击游客登录...")
    click_play_as_guest()
    time.sleep(2)
```

### 2. 进主循环前一次(main.py ~704 行 `_reassert_invert_attack()` 之前)

```python
if on_guest_screen():
    print("👤 未登录标题页, 先点『以游客身份游玩』进正常标题页...")
    click_play_as_guest()
    time.sleep(2)
```

理由:`_reassert_invert_attack()`(pre-loop 那次)走 CDP 读 florr 的 `window.Module` WASM 内存。停在游客页时 florr 游戏模块可能还没加载,`ensure_invert_attack_on` 返回 `failed` → 白警告一次。先把游客页点掉,让 florr 开始加载游戏,pre-loop 的 invert-attack 探测才有意义。这次是 warn-only 逻辑的前置清理,3 行。

`on_guest_screen` / `click_play_as_guest` 通过 `from utils import *`(main.py 第 1 行)已经在作用域里,不用额外 import。

登录过的 profile 上 `on_guest_screen()` 恒为 False,两处接入都是「每轮一次小区域 `pyautogui.screenshot`」的成本,无副作用。

## 测试

### `test_utils.py` 扩

`on_guest_screen` —— monkeypatch `utils._green_button_ratio` 返回定值,验证阈值边界:

- `_green_button_ratio` 返回 `0.5` → `on_guest_screen()` is True
- 返回 `0.25` → False(严格 `>`)
- 返回 `0.05` → False
- (可选)断言传给 `_green_button_ratio` 的 `half_w/half_h` 是 `_GUEST_SCREEN_SAMPLE_HALF_W/H`,pos 是 `_PLAY_AS_GUEST_POS`

`click_play_as_guest` —— 复用现有 `_patch_click` 的写法,但它 hard-code 了 monkeypatch `on_start_screen`。把 `_patch_click` 小改成能接 screen-check 函数名(默认仍 `"on_start_screen"`),或另写一个平行的 `_patch_click_guest` monkeypatch `on_guest_screen`。三个平行用例:

- 点一下游客页就消失 → `click_play_as_guest()` is True,`spy.clicks == 2`
- 前两轮还在、第三轮消失 → True,`spy.clicks == 2 * 3`
- 怎么点都不消失 → False,`spy.clicks == 2 * _CONFIRM_CLICK_MAX_ATTEMPTS`,不抛异常

### `test_main_worker.py` 扩

现有那套「主循环第一个调用抛 KeyboardInterrupt 掐断」的模式:

- monkeypatch `on_guest_screen` 返回 True、`click_play_as_guest` 记调用次数 → 断言 worker 第 1 轮调用了 `click_play_as_guest` 恰好一次(至少一次),之后正常走到被掐断
- monkeypatch `on_guest_screen` 恒 False → `click_play_as_guest` 一次都不被调(登录过的 profile 的常态)

现有 test 全绿。

## Self-review

- **占位符:** 无。坐标 `(960,498)` / 阈值 `0.25` / 采样框 `60×16` 都是浏览器实测推出来的确定值,注释里写明了来源和 florr 改布局时怎么重标。
- **一致性:** `on_guest_screen` / `click_play_as_guest` 跟 `on_start_screen` / `click_start_game`、`on_death_screen` / `click_continue_after_death` 完全同构(同样的 `_green_button_ratio` + `_click_button_until_gone`)。接入点(主循环顶部,death/start 之前)跟现有两个状态检测并列。
- **歧义:** 「未登录标题页」= `_PLAY_AS_GUEST_POS` 处 `_green_button_ratio > _GUEST_SCREEN_GREEN_THRESHOLD`,一个明确的像素判据。「先查游客页再查 start」的顺序在接入代码里写死。
- **范围:** utils.py 加两函数 + 三常量,main.py 两处各 3~5 行接入,两个测试文件各扩几个用例。单一 plan 够,不需要拆。
- **风险(接入时验证):** ① florr 改标题页布局 → 坐标失效 → `on_guest_screen()` 恒 False → 退化成「当前行为」(卡游客页误换服),不会更糟,用户重新截图量坐标。② 真机 1920×1080 全屏下 florr canvas 若不是整窗口尺寸(缩放/黑边)→ 实测值偏移 → 同 ①。③ 非 16:9 分辨率:`scale_point` 按 `_REF_WIDTH/_REF_HEIGHT` 独立轴缩放,水平居中的按钮在宽高比不同的屏上 x 会偏——已知局限,跟现有 start/continue 按钮一样,不在本 spec 解决。
- **风险(误报方向,尚未测量):** 上面 ①~③ 全是「假阴性」方向的分析——坐标漂移让 `on_guest_screen()` 返回 False,顶多退回当前行为。反过来的「假阳性」方向(已登录的标题页那颗绿色「开始」按钮把 `_PLAY_AS_GUEST_POS` 处的绿占比顶过 `0.25`,在登录过的号上每轮误判成游客页)只是被断言、没被量过——实测那次浏览器是登出态,没有登录态标题页的采样。即便误判也会优雅降级:`click_play_as_guest()` 10 次重试(~17s)点空后 `on_start_screen()` 接手,不卡死。收口动作:上 Windows 机截一张登录态标题页,跑 `_green_button_ratio(_PLAY_AS_GUEST_POS, 60, 16)` 确认 < 0.25,把实测值补进 `utils.py` 注释。
