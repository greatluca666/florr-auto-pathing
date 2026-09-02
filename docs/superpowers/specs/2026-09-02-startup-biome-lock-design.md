# 启动时锁定生态区 + GUI 置灰 ocean/anthell — design

## Problem

florr **不记忆**上次选的生态区。worker 每次进游戏走的是 `click_start_game()`
(点标题页绿色「开始」),进的就是 florr 当前默认那个生态区 —— 通常是花园
(Garden)。而寻路 / 刷怪用的是 `config.json` 里 `map` 指定的地图(`utils.apply_map`
→ `load_binary_map()` 读 `maps/<map>.png`)。两者对不上时:

- 花的实际位置在花园,寻路用沙漠的二值图 → 坐标全错、`get_player_position()`
  测不准、pathing 原地打转。
- 用户每次启动都得手动去标题页点一下目标生态区,才能保证进对。

用户确认失败模式就是「florr 就是不记忆」—— 不是掉线 / 死亡 / 换服务器引起的
漂移,是每次进都回默认。

**相关既有 bug:** `main.py` 主循环里换服务器只调 `switch_server()`(无参),
`utils.switch_server` 的 `biome` 参数恒为默认 `"desert"`。配 ocean/anthell 时,
连续短局触发换服务器 → 直接把玩家换到一台**沙漠**服务器。

## Goal

1. 每次 worker 回到开局菜单后(启动第一次 + 之后每轮),用 CDP
   `cp6.forceServerID("<目标生态区服务器码>")` 把客户端钉到 `config.json` 的 `map`
   对应的生态区服务器。复用已验证能触发重连的 `utils.switch_server(biome)`。
2. GUI(时块编辑器)里把 ocean / anthell 两个地图置灰、标「暂不可用」,只留沙漠
   可选。这是「暂时」措施 —— 以后加回只改一行常量。
3. 顺手修 `switch_server()` 无参 bug:换服务器时按配置的生态区换。

CDP 没有「在标题页点击生态区按钮」这个动作。`forceServerID` 在进游戏后立刻发起
重连、连到目标生态区的服务器,等价于「玩家从没真正玩到错的生态区」。

## Non-goals

- **不删 `maps/ocean.png` / `maps/anthell.png`,不动 `_VALID_MAPS`。** 用户要的是
  「GUI 里置灰、暂时无法访问」,不是从代码里铲掉。旧 config.json 里 `map=ocean`
  的时块仍要能 coerce 通过、能跑(只是 GUI 不让新选)。
- **不动 `server_lookup.BIOME_INDEX` / `test_server_lookup` 的「7 个生态区」用例。**
- **不像素点标题页的生态区选择器。** florr.io 标题页是纯 canvas(见
  `2026-09-02-play-as-guest-click-design.md`:整页只有一个 `<canvas>`),没有可查的
  DOM;而且 [[switch-server-cdp-not-clicks]] 记着:换生态区 / 换服这类「重连」操作
  走 florr 自己的 JS API(`cp6.*`)比像素点稳得多。本 spec 一律走 CDP。
- **不新写生态区查询逻辑。** `server_lookup.fetch_server_ids(biome)` +
  `utils.switch_server(biome)` 已经齐了,只是没人按配置传 `biome`。
- **不做「进游戏前就在标题页锁好」的强保证。** `forceServerID` 在标题页
  (`cp6` / `window.Module` 可能还没加载)行不行没验证过。进主循环前那次锁是
  best-effort;循环内「`click_start_game()` 之后」那次(此刻一定在局内,`cp6` 就绪)
  才是可靠的那一发。
- **不碰 `on_guest_screen` / `click_play_as_guest`**(另一条 spec 的事)。本 spec 的
  接线点排在它们**之后**、`_reassert_invert_attack` 一带。

## 机制

`utils.switch_server(biome="desert")` 现状:
`server_lookup.fetch_server_ids(biome)` 查官方实时服务器码 → `_pick_server_id()`
按 30 分钟冷却挑一个 → `cdp_bridge.eval_js('cp6.forceServerID("码")')` → 返回码。
仓库历史(`utils.py` 换服务器注释 #2)确认过:靠 CDP 执行 `forceServerID` **确实
触发重连**。

`biome` 参数取值是 `server_lookup.BIOME_INDEX` 的 key
(`garden/desert/ocean/jungle/ant_hell/hel/sewers`)。`config.json` 的 `map` 取值是
`_VALID_MAPS`(`desert/ocean/anthell`)。**`anthell` ≠ `ant_hell`** → 需要一层映射。

## 改动

### `server_lookup.py` — 加映射

```python
# config.json 的 map 名 → 本模块 BIOME_INDEX 的 key. 目前只差 anthell/ant_hell
# 这一个不一致; desert/ocean 一样. 未知名回退 desert (调用方 main._apply_worker_config
# 已保证传进来的是 _VALID_MAPS 之一, 这里只是多一层不炸).
_MAP_TO_BIOME = {"desert": "desert", "ocean": "ocean", "anthell": "ant_hell"}


def biome_key_for_map(map_name):
    """把 config.json 的 map 名翻成 fetch_server_ids() 认的生态区 key."""
    return _MAP_TO_BIOME.get(map_name, "desert")
```

### `app_config.py` — GUI 可选地图开关

```python
_VALID_MAPS = ("desert", "ocean", "anthell")   # 不动 —— coerce 仍认全部 3 个

# GUI 时块编辑器里实际可选的地图. 不在这里的(ocean/anthell)在界面上置灰、标
# 「暂不可用」. 「暂时」措施: 索敌 canvas decode 只做了沙漠、生态区锁只在沙漠
# 上验证过. 以后放开 ocean 只需把它加回这个元组. coerce 层(_VALID_MAPS)不受影响,
# 手写 config.json 或旧时块里的 ocean 仍能跑.
_GUI_ENABLED_MAPS = ("desert",)
```

### `gui_schedule.py` — 选择器换 radio + 校验

现在(line ~194):

```python
self._map = ctk.CTkOptionMenu(self, values=list(app_config._VALID_MAPS),
                              command=self._on_map_change)
self._map.set(self._block.get("map", "desert"))
self._map.pack(anchor="w", padx=12, pady=4)
```

改成一排 `CTkRadioButton`(`CTkOptionMenu` 没法逐项禁用):

- `self._map = ctk.StringVar(value=self._block.get("map", "desert"))`。
- 对每个 `app_config._VALID_MAPS`:一个 `CTkRadioButton(text=..., variable=self._map,
  value=m, command=self._on_map_change)`。不在 `app_config._GUI_ENABLED_MAPS` 里的:
  `configure(state="disabled")`,`text` 加后缀「(暂不可用)」。
- 全部读 `self._map.get()` 的地方(`_on_map_change`、`_collect`/保存段的
  `map=self._map.get()`)不变 —— `StringVar.get()` 跟 `CTkOptionMenu.get()` 同接口。
- `_on_map_change` 现在签名是 `(self, name)`(OptionMenu 回调传值);radio 的
  `command` 不传值 → 改成 `_on_map_change(self)` 里自己 `self._map.get()`。

保存校验(跟现有「时间非法 → 红字、存不了」同一条路,`_collect` / 保存前那段):

```python
if self._map.get() not in app_config._GUI_ENABLED_MAPS:
    # 海洋 / 蚁狱暂不可用 —— 打开旧的 ocean 时块会落到这
    <红字提示 "海洋 / 蚁狱暂不可用, 请选沙漠">
    return  # 不保存
```

打开一个 `map=ocean` 的旧时块:radio 停在 ocean(灰、选中),保存时红字挡住,
用户切到沙漠才能存。新建时块 `new_block_template` 默认已是 `"desert"`,不改。

### `main.py` — 锁生态区

顶部加 `import server_lookup`。

**`_apply_worker_config`** 返回 dict 加一个键:

```python
"biome": server_lookup.biome_key_for_map(src.get("map", d["map"])),
```

**新函数**(放在 `_reassert_invert_attack` 旁边):

```python
_BIOME_LOCK_RETRIES = 3
_BIOME_LOCK_RETRY_SLEEP = 3.0
_BIOME_RECONNECT_SLEEP = 3.0


def _lock_biome(biome):
    """把客户端钉到 biome 对应生态区的服务器. florr 不记忆上次选的生态区 ——
    不锁的话 click_start_game() 进的是 florr 默认那个(通常花园), 跟寻路用的地图
    对不上. 复用 switch_server(biome) 的 CDP forceServerID(已验证能触发重连).

    失败重试 _BIOME_LOCK_RETRIES 次(隔 _BIOME_LOCK_RETRY_SLEEP 秒), 都不成只警告
    不阻断(跟 _reassert_invert_attack 一个风格)—— 宁可这轮进错生态区, 也不卡死在
    开局菜单外面. 成功后 sleep 等重连落地再让调用方开始寻路. 返回 True/False.
    """
    for attempt in range(1, _BIOME_LOCK_RETRIES + 1):
        try:
            sid = switch_server(biome)
            print(f"🗺️ 已锁定生态区 {biome} (服务器 {sid})")
            time.sleep(_BIOME_RECONNECT_SLEEP)
            return True
        except Exception as e:
            print(f"⚠️ 锁定生态区第 {attempt}/{_BIOME_LOCK_RETRIES} 次失败: {e}")
            if attempt < _BIOME_LOCK_RETRIES:
                time.sleep(_BIOME_LOCK_RETRY_SLEEP)
    print("⚠️ 生态区没锁上, 先按当前服务器进游戏 (下轮回开局菜单再试)")
    return False
```

**接线三处**:

1. `run_worker` 进 `while True` 之前(`w = _apply_worker_config(cfg)` 之后、
   `print("🎮 ...")` 一带):
   ```python
   if not _lock_biome(w["biome"]):
       overlay.update(message="⚠️ 生态区未锁定, 见日志")
   ```
   best-effort:worker 刚起、florr 可能还停在标题页,`forceServerID` 可能白重试
   ~9s。靠下面第 2 处兜底。

2. 主循环里 `if on_start_screen():` 分支,`click_start_game()` + `time.sleep(3)`
   **之后**(现 main.py ~729-730),`_reassert_invert_attack()` 之前:
   ```python
   _lock_biome(w["biome"])
   ```
   此刻已在局内 → `cp6` 一定就绪 → `forceServerID` 重连到目标生态区。这是可靠的
   那一发。正在刷怪时不经过 `on_start_screen()` → 不触发,不打断刷怪。

3. 换服务器分支(现 main.py ~763)`switch_server()` → `switch_server(w["biome"])`。
   bug 修:配 ocean/anthell 时换服务器不再把人扔进沙漠。

## 测试

### `test_server_lookup.py`
- `biome_key_for_map("desert"/"ocean"/"anthell")` → `"desert"/"ocean"/"ant_hell"`。
- `biome_key_for_map("不存在")` → `"desert"`。

### `test_main_worker.py`(现有「主循环第一个调用抛 KeyboardInterrupt 掐断」模式)
- `_apply_worker_config` 返回的 dict 带 `"biome"`;`map="anthell"` → `biome=="ant_hell"`;
  `map="ocean"` → `"ocean"`。
- monkeypatch `main.switch_server` 记调用参数:
  - `_lock_biome` 第 1 次就成功 → `switch_server` 调 1 次、参数 = 配置 biome、
    `_lock_biome` 返回 True。
  - 前 2 次抛、第 3 次成 → 调 3 次、返回 True。
  - 3 次全抛 → 不抛异常、返回 False。
- worker 跑一轮(`on_start_screen` 首轮 True、次轮起 raise KeyboardInterrupt):
  `_lock_biome` 进循环前 1 次 + `on_start_screen` 分支里(`click_start_game` 之后)
  1 次。
- `on_start_screen` 恒 False(正在刷怪的常态)→ 循环体里不调 `_lock_biome`。
- 换服务器分支:monkeypatch 到触发,断言 `switch_server` 收到的是 `w["biome"]`
  不是空参 / `"desert"` 硬值(用非 desert 配置区分)。

### `test_gui_schedule.py`
- 编辑器 build 后:desert radio `state=="normal"`,ocean / anthell radio
  `state=="disabled"`。
- `self._map`(StringVar)round-trip:塞 `map="desert"` 的 block → `_collect` 出来
  `map=="desert"`。
- 打开 `map="ocean"` 的 block → 保存触发校验失败路径(红字、不写)。用现有校验
  测试的断言方式(查 error label 文案 / 保存回调没被调)。
- `block_to_active` / `new_block_template` 不受影响(仍 desert)——保持现有用例绿。

### `test_app_config.py`
- `_VALID_MAPS` 仍是 3 个(`"ocean"` in `_VALID_MAPS`)——旧 ocean 时块 coerce 不被丢。
- (可选)`_GUI_ENABLED_MAPS == ("desert",)`。
- 现有 coerce 用例(含 `map="ocean"` / `map="anthell"` 的)全绿。

## 已接受的风险

- **`forceServerID` 在标题页(进游戏前)行不行未验证。** 进循环前那次可能白重试
  3 次 ≈ 9s 启动延迟。缓解:那次可把重试次数单独降到 1(spec 里先按 3,实现时按
  实测调)。循环内那次(局内)不受影响。
- **`switch_server` 的 30 分钟同服冷却。** 频繁死亡 → 每轮 `_lock_biome` →
  冷却池(每生态区 3 台)很快耗尽 → `_pick_server_id` 回退「最久没用的那台」。
  仍是**同生态区**的服务器,生态区锁的目的达到,无害。
- **GUI 置灰不动 `_VALID_MAPS`:** 手写 config.json 仍能塞 `map="ocean"` 跑起来。
  可接受 —— 「暂时」措施,不是硬封禁。
- **radio 换 OptionMenu 的布局位移。** 3 个 radio 竖排比一个 OptionMenu 高。
  编辑器是 `CTkScrollableFrame`(可滚),不挤爆布局。实现时目测一下。
- **florr 未来改 `cp6.forceServerID` 的名字 / 语义。** 换服务器功能一起坏,不是本
  spec 单独的新风险。

## Self-review

- **占位符:** 无。重试次数 3 / 间隔 3s / 重连等待 3s 是明确初值,注释写了实现时按
  实测调的余地。
- **一致性:** `_lock_biome` 跟 `_reassert_invert_attack` 同构(warn-only、不阻断、
  每轮 + 启动各一次)。接线点(`on_start_screen` 分支内、`_reassert_invert_attack`
  之前)跟现有状态检测并列。映射 `biome_key_for_map` 放 `server_lookup`(BIOME_INDEX
  的邻居)。
- **歧义:** 「锁生态区」= `switch_server(server_lookup.biome_key_for_map(cfg map))`。
  「GUI 置灰」= 不在 `_GUI_ENABLED_MAPS` 的 radio `state="disabled"` + 保存校验挡住。
  接线顺序在代码里写死。
- **范围:** `server_lookup.py` +1 函数、`app_config.py` +1 常量、`gui_schedule.py`
  选择器换 radio + 1 处校验、`main.py` +`_lock_biome` + 3 处接线、4 个测试文件扩用
  例。单一 plan 够,不需要拆。
