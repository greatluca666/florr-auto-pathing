# GUI 阶段2 — 周计划调度 + 账号管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把阶段1 GUI 的单页控制台换成「按星期几 + 时间段」的时块调度列表 + 账号(Chrome profile)管理页,并把 Chrome 登录引导从阻塞式弹框改成非模态、可最小化的引导区。

**Architecture:** `config.json` 升到 v2(`profiles` + `schedule` 时块数组 + `active` worker 只读切片)。调度器住在 GUI 进程里,`self.after(30_000)` tick:算当前命中的时块 → 按需关/开 Chrome(换 profile)、重写 `config["active"]`、重启 `main.py --worker`。空档期停 worker。所有时块参数校验 + 调度转移决策抽成纯函数单测,tk 控件层不写自动化测试(沿用阶段1 惯例)。

**Tech Stack:** Python 3, CustomTkinter, tkinter.Canvas, Pillow, pytest, Chrome DevTools Protocol(已有 `cdp_bridge.py`)。

## Global Constraints

- **不新增第三方依赖** —— `customtkinter` / `pillow` / `websocket-client` 已在 `requirements.txt`。`_Tooltip` 用纯 tkinter 实现,不引 tooltip 库。
- **纯函数不 import 任何 GUI 库** —— `app_config.py` 全程不碰 `customtkinter` / `tkinter`;`gui_schedule.py` / `gui_accounts.py` 里的校验/数据函数写成能脱离 tk 单测的模块级函数。
- **worker 内部逻辑不动** —— 只改 `main._apply_worker_config` 读取路径(多认一层 `cfg["active"]`,缺失回退顶层扁平键再回退 `app_config.DEFAULTS`)。寻路/刷怪/索敌/换服全不碰。
- **调度驱动的运行零人工** —— Chrome 起动带 `--start-fullscreen` + `https://florr.io`;不弹任何 `messagebox`;跑到未登录 profile 就跳过时块 + 日志,不阻塞。
- **登录引导非模态** —— 不设 `-topmost`,不 `grab_set()`,主窗口全程可最小化。只有用户主动「新建账号 / 重新登录」时才触发。
- **profile 目录** —— `<exe同级>/chrome-profiles/<别名>/`,`sys.argv[0]` 语义(跟 `app_config.CONFIG_PATH` / `cdp_bridge._CHROME_PROFILE_DIR` 一致)。别名合法字符:`\w`、`-`、汉字;其余替换成 `_`;结果为空则拒。
- **星期编号** —— `0=周一 … 6=周日`。
- **时间格式** —— `HH:MM` 24 小时,正则 `^([01]\d|2[0-3]):[0-5]\d$`。`start == end == "00:00"` 约定为「全天」;其它 `start == end` 非法。`start > end`(或 `end == "00:00"` 且 `start != "00:00"`)= 跨午夜到次日。
- **重叠判定** —— 两个时块摊平成 `(星期, 起分钟, 止分钟)` 半开区间后,任一对在同一星期且 `sa < eb and sb < ea` 即冲突;`end` 相接不算重叠。GUI 编辑器保存时拦冲突;`_coerce` 只 `print` 警告不改数据。
- **索敌 AI 文案** —— 去掉所有 "YOLO" 字样;默认 `True`;GUI 里标注「仅沙漠」。`models/*.pt` 已无代码引用(canvas decode 后)。
- **每个 TDD 任务**:先写失败测试 → 跑它确认失败 → 最小实现 → 跑测试确认通过 → `pytest -q`(全量)确认无回归 → commit。

---

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `app_config.py` | GUI 与 worker 共用的配置读写 + 调度时间数学(纯函数) | v2 schema、`_coerce` v2、`migrate_v1`、`_valid_time` / `_hhmm_to_min` / `expand_block_days` / `blocks_overlap` / `active_block` / `next_start` |
| `gui_schedule.py` **新** | 时块编辑器 + 时块列表行 + 校验纯函数 + `_Tooltip` + `_safe_dirname` + `block_to_active` | 全新 |
| `gui_accounts.py` **新** | 账号页 + 账号操作纯函数(新建校验 / 改名同步 / 删除拦引用) | 全新 |
| `gui_chrome_flow.py` | 登录引导 | 重写成非模态 `LoginGuide`,删阻塞 `while`/`messagebox`/`ChromeSetupCancelled` |
| `cdp_bridge.py` | CDP 桥 + Chrome 启动 | `launch_chrome_for_profile(dir, *, open_url, fullscreen)`;`quit_and_launch_chrome` / `launch_dedicated_chrome` 变 wrapper;`_CHROME_PROFILE_DIR` → `chrome-profiles/默认` |
| `gui_app.py` | 主窗口 + 调度器 | 侧栏 `控制台`→`时间表`;接 `时间表`/`账号` 页;调度状态机(`_sched_tick` + 启停 + 编辑锁);删 `_start_worker` 的 `-topmost`/切全屏 `messagebox`/阻塞 `ensure_chrome_ready`;`plan_transition` 纯函数;索敌文案去 YOLO |
| `main.py` | worker 入口 | `_apply_worker_config` 读 `cfg["active"]`(回退链) |
| `README.md` / `PACKAGING.md` | 文档 | 周计划 + 账号页 + v2 + `chrome-profiles/`;删「自备 `models/desert.pt`」段 |
| `models/desert.pt` `models/sandstorm.pt` | — | `git rm`(收尾 task) |
| `docs/bilibili/*.md` | 视频脚本 | YOLO/模型镜头改写(收尾 task) |

---

## Task 1: `app_config` 调度时间数学(纯函数)

**Files:**
- Modify: `app_config.py`(顶部加常量 + 文件末尾加函数)
- Test: `test_app_config.py`(新增一组 `class TestScheduleMath`)

**Interfaces:**
- Consumes: 无(纯标准库)
- Produces:
  - `_ACTIVE_KEYS: tuple[str, ...]` = `("map","location","farming_area","farming_duration","consecutive_short_round_limit","enemy_ai_enabled","auto_switch_server")`
  - `_hhmm_to_min(s: str) -> int` —— `"09:30" -> 570`
  - `_valid_time(s) -> bool`
  - `expand_block_days(block: dict) -> list[tuple[int,int,int]]` —— `[(weekday, start_min, end_min)]`,`end_min` 可为 `1440`,跨午夜拆两段(次日 `weekday=(d+1)%7`)
  - `blocks_overlap(a: dict, b: dict) -> bool`
  - `active_block(schedule: list[dict], weekday: int, hhmm: str) -> dict | None` —— 第一个 `enabled` 且命中的块
  - `next_start(schedule: list[dict], weekday: int, hhmm: str) -> tuple[int,str] | None` —— 从「此刻」起最近的时块起点,返回 `(weekday, "HH:MM")`;无则 `None`

- [ ] **Step 1: 写失败测试**

在 `test_app_config.py` 末尾追加:

```python
class TestScheduleMath:
    def _blk(self, **kw):
        base = dict(id="b", enabled=True, days=[0], start="09:00", end="12:00",
                    profile="默认", map="desert", location=[1, 2],
                    farming_area=[[0, 0], [9, 9]], farming_duration=300,
                    consecutive_short_round_limit=2, enemy_ai_enabled=True,
                    auto_switch_server=True)
        base.update(kw)
        return base

    def test_hhmm_to_min(self):
        assert app_config._hhmm_to_min("00:00") == 0
        assert app_config._hhmm_to_min("09:30") == 570
        assert app_config._hhmm_to_min("23:59") == 1439

    @pytest.mark.parametrize("s, ok", [
        ("09:00", True), ("00:00", True), ("23:59", True),
        ("9:00", False), ("24:00", False), ("12:60", False), ("", False), (None, False),
    ])
    def test_valid_time(self, s, ok):
        assert app_config._valid_time(s) is ok

    def test_expand_plain(self):
        assert app_config.expand_block_days(self._blk(days=[0, 3])) == [
            (0, 540, 720), (3, 540, 720)]

    def test_expand_all_day(self):
        assert app_config.expand_block_days(
            self._blk(days=[2], start="00:00", end="00:00")) == [(2, 0, 1440)]

    def test_expand_cross_midnight(self):
        assert app_config.expand_block_days(
            self._blk(days=[0], start="22:00", end="02:00")) == [
            (0, 1320, 1440), (1, 0, 120)]

    def test_overlap_same_day_intersect(self):
        assert app_config.blocks_overlap(
            self._blk(start="09:00", end="12:00"),
            self._blk(start="11:00", end="13:00")) is True

    def test_overlap_different_day(self):
        assert app_config.blocks_overlap(
            self._blk(days=[0]), self._blk(days=[1])) is False

    def test_overlap_touching_edges_not_overlap(self):
        assert app_config.blocks_overlap(
            self._blk(start="09:00", end="12:00"),
            self._blk(start="12:00", end="15:00")) is False

    def test_overlap_cross_midnight_spills_into_next_day(self):
        assert app_config.blocks_overlap(
            self._blk(days=[0], start="22:00", end="02:00"),
            self._blk(days=[1], start="01:00", end="03:00")) is True

    def test_active_block_hit_and_miss(self):
        sched = [self._blk(id="x", days=[0], start="09:00", end="12:00")]
        assert app_config.active_block(sched, 0, "10:00")["id"] == "x"
        assert app_config.active_block(sched, 0, "12:00") is None   # 半开区间
        assert app_config.active_block(sched, 1, "10:00") is None

    def test_active_block_skips_disabled(self):
        sched = [self._blk(id="x", enabled=False, days=[0], start="09:00", end="12:00")]
        assert app_config.active_block(sched, 0, "10:00") is None

    def test_active_block_cross_midnight_belongs_to_next_day(self):
        sched = [self._blk(id="x", days=[0], start="22:00", end="02:00")]
        assert app_config.active_block(sched, 1, "01:00")["id"] == "x"

    def test_next_start_same_day(self):
        sched = [self._blk(days=[0], start="18:00", end="20:00")]
        assert app_config.next_start(sched, 0, "09:00") == (0, "18:00")

    def test_next_start_wraps_week(self):
        sched = [self._blk(days=[2], start="09:00", end="12:00")]
        assert app_config.next_start(sched, 3, "10:00") == (2, "09:00")

    def test_next_start_none_when_empty(self):
        assert app_config.next_start([], 0, "09:00") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test_app_config.py::TestScheduleMath -q`
Expected: FAIL —— `AttributeError: module 'app_config' has no attribute '_hhmm_to_min'`

- [ ] **Step 3: 最小实现**

在 `app_config.py` 顶部(`_VALID_MAPS` 附近)加:

```python
import re

_ACTIVE_KEYS = (
    "map", "location", "farming_area", "farming_duration",
    "consecutive_short_round_limit", "enemy_ai_enabled", "auto_switch_server",
)
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
```

在文件末尾加:

```python
def _hhmm_to_min(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _valid_time(s):
    return isinstance(s, str) and _TIME_RE.match(s) is not None


def expand_block_days(block):
    """把一个时块摊平成 [(weekday, start_min, end_min)]. 半开区间 [start, end).
    00:00–00:00 = 全天; start >= end(且非全天)= 跨午夜, 拆成当天尾段 + 次日头段."""
    s = _hhmm_to_min(block["start"])
    e = _hhmm_to_min(block["end"])
    out = []
    for d in block["days"]:
        if s == 0 and e == 0:
            out.append((d, 0, 1440))
        elif s < e:
            out.append((d, s, e))
        else:
            out.append((d, s, 1440))
            if e > 0:
                out.append(((d + 1) % 7, 0, e))
    return out


def blocks_overlap(a, b):
    for (da, sa, ea) in expand_block_days(a):
        for (db, sb, eb) in expand_block_days(b):
            if da == db and sa < eb and sb < ea:
                return True
    return False


def active_block(schedule, weekday, hhmm):
    m = _hhmm_to_min(hhmm)
    for blk in schedule:
        if not blk.get("enabled"):
            continue
        for (d, s, e) in expand_block_days(blk):
            if d == weekday and s <= m < e:
                return blk
    return None


def next_start(schedule, weekday, hhmm):
    """从此刻(weekday, hhmm)起, 一周内最近的一个时块起点. 返回 (weekday, 'HH:MM')."""
    now = weekday * 1440 + _hhmm_to_min(hhmm)
    best = None
    for blk in schedule:
        if not blk.get("enabled"):
            continue
        for (d, s, _e) in expand_block_days(blk):
            start_abs = d * 1440 + s
            delta = (start_abs - now) % (7 * 1440)
            if delta == 0:
                continue
            if best is None or delta < best[0]:
                best = (delta, d, "%02d:%02d" % divmod(s, 60))
    return None if best is None else (best[1], best[2])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest test_app_config.py::TestScheduleMath -q`
Expected: PASS(全部)

- [ ] **Step 5: 全量回归**

Run: `pytest -q`
Expected: PASS(阶段1 的 241 条 + 新增,零失败)

- [ ] **Step 6: Commit**

```bash
git add app_config.py test_app_config.py
git commit -m "feat(app_config): schedule time math — expand/overlap/active_block/next_start"
```

---

## Task 2: `app_config` v2 schema + `_coerce`

**Files:**
- Modify: `app_config.py`
- Test: `test_app_config.py`

**Interfaces:**
- Consumes(Task 1): `_ACTIVE_KEYS`, `_valid_time`, `blocks_overlap`
- Produces:
  - `DEFAULTS` —— 阶段1 那个 8 键扁平 dict,**保留不动**(迁移取值 + `active` 兜底)
  - `DEFAULTS_V2: dict` —— `{"version":2,"afk_enabled":False,"profiles":[{"alias":"默认","dir":"chrome-profiles/默认"}],"schedule":[],"active":{7 键取自 DEFAULTS}}`
  - `_coerce_v1(raw) -> dict` —— 就是阶段1 的 `_coerce` 改名(8 键扁平校验)
  - `_coerce(raw) -> dict` —— 新的 v2 顶层校验:`version`/`afk_enabled`/`profiles`/`schedule`/`active`
  - 坏时块整块丢弃;`profile` 悬空 → 该块 `enabled=False`(不丢);`profiles` 空 → 补「默认」;别名去重

- [ ] **Step 1: 写失败测试**

先把阶段1 里假设「扁平 DEFAULTS」的用例迁走。**替换** `test_app_config.py` 顶部到 `test_unknown_keys_are_dropped` 之间的内容为:

```python
import copy
import json

import pytest

import app_config


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "CONFIG_PATH", str(p))
    return p


def _v2_block(**kw):
    base = dict(id="blk-1", enabled=True, days=[0, 1, 2, 3, 4, 5, 6],
               start="00:00", end="00:00", profile="默认", map="desert",
               location=[22, 32], farming_area=[[9, 8], [51, 56]],
               farming_duration=300, consecutive_short_round_limit=2,
               enemy_ai_enabled=True, auto_switch_server=True)
    base.update(kw)
    return base


def _v2_cfg(**kw):
    c = copy.deepcopy(app_config.DEFAULTS_V2)
    c["schedule"] = [_v2_block()]
    c["profiles"] = [{"alias": "默认", "dir": "chrome-profiles/默认"}]
    c.update(kw)
    return c


class TestCoerceV2:
    def test_missing_file_returns_defaults_v2(self, cfg_path):
        assert app_config.load_config() == app_config.DEFAULTS_V2

    def test_v2_roundtrips(self, cfg_path):
        cfg = _v2_cfg()
        app_config.save_config(cfg)
        assert app_config.load_config() == app_config.load_config()  # 稳定
        got = app_config.load_config()
        assert got["schedule"][0]["id"] == "blk-1"
        assert got["profiles"] == [{"alias": "默认", "dir": "chrome-profiles/默认"}]

    def test_top_level_not_dict_falls_back(self, cfg_path):
        cfg_path.write_text("[1,2,3]", encoding="utf-8")
        assert app_config.load_config() == app_config.DEFAULTS_V2

    @pytest.mark.parametrize("bad_block", [
        {"days": []},
        {"days": [7]},
        {"start": "9:00"},
        {"start": "10:00", "end": "10:00"},   # start==end 非全天
        {"map": "nonsense"},
        {"location": "12,32"},
        {"farming_area": [[1, 2], [3]]},
        {"farming_duration": 0},
        {"enemy_ai_enabled": "yes"},
    ])
    def test_bad_block_is_dropped_whole(self, cfg_path, bad_block):
        good = _v2_block(id="good")
        bad = _v2_block(id="bad", **bad_block)
        cfg_path.write_text(json.dumps(_v2_cfg(schedule=[bad, good])), encoding="utf-8")
        got = app_config.load_config()
        assert [b["id"] for b in got["schedule"]] == ["good"]

    def test_dangling_profile_disables_block_not_drops(self, cfg_path):
        blk = _v2_block(id="x", profile="不存在")
        cfg_path.write_text(json.dumps(_v2_cfg(schedule=[blk])), encoding="utf-8")
        got = app_config.load_config()
        assert [b["id"] for b in got["schedule"]] == ["x"]
        assert got["schedule"][0]["enabled"] is False

    def test_empty_profiles_gets_default(self, cfg_path):
        cfg_path.write_text(json.dumps(_v2_cfg(profiles=[])), encoding="utf-8")
        assert app_config.load_config()["profiles"] == [
            {"alias": "默认", "dir": "chrome-profiles/默认"}]

    def test_duplicate_alias_deduped(self, cfg_path):
        cfg_path.write_text(json.dumps(_v2_cfg(profiles=[
            {"alias": "a", "dir": "chrome-profiles/a"},
            {"alias": "a", "dir": "chrome-profiles/a2"},
        ], schedule=[])), encoding="utf-8")
        got = app_config.load_config()
        assert [p["alias"] for p in got["profiles"]] == ["a"]

    def test_days_sorted_deduped(self, cfg_path):
        blk = _v2_block(days=[4, 0, 0, 2])
        cfg_path.write_text(json.dumps(_v2_cfg(schedule=[blk])), encoding="utf-8")
        assert app_config.load_config()["schedule"][0]["days"] == [0, 2, 4]

    def test_active_defaults_from_first_block_when_absent(self, cfg_path):
        c = _v2_cfg(schedule=[_v2_block(map="ocean", location=[5, 5])])
        del c["active"]
        cfg_path.write_text(json.dumps(c), encoding="utf-8")
        got = app_config.load_config()
        assert got["active"]["map"] == "ocean"
        assert got["active"]["location"] == [5, 5]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test_app_config.py::TestCoerceV2 -q`
Expected: FAIL —— `AttributeError: module 'app_config' has no attribute 'DEFAULTS_V2'`

- [ ] **Step 3: 最小实现**

`app_config.py` 里:把现有 `def _coerce(raw):` 整体 **改名** 为 `def _coerce_v1(raw):`(函数体不动)。在其后加:

```python
DEFAULTS_V2 = {
    "version": 2,
    "afk_enabled": False,
    "profiles": [{"alias": "默认", "dir": "chrome-profiles/默认"}],
    "schedule": [],
    "active": {k: copy.deepcopy(DEFAULTS[k]) for k in _ACTIVE_KEYS},
}


def _coerce_profiles(v):
    out, seen = [], set()
    if isinstance(v, list):
        for item in v:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            if not (isinstance(alias, str) and alias.strip()):
                continue
            d = item.get("dir")
            if not (isinstance(d, str) and d):
                d = f"chrome-profiles/{alias}"
            if alias in seen:
                print(f"⚠️ config.json 重复的账号别名 {alias!r}, 丢弃后一个")
                continue
            seen.add(alias)
            out.append({"alias": alias, "dir": d})
    if not out:
        out = [{"alias": "默认", "dir": "chrome-profiles/默认"}]
    return out


def _coerce_block(raw, aliases, n):
    if not isinstance(raw, dict):
        return None
    bid = raw["id"] if isinstance(raw.get("id"), str) and raw["id"] else f"blk-{n}"
    days = raw.get("days")
    if not (isinstance(days, list) and days and all(
            isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days)):
        return None
    days = sorted(set(days))
    start, end = raw.get("start"), raw.get("end")
    if not (_valid_time(start) and _valid_time(end)):
        return None
    if start == end and start != "00:00":
        return None
    profile = raw.get("profile")
    if not isinstance(profile, str):
        return None
    if raw.get("map") not in _VALID_MAPS:
        return None
    loc = raw.get("location")
    if not _is_int_pair(loc):
        return None
    area = raw.get("farming_area")
    if not (isinstance(area, (list, tuple)) and len(area) == 2
            and all(_is_int_pair(c) for c in area)):
        return None
    dur = raw.get("farming_duration")
    if not (isinstance(dur, int) and not isinstance(dur, bool) and dur > 0):
        return None
    lim = raw.get("consecutive_short_round_limit")
    if not (isinstance(lim, int) and not isinstance(lim, bool) and lim >= 1):
        return None
    eai, asw = raw.get("enemy_ai_enabled"), raw.get("auto_switch_server")
    if not isinstance(eai, bool) or not isinstance(asw, bool):
        return None
    enabled = raw.get("enabled")
    enabled = True if not isinstance(enabled, bool) else enabled
    if profile not in aliases:
        print(f"⚠️ config.json 时块 {bid} 引用的账号 {profile!r} 不存在, 已禁用该时块")
        enabled = False
    return {
        "id": bid, "enabled": enabled, "days": days, "start": start, "end": end,
        "profile": profile, "map": raw["map"],
        "location": [int(loc[0]), int(loc[1])],
        "farming_area": [[int(area[0][0]), int(area[0][1])],
                         [int(area[1][0]), int(area[1][1])]],
        "farming_duration": dur, "consecutive_short_round_limit": lim,
        "enemy_ai_enabled": eai, "auto_switch_server": asw,
    }


def _coerce_schedule(v, aliases):
    if not isinstance(v, list):
        return []
    out = []
    for i, raw_blk in enumerate(v, 1):
        blk = _coerce_block(raw_blk, aliases, i)
        if blk is None:
            print(f"⚠️ config.json 第 {i} 个时块不合法, 整块丢弃")
        else:
            out.append(blk)
    for a in range(len(out)):
        for b in range(a + 1, len(out)):
            if out[a]["enabled"] and out[b]["enabled"] and blocks_overlap(out[a], out[b]):
                print(f"⚠️ config.json 时块 {out[a]['id']} 与 {out[b]['id']} 时间重叠")
    return out


def _coerce_active(v, schedule):
    if isinstance(v, dict):
        got = _coerce_v1(v)
        return {k: got[k] for k in _ACTIVE_KEYS}
    if schedule:
        return {k: copy.deepcopy(schedule[0][k]) for k in _ACTIVE_KEYS}
    return {k: copy.deepcopy(DEFAULTS[k]) for k in _ACTIVE_KEYS}


def _coerce(raw):
    if not isinstance(raw, dict):
        print(f"⚠️ config.json 顶层不是对象(是 {type(raw).__name__}), 全部用默认值")
        return copy.deepcopy(DEFAULTS_V2)
    cfg = {"version": 2}
    cfg["afk_enabled"] = raw["afk_enabled"] if isinstance(raw.get("afk_enabled"), bool) else False
    cfg["profiles"] = _coerce_profiles(raw.get("profiles"))
    aliases = {p["alias"] for p in cfg["profiles"]}
    cfg["schedule"] = _coerce_schedule(raw.get("schedule"), aliases)
    cfg["active"] = _coerce_active(raw.get("active"), cfg["schedule"])
    return cfg
```

**先不改 `load_config` / `save_config`**(Task 3 处理迁移与写回);但 `save_config` 现在调的是新 `_coerce`(v2),而它还会被 Task 3 前的测试用到 —— 本步把 `save_config` 里 `_coerce(cfg)` 保持不变即可(名字没变,指向新 v2 版)。`load_config` 里 `_coerce(raw)` 同理暂时直接指向 v2 版;v1 文件的迁移在 Task 3 加。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest test_app_config.py::TestCoerceV2 -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `pytest -q`
Expected: PASS。若 `test_main_worker.py` 里 `run_worker({})` 相关用例失败(因为 `load_config` 结构变了)—— Task 5 修;此处若红,临时在该测试加 `@pytest.mark.xfail(reason="Task 5")` 并在提交信息注明,Task 5 摘掉。

- [ ] **Step 6: Commit**

```bash
git add app_config.py test_app_config.py
git commit -m "feat(app_config): v2 schema — profiles + schedule + active, _coerce rewrite"
```

---

## Task 3: `app_config` v1→v2 迁移 + `load_config` 写回

**Files:**
- Modify: `app_config.py`
- Test: `test_app_config.py`

**Interfaces:**
- Consumes(Task 2): `_coerce_v1`, `_coerce`, `DEFAULTS_V2`, `_ACTIVE_KEYS`
- Produces:
  - `migrate_v1(raw: dict) -> dict` —— v1 扁平 → v2(单「默认」profile + 一个 `days=[0..6] start=end="00:00"` 时块 + `active`)
  - `_rename_legacy_profile_dir()` —— `<root>/chrome-profile` → `<root>/chrome-profiles/默认`,best-effort
  - `load_config()` —— 读到 `raw.get("version") != 2` 的 dict → `migrate_v1` → `_coerce` → 写回 → 返回

- [ ] **Step 1: 写失败测试**

追加到 `test_app_config.py`:

```python
class TestMigrationV1:
    def _v1(self, **kw):
        base = dict(map="ocean", location=[7, 8], farming_area=[[1, 1], [5, 5]],
                    farming_duration=222, consecutive_short_round_limit=3,
                    enemy_ai_enabled=False, auto_switch_server=False, afk_enabled=True)
        base.update(kw)
        return base

    def test_v1_flat_migrates_to_single_all_week_block(self, cfg_path):
        cfg_path.write_text(json.dumps(self._v1()), encoding="utf-8")
        got = app_config.load_config()
        assert got["version"] == 2
        assert got["afk_enabled"] is True
        assert got["profiles"] == [{"alias": "默认", "dir": "chrome-profiles/默认"}]
        assert len(got["schedule"]) == 1
        blk = got["schedule"][0]
        assert blk["days"] == [0, 1, 2, 3, 4, 5, 6]
        assert blk["start"] == "00:00" and blk["end"] == "00:00"
        assert blk["profile"] == "默认"
        assert blk["map"] == "ocean" and blk["location"] == [7, 8]
        assert blk["farming_duration"] == 222
        assert got["active"]["map"] == "ocean"
        assert got["active"]["consecutive_short_round_limit"] == 3

    def test_migration_is_written_back_as_v2(self, cfg_path):
        cfg_path.write_text(json.dumps(self._v1()), encoding="utf-8")
        app_config.load_config()
        on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert on_disk["version"] == 2
        assert "schedule" in on_disk

    def test_migration_tolerates_bad_v1_values(self, cfg_path):
        cfg_path.write_text(json.dumps(self._v1(map="bogus", farming_duration=-1)),
                            encoding="utf-8")
        blk = app_config.load_config()["schedule"][0]
        assert blk["map"] == app_config.DEFAULTS["map"]
        assert blk["farming_duration"] == app_config.DEFAULTS["farming_duration"]

    def test_rename_legacy_dir_best_effort(self, tmp_path, monkeypatch):
        root = tmp_path
        monkeypatch.setattr(app_config.sys, "argv", [str(root / "app.exe")])
        (root / "chrome-profile").mkdir()
        app_config._rename_legacy_profile_dir()
        assert (root / "chrome-profiles" / "默认").is_dir()
        assert not (root / "chrome-profile").exists()

    def test_rename_legacy_dir_noop_when_target_exists(self, tmp_path, monkeypatch):
        root = tmp_path
        monkeypatch.setattr(app_config.sys, "argv", [str(root / "app.exe")])
        (root / "chrome-profile").mkdir()
        (root / "chrome-profiles" / "默认").mkdir(parents=True)
        app_config._rename_legacy_profile_dir()   # 不该抛
        assert (root / "chrome-profile").exists()  # 保留原样
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test_app_config.py::TestMigrationV1 -q`
Expected: FAIL —— `AttributeError: ... 'migrate_v1'`

- [ ] **Step 3: 最小实现**

`app_config.py` 加:

```python
def _rename_legacy_profile_dir():
    """阶段1 的 chrome-profile/ → 阶段2 的 chrome-profiles/默认/. best-effort:
    目标已存在 / 改名失败(目录被占用)都只 print 一句, 不抛 —— 用户下次用
    『默认』账号时会走登录引导补上。"""
    root = os.path.dirname(os.path.abspath(sys.argv[0]))
    old = os.path.join(root, "chrome-profile")
    new = os.path.join(root, "chrome-profiles", "默认")
    if not os.path.isdir(old) or os.path.exists(new):
        return
    try:
        os.makedirs(os.path.join(root, "chrome-profiles"), exist_ok=True)
        os.rename(old, new)
    except OSError as e:
        print(f"⚠️ 旧 Chrome profile 目录改名失败({e}); 用『默认』账号时会要求重新登录")


def migrate_v1(raw):
    flat = _coerce_v1(raw if isinstance(raw, dict) else {})
    _rename_legacy_profile_dir()
    block = {
        "id": "blk-1", "enabled": True, "days": [0, 1, 2, 3, 4, 5, 6],
        "start": "00:00", "end": "00:00", "profile": "默认",
    }
    for k in _ACTIVE_KEYS:
        block[k] = copy.deepcopy(flat[k])
    return {
        "version": 2,
        "afk_enabled": flat["afk_enabled"],
        "profiles": [{"alias": "默认", "dir": "chrome-profiles/默认"}],
        "schedule": [block],
        "active": {k: copy.deepcopy(flat[k]) for k in _ACTIVE_KEYS},
    }
```

改 `load_config`:

```python
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS_V2)
    except Exception as e:
        print(f"⚠️ 读 config.json 失败, 全部用默认值: {e}")
        return copy.deepcopy(DEFAULTS_V2)
    if not isinstance(raw, dict):
        print(f"⚠️ config.json 顶层不是对象(是 {type(raw).__name__}), 全部用默认值")
        return copy.deepcopy(DEFAULTS_V2)
    if raw.get("version") != 2:
        cfg = _coerce(migrate_v1(raw))
        try:
            _write(cfg)
        except OSError as e:
            print(f"⚠️ 迁移后写回 config.json 失败: {e}")
        return cfg
    return _coerce(raw)
```

加 `_write` 并让 `save_config` 复用:

```python
def _write(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def save_config(cfg):
    """写之前先 _coerce, GUI 传进来的也不例外 —— 别让界面 bug 写出坏配置。"""
    _write(_coerce(cfg))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest test_app_config.py -q`
Expected: PASS(`TestScheduleMath` + `TestCoerceV2` + `TestMigrationV1` + 保留的 `test_unknown_keys_are_dropped` 若还在则需适配 v2 —— 若它断言扁平结构,删掉它,迁移测试已覆盖「未知键丢弃」语义)

- [ ] **Step 5: 全量回归**

Run: `pytest -q`
Expected: `test_app_config.py` 全绿。其它文件若因 `load_config` 结构变化报错留给对应 Task。

- [ ] **Step 6: Commit**

```bash
git add app_config.py test_app_config.py
git commit -m "feat(app_config): migrate v1 flat config to v2 on load, write back"
```

---

## Task 4: `cdp_bridge.launch_chrome_for_profile`

**Files:**
- Modify: `cdp_bridge.py`
- Test: `test_cdp_bridge.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `launch_chrome_for_profile(profile_dir: str, *, open_url: str = "https://florr.io", fullscreen: bool = False) -> None`
    —— `_quit_all_chrome()` 后带三个 CDP 参数 + `--user-data-dir=<profile_dir>` +(`fullscreen` 时)`--start-fullscreen` + `open_url` 位置参数拉起 Chrome。Windows 找不到 `chrome.exe` → `RuntimeError`。
  - `_CHROME_PROFILE_DIR` 常量值改为 `os.path.join(<root>, "chrome-profiles", "默认")`
  - `quit_and_launch_chrome()` 变 `launch_chrome_for_profile(_CHROME_PROFILE_DIR)` 的 wrapper(签名不变)
  - `launch_dedicated_chrome()` 内部循环不变,只是启动那步走 `quit_and_launch_chrome()`(已是)

- [ ] **Step 1: 写失败测试**

追加到 `test_cdp_bridge.py`:

```python
class TestLaunchChromeForProfile:
    def test_windows_args_include_profile_and_url(self, monkeypatch):
        monkeypatch.setattr(cdp_bridge.sys, "platform", "win32")
        monkeypatch.setattr(cdp_bridge, "_quit_all_chrome", lambda: None)
        with patch("cdp_bridge._find_windows_chrome", return_value=r"C:\chrome.exe"), \
             patch("cdp_bridge.subprocess.Popen") as mock_popen:
            cdp_bridge.launch_chrome_for_profile(r"D:\profiles\小号2",
                                                open_url="https://florr.io", fullscreen=True)
        argv = mock_popen.call_args[0][0]
        assert argv[0] == r"C:\chrome.exe"
        assert r"--user-data-dir=D:\profiles\小号2" in argv
        assert "--start-fullscreen" in argv
        assert "--remote-debugging-port=9222" in argv
        assert "--remote-allow-origins=*" in argv
        assert argv[-1] == "https://florr.io"

    def test_fullscreen_false_omits_flag(self, monkeypatch):
        monkeypatch.setattr(cdp_bridge.sys, "platform", "win32")
        monkeypatch.setattr(cdp_bridge, "_quit_all_chrome", lambda: None)
        with patch("cdp_bridge._find_windows_chrome", return_value=r"C:\chrome.exe"), \
             patch("cdp_bridge.subprocess.Popen") as mock_popen:
            cdp_bridge.launch_chrome_for_profile(r"D:\p", fullscreen=False)
        assert "--start-fullscreen" not in mock_popen.call_args[0][0]

    def test_macos_uses_open_dash_a_with_args(self, monkeypatch):
        monkeypatch.setattr(cdp_bridge.sys, "platform", "darwin")
        monkeypatch.setattr(cdp_bridge, "_quit_all_chrome", lambda: None)
        with patch("cdp_bridge.subprocess.Popen") as mock_popen:
            cdp_bridge.launch_chrome_for_profile("/tmp/p", open_url="https://florr.io")
        argv = mock_popen.call_args[0][0]
        assert argv[:4] == ["open", "-a", "Google Chrome", "--args"]
        assert "--user-data-dir=/tmp/p" in argv
        assert argv[-1] == "https://florr.io"

    def test_windows_missing_chrome_raises(self, monkeypatch):
        monkeypatch.setattr(cdp_bridge.sys, "platform", "win32")
        monkeypatch.setattr(cdp_bridge, "_quit_all_chrome", lambda: None)
        with patch("cdp_bridge._find_windows_chrome", return_value=None):
            with pytest.raises(RuntimeError):
                cdp_bridge.launch_chrome_for_profile(r"D:\p")

    def test_quit_and_launch_chrome_delegates(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(cdp_bridge, "launch_chrome_for_profile",
                            lambda d, **k: seen.update(dir=d, kw=k))
        cdp_bridge.quit_and_launch_chrome()
        assert seen["dir"] == cdp_bridge._CHROME_PROFILE_DIR
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test_cdp_bridge.py::TestLaunchChromeForProfile -q`
Expected: FAIL —— `AttributeError: ... 'launch_chrome_for_profile'`

- [ ] **Step 3: 最小实现**

`cdp_bridge.py`:改常量:

```python
_CHROME_PROFILE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(sys.argv[0])), "chrome-profiles", "默认"
)
```

加函数(放在 `_launch_chrome_process` 之后):

```python
def launch_chrome_for_profile(profile_dir, *, open_url="https://florr.io", fullscreen=False):
    """杀掉所有 Chrome, 用指定 profile 目录拉起专用实例, 顺带打开 open_url.
    调度器换账号时用(fullscreen=True 让 florr.io canvas 直接铺满); 登录引导用
    (fullscreen=False, 普通窗口方便登录). 找不到 chrome.exe(仅 Windows 需按路径
    找)时抛 RuntimeError。"""
    _quit_all_chrome()
    args = [
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if fullscreen:
        args.append("--start-fullscreen")
    args.append(open_url)
    if sys.platform == "win32":
        chrome_path = _find_windows_chrome()
        if chrome_path is None:
            raise RuntimeError("没找到 Chrome, 请先安装: https://www.google.com/chrome/")
        subprocess.Popen([chrome_path] + args)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Google Chrome", "--args"] + args)
    else:
        raise RuntimeError(f"不支持的平台: {sys.platform}")
```

改 `quit_and_launch_chrome`:

```python
def quit_and_launch_chrome():
    """非交互版: 默认『默认』profile, 不全屏(命令行/旧 GUI 引导用)。"""
    launch_chrome_for_profile(_CHROME_PROFILE_DIR, fullscreen=False)
```

(`_launch_chrome_process` 保留 —— 现有 `test_cdp_bridge.py` 一堆用例还在测它;它现在只被老的 `quit_and_launch_chrome` 之外的路径……实际已无人调。留着不碍事,或在本步删掉它 + 删对应 4 条测试。**保守起见留着**。)

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest test_cdp_bridge.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `pytest -q`

- [ ] **Step 6: Commit**

```bash
git add cdp_bridge.py test_cdp_bridge.py
git commit -m "feat(cdp_bridge): launch_chrome_for_profile(dir, open_url, fullscreen)"
```

---

## Task 5: `main._apply_worker_config` 读 `cfg["active"]`

**Files:**
- Modify: `main.py`(`_apply_worker_config` ~ line 645-657)
- Test: `test_main_worker.py`

**Interfaces:**
- Consumes(Task 2/3): `app_config.DEFAULTS`, `cfg["active"]`
- Produces: `_apply_worker_config(cfg)` —— `active = cfg.get("active")`;非 dict 时回退 `cfg` 本身(老扁平 / 手写文件);逐键再回退 `app_config.DEFAULTS`。返回值结构不变(`location`/`farming_area`/`farming_duration`/`short_round_limit`/`enemy_ai_enabled`/`auto_switch_server`)。

- [ ] **Step 1: 写失败测试**

改 `test_main_worker.py::test_apply_worker_config_maps_keys` 为「读 active」,并加回退用例:

```python
def test_apply_worker_config_reads_active_slice(monkeypatch):
    applied = {}
    monkeypatch.setattr(main, "apply_map", lambda name: applied.setdefault("map", name))
    cfg = {"version": 2, "active": {
        "map": "ocean", "location": [11, 22], "farming_area": [[1, 2], [3, 4]],
        "farming_duration": 120, "consecutive_short_round_limit": 5,
        "enemy_ai_enabled": False, "auto_switch_server": False,
    }}
    w = main._apply_worker_config(cfg)
    assert applied["map"] == "ocean"
    assert w["location"] == (11, 22)
    assert w["farming_area"] == [(1, 2), (3, 4)]
    assert w["farming_duration"] == 120
    assert w["short_round_limit"] == 5
    assert w["enemy_ai_enabled"] is False
    assert w["auto_switch_server"] is False


def test_apply_worker_config_falls_back_to_flat_when_no_active(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    cfg = {"map": "anthell", "location": [1, 1], "farming_area": [[0, 0], [2, 2]],
           "farming_duration": 99, "consecutive_short_round_limit": 4,
           "enemy_ai_enabled": True, "auto_switch_server": True}
    w = main._apply_worker_config(cfg)
    assert w["farming_duration"] == 99
    assert w["short_round_limit"] == 4


def test_apply_worker_config_fills_missing_from_defaults(monkeypatch):
    monkeypatch.setattr(main, "apply_map", lambda name: None)
    w = main._apply_worker_config({"version": 2, "active": {"map": "desert"}})
    import app_config
    assert w["farming_duration"] == app_config.DEFAULTS["farming_duration"]
    assert w["location"] == tuple(app_config.DEFAULTS["location"])
```

(若 Task 2 里给某个用例加了 `xfail`,这里一并去掉那个标记。)

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test_main_worker.py::test_apply_worker_config_reads_active_slice test_main_worker.py::test_apply_worker_config_falls_back_to_flat_when_no_active -q`
Expected: FAIL

- [ ] **Step 3: 最小实现**

`main.py`:

```python
def _apply_worker_config(cfg):
    """把 config.json 的值应用/摊平成 run_worker 主循环要用的局部值.
    v2: 读 cfg['active']; 老扁平文件 / 手写调试文件: 回退 cfg 本身; 再缺: DEFAULTS.
    apply_map() 必须在这里就调 —— utils 的 MAP 是模块级全局。"""
    src = cfg.get("active")
    if not isinstance(src, dict):
        src = cfg
    d = app_config.DEFAULTS
    apply_map(src.get("map", d["map"]))
    return {
        "location": tuple(src.get("location", d["location"])),
        "farming_area": [tuple(p) for p in src.get("farming_area", d["farming_area"])],
        "farming_duration": src.get("farming_duration", d["farming_duration"]),
        "short_round_limit": src.get("consecutive_short_round_limit",
                                    d["consecutive_short_round_limit"]),
        "enemy_ai_enabled": src.get("enemy_ai_enabled", d["enemy_ai_enabled"]),
        "auto_switch_server": src.get("auto_switch_server", d["auto_switch_server"]),
    }
```

确认 `main.py` 顶部已 `import app_config`(是,`__main__` 里用了)。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest test_main_worker.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `pytest -q`
Expected: 全绿(此时 `app_config` + `cdp_bridge` + `main` 三处已自洽;GUI 相关文件还没动)

- [ ] **Step 6: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "feat(main): _apply_worker_config reads cfg['active'] with flat/DEFAULTS fallback"
```

---

## Task 6: `gui_schedule` 纯函数 —— `_safe_dirname` / `validate_block` / `block_to_active`

**Files:**
- Create: `gui_schedule.py`
- Test: `test_gui_schedule.py`(新)

**Interfaces:**
- Consumes(Task 1): `app_config.blocks_overlap`, `app_config._valid_time`
- Produces:
  - `_safe_dirname(name: str) -> str` —— 保留 `\w`(含汉字)`-`,其余 → `_`;首尾 `_` 去掉;空 → `""`
  - `WEEKDAY_LABELS = ("一","二","三","四","五","六","日")`
  - `block_to_active(block: dict) -> dict` —— 取 7 个 `_ACTIVE_KEYS`,数值 `int()`/`bool()` 规整,坐标转 list
  - `validate_block(block: dict, others: list[dict]) -> str | None` —— 返回错误中文串或 `None`
    - `days` 非空(≥1)
    - `start`/`end` 都 `_valid_time`;`start == end` 且非 `"00:00"` → 「起止时间不能相同」
    - `location` 和 `farming_area` 至少一个非空
    - `farming_duration` / `consecutive_short_round_limit` 正整数
    - 对 `others`(不含自己)逐一 `app_config.blocks_overlap` → 「跟时块 {id} 时间重叠」

- [ ] **Step 1: 写失败测试**

`test_gui_schedule.py`:

```python
import pytest

import gui_schedule as gs


def _blk(**kw):
    base = dict(id="b", enabled=True, days=[0], start="09:00", end="12:00",
               profile="默认", map="desert", location=[1, 2],
               farming_area=[[0, 0], [9, 9]], farming_duration=300,
               consecutive_short_round_limit=2, enemy_ai_enabled=True,
               auto_switch_server=True)
    base.update(kw)
    return base


@pytest.mark.parametrize("raw, out", [
    ("小号2", "小号2"),
    ("main account", "main_account"),
    ("a/b\\c", "a_b_c"),
    ("  x  ", "x"),
    ("***", ""),
])
def test_safe_dirname(raw, out):
    assert gs._safe_dirname(raw) == out


def test_block_to_active_shapes():
    a = gs.block_to_active(_blk(map="ocean", location=(5, 6),
                               farming_area=[(1, 1), (2, 2)], farming_duration="120"))
    assert a == {
        "map": "ocean", "location": [5, 6], "farming_area": [[1, 1], [2, 2]],
        "farming_duration": 120, "consecutive_short_round_limit": 2,
        "enemy_ai_enabled": True, "auto_switch_server": True,
    }


def test_validate_ok():
    assert gs.validate_block(_blk(), []) is None


def test_validate_no_days():
    assert "星期" in gs.validate_block(_blk(days=[]), [])


def test_validate_bad_time():
    assert gs.validate_block(_blk(start="9am"), [])


def test_validate_equal_times():
    assert gs.validate_block(_blk(start="09:00", end="09:00"), [])


def test_validate_all_day_equal_times_ok():
    assert gs.validate_block(_blk(start="00:00", end="00:00"), []) is None


def test_validate_no_point_no_area():
    assert gs.validate_block(_blk(location=None, farming_area=None), [])


def test_validate_bad_numbers():
    assert gs.validate_block(_blk(farming_duration=0), [])
    assert gs.validate_block(_blk(consecutive_short_round_limit=-1), [])


def test_validate_overlap_reports_other_id():
    other = _blk(id="blk-9", start="11:00", end="13:00")
    msg = gs.validate_block(_blk(id="mine"), [other])
    assert "blk-9" in msg


def test_validate_ignores_self_in_others():
    me = _blk(id="mine")
    assert gs.validate_block(me, [me]) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test_gui_schedule.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'gui_schedule'`

- [ ] **Step 3: 最小实现**

`gui_schedule.py`(先只放纯函数 + imports;tk 控件 Task 9 加):

```python
"""时块调度: 编辑器(CTkToplevel) + 列表折叠行 + 校验纯函数 + Tooltip.
纯函数(_safe_dirname / validate_block / block_to_active)不碰 tk, 单测直接调。"""
import re

import app_config

WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")

_ACTIVE_KEYS = app_config._ACTIVE_KEYS
_SAFE_DIR_RE = re.compile(r"[^\w\-]", re.UNICODE)


def _safe_dirname(name):
    if not isinstance(name, str):
        return ""
    cleaned = _SAFE_DIR_RE.sub("_", name.strip())
    return cleaned.strip("_")


def block_to_active(block):
    loc = block["location"]
    area = block["farming_area"]
    return {
        "map": block["map"],
        "location": [int(loc[0]), int(loc[1])],
        "farming_area": [[int(area[0][0]), int(area[0][1])],
                         [int(area[1][0]), int(area[1][1])]],
        "farming_duration": int(block["farming_duration"]),
        "consecutive_short_round_limit": int(block["consecutive_short_round_limit"]),
        "enemy_ai_enabled": bool(block["enemy_ai_enabled"]),
        "auto_switch_server": bool(block["auto_switch_server"]),
    }


def _positive_int(v):
    try:
        return int(v) > 0
    except (TypeError, ValueError):
        return False


def validate_block(block, others):
    """返回错误中文串, 或 None 表示通过. others 里跟 block 同 id 的会被跳过。"""
    if not block.get("days"):
        return "至少勾一个星期"
    start, end = block.get("start"), block.get("end")
    if not (app_config._valid_time(start) and app_config._valid_time(end)):
        return "时间格式要是 HH:MM"
    if start == end and start != "00:00":
        return "起止时间不能相同(全天请填 00:00–00:00)"
    if not block.get("location") and not block.get("farming_area"):
        return "在地图上点个目标点, 或框个刷怪区"
    if not _positive_int(block.get("farming_duration")):
        return "刷怪时长要是正整数"
    if not _positive_int(block.get("consecutive_short_round_limit")):
        return "连续短局阈值要是正整数"
    for o in others:
        if o.get("id") == block.get("id"):
            continue
        if app_config.blocks_overlap(block, o):
            return f"跟时块 {o.get('id')} 时间重叠"
    return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest test_gui_schedule.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `pytest -q`

- [ ] **Step 6: Commit**

```bash
git add gui_schedule.py test_gui_schedule.py
git commit -m "feat(gui_schedule): pure fns — _safe_dirname, validate_block, block_to_active"
```

---

## Task 7: `gui_accounts` 纯函数 —— 新建校验 / 改名同步 / 删除拦引用

**Files:**
- Create: `gui_accounts.py`
- Test: `test_gui_accounts.py`(新)

**Interfaces:**
- Consumes(Task 6): `gui_schedule._safe_dirname`
- Produces(全部对 `cfg` dict 操作,返回 `(cfg, None)` 或 `(cfg_unchanged, "错误串")`;不写盘,调用方负责 `app_config.save_config`):
  - `add_profile(cfg: dict, alias: str) -> tuple[dict, str | None]` —— 校验非空 / `_safe_dirname` 非空 / 别名不重复;成功则 `cfg["profiles"]` 追加 `{"alias": alias, "dir": f"chrome-profiles/{alias}"}`
  - `rename_profile(cfg: dict, old: str, new: str) -> tuple[dict, str | None]` —— 校验 `new`;改 `profiles[i]` 的 `alias`+`dir`;把 `schedule` 里 `profile == old` 的改成 `new`
  - `delete_profile(cfg: dict, alias: str) -> tuple[dict, str | None]` —— `schedule` 里有引用 → 返回 `"时块 x, y 还在用『alias』"`;否则从 `profiles` 移除
  - `profile_dir(cfg: dict, alias: str) -> str | None`

- [ ] **Step 1: 写失败测试**

`test_gui_accounts.py`:

```python
import copy

import pytest

import gui_accounts as ga


def _cfg():
    return {
        "version": 2, "afk_enabled": False,
        "profiles": [{"alias": "默认", "dir": "chrome-profiles/默认"},
                     {"alias": "小号2", "dir": "chrome-profiles/小号2"}],
        "schedule": [
            {"id": "blk-1", "enabled": True, "profile": "默认"},
            {"id": "blk-2", "enabled": True, "profile": "小号2"},
            {"id": "blk-3", "enabled": False, "profile": "小号2"},
        ],
        "active": {},
    }


def test_add_profile_ok():
    cfg, err = ga.add_profile(_cfg(), "新号")
    assert err is None
    assert cfg["profiles"][-1] == {"alias": "新号", "dir": "chrome-profiles/新号"}


def test_add_profile_rejects_blank():
    _, err = ga.add_profile(_cfg(), "   ")
    assert err


def test_add_profile_rejects_pure_symbols():
    _, err = ga.add_profile(_cfg(), "***")
    assert err


def test_add_profile_rejects_duplicate():
    _, err = ga.add_profile(_cfg(), "小号2")
    assert err


def test_rename_profile_updates_schedule_refs():
    cfg, err = ga.rename_profile(_cfg(), "小号2", "big")
    assert err is None
    aliases = [p["alias"] for p in cfg["profiles"]]
    assert "big" in aliases and "小号2" not in aliases
    assert [b["profile"] for b in cfg["schedule"]] == ["默认", "big", "big"]
    assert cfg["profiles"][1]["dir"] == "chrome-profiles/big"


def test_rename_profile_rejects_duplicate_target():
    _, err = ga.rename_profile(_cfg(), "小号2", "默认")
    assert err


def test_delete_profile_blocked_by_references():
    cfg, err = ga.delete_profile(_cfg(), "小号2")
    assert "blk-2" in err and "blk-3" in err
    assert len(cfg["profiles"]) == 2   # 没删


def test_delete_profile_ok_when_unreferenced():
    c = _cfg()
    c["schedule"] = [{"id": "blk-1", "enabled": True, "profile": "默认"}]
    cfg, err = ga.delete_profile(c, "小号2")
    assert err is None
    assert [p["alias"] for p in cfg["profiles"]] == ["默认"]


def test_profile_dir_lookup():
    assert ga.profile_dir(_cfg(), "小号2") == "chrome-profiles/小号2"
    assert ga.profile_dir(_cfg(), "没有") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test_gui_accounts.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'gui_accounts'`

- [ ] **Step 3: 最小实现**

`gui_accounts.py`(先只纯函数;`AccountsPage` 控件 Task 9):

```python
"""账号(Chrome profile)管理: 纯数据操作 + 账号页控件.
纯函数对 cfg dict 操作, 返回 (cfg, err|None), 不写盘 —— 调用方 save_config。"""
from gui_schedule import _safe_dirname


def profile_dir(cfg, alias):
    for p in cfg.get("profiles", []):
        if p["alias"] == alias:
            return p["dir"]
    return None


def _aliases(cfg):
    return [p["alias"] for p in cfg.get("profiles", [])]


def add_profile(cfg, alias):
    alias = (alias or "").strip()
    if not alias:
        return cfg, "账号名不能为空"
    if not _safe_dirname(alias):
        return cfg, "账号名里没有可用作目录名的字符"
    if alias in _aliases(cfg):
        return cfg, f"账号名『{alias}』已存在"
    cfg["profiles"].append({"alias": alias, "dir": f"chrome-profiles/{alias}"})
    return cfg, None


def rename_profile(cfg, old, new):
    new = (new or "").strip()
    if not new:
        return cfg, "新名字不能为空"
    if not _safe_dirname(new):
        return cfg, "新名字里没有可用作目录名的字符"
    if new == old:
        return cfg, None
    if new in _aliases(cfg):
        return cfg, f"账号名『{new}』已存在"
    for p in cfg["profiles"]:
        if p["alias"] == old:
            p["alias"] = new
            p["dir"] = f"chrome-profiles/{new}"
            break
    else:
        return cfg, f"没有账号『{old}』"
    for b in cfg.get("schedule", []):
        if b.get("profile") == old:
            b["profile"] = new
    return cfg, None


def delete_profile(cfg, alias):
    used = [b.get("id") for b in cfg.get("schedule", []) if b.get("profile") == alias]
    if used:
        return cfg, f"时块 {', '.join(used)} 还在用『{alias}』, 先改掉那些时块的账号"
    cfg["profiles"] = [p for p in cfg["profiles"] if p["alias"] != alias]
    return cfg, None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest test_gui_accounts.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `pytest -q`

- [ ] **Step 6: Commit**

```bash
git add gui_accounts.py test_gui_accounts.py
git commit -m "feat(gui_accounts): pure profile ops — add/rename(sync refs)/delete(guard)"
```

---

## Task 8: `gui_chrome_flow` 重写为非模态 `LoginGuide`

**Files:**
- Rewrite: `gui_chrome_flow.py`
- Rewrite: `test_gui_chrome_flow.py`

**Interfaces:**
- Consumes(Task 4): `cdp_bridge.launch_chrome_for_profile`, `cdp_bridge.wait_for_florr_tab`
- Produces:
  - `class LoginGuide` —— 挂在主窗口一块 `CTkFrame`(引导区)上的控制器。不弹 `Toplevel`,不设 `-topmost`。
    - `__init__(self, host_frame, *, after, launch=cdp_bridge.launch_chrome_for_profile, poll=cdp_bridge.wait_for_florr_tab)` —— `after` = `widget.after`(可注入假的);`launch`/`poll` 可注入
    - `start(self, profile_dir, *, on_done, on_cancel)` —— 调 `launch(profile_dir, open_url="https://florr.io", fullscreen=False)`,显示引导区,`after(2000, self._poll_once)` 开始轮询
    - `_poll_once(self)` —— `poll(1)` 非 None → `self._detected = True` + 点亮「完成」;否则 `after(2000, self._poll_once)`
    - `finish(self)` / `cancel(self)` —— 收起引导区,调 `on_done` / `on_cancel`;取消未决的 poll
  - 删除:`ensure_chrome_ready`、`_default_confirm`、`_default_prompt_retry`、`ChromeSetupCancelled`

- [ ] **Step 1: 写失败测试**

**整个替换** `test_gui_chrome_flow.py`:

```python
import gui_chrome_flow as flow


class FakeAfter:
    """把 widget.after(ms, fn) 收集起来, 手动 flush —— 不进 tk 主循环。"""
    def __init__(self):
        self.calls = []

    def __call__(self, ms, fn=None, *a):
        if fn is not None:
            self.calls.append((fn, a))
        return len(self.calls)

    def flush(self):
        pending, self.calls = self.calls, []
        for fn, a in pending:
            fn(*a)


class FakeHost:
    """冒充引导区 CTkFrame: 只记录 show/hide。"""
    def __init__(self):
        self.visible = False

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


def _guide(tab_results, after):
    launched = []
    it = iter(tab_results)

    def launch(profile_dir, **kw):
        launched.append((profile_dir, kw))

    def poll(timeout):
        try:
            return next(it)
        except StopIteration:
            return None

    host = FakeHost()
    g = flow.LoginGuide(host, after=after, launch=launch, poll=poll)
    g._launched = launched
    return g, host


def test_start_launches_windowed_florr_and_shows_host():
    after = FakeAfter()
    g, host = _guide([None], after)
    g.start("chrome-profiles/小号2", on_done=lambda: None, on_cancel=lambda: None)
    assert g._launched[0][0] == "chrome-profiles/小号2"
    assert g._launched[0][1]["fullscreen"] is False
    assert g._launched[0][1]["open_url"] == "https://florr.io"
    assert host.visible is True


def test_poll_until_tab_then_finish_calls_on_done():
    after = FakeAfter()
    g, host = _guide([None, None, {"url": "https://florr.io/"}], after)
    done = []
    g.start("d", on_done=lambda: done.append(1), on_cancel=lambda: None)
    after.flush()   # poll #1 -> None -> reschedule
    after.flush()   # poll #2 -> None -> reschedule
    after.flush()   # poll #3 -> tab found
    assert g._detected is True
    g.finish()
    assert done == [1]
    assert host.visible is False


def test_cancel_hides_and_calls_on_cancel_and_stops_polling():
    after = FakeAfter()
    g, host = _guide([None], after)
    cancelled = []
    g.start("d", on_done=lambda: None, on_cancel=lambda: cancelled.append(1))
    g.cancel()
    assert cancelled == [1]
    assert host.visible is False
    after.flush()   # 已取消 —— 不该再有 poll 回调执行(不抛即可)


def test_manual_finish_before_detection_still_works():
    after = FakeAfter()
    g, host = _guide([None], after)
    done = []
    g.start("d", on_done=lambda: done.append(1), on_cancel=lambda: None)
    g.finish()      # 用户没等检测就手点「完成」
    assert done == [1]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test_gui_chrome_flow.py -q`
Expected: FAIL —— `AttributeError: module 'gui_chrome_flow' has no attribute 'LoginGuide'`

- [ ] **Step 3: 最小实现**

**整个替换** `gui_chrome_flow.py`:

```python
"""登录引导 —— 非模态版. 主窗口一块引导区 CTkFrame(host)由本控制器 show/hide.
不弹 Toplevel、不设 -topmost、不 grab_set: 登录期间用户能最小化窗口去干别的。

只有用户主动「新建账号 / 重新登录」才走这个流程。调度器换账号时不用它
(那条路零人工: 直接 launch_chrome_for_profile(..., fullscreen=True) + wait_for_florr_tab,
超时就跳过时块)。
"""
import cdp_bridge

_POLL_MS = 2000


class LoginGuide:
    def __init__(self, host, *, after, launch=cdp_bridge.launch_chrome_for_profile,
                 poll=cdp_bridge.wait_for_florr_tab):
        self._host = host
        self._after = after
        self._launch = launch
        self._poll = poll
        self._on_done = None
        self._on_cancel = None
        self._detected = False
        self._active = False

    def start(self, profile_dir, *, on_done, on_cancel):
        self._on_done, self._on_cancel = on_done, on_cancel
        self._detected = False
        self._active = True
        self._launch(profile_dir, open_url="https://florr.io", fullscreen=False)
        self._host.show()
        self._after(_POLL_MS, self._poll_once)

    def _poll_once(self):
        if not self._active:
            return
        if self._poll(1) is not None:
            self._detected = True
            return
        self._after(_POLL_MS, self._poll_once)

    def finish(self):
        if not self._active:
            return
        self._active = False
        self._host.hide()
        if self._on_done:
            self._on_done()

    def cancel(self):
        if not self._active:
            return
        self._active = False
        self._host.hide()
        if self._on_cancel:
            self._on_cancel()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest test_gui_chrome_flow.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `pytest -q`
Expected: `gui_app.py` 现在 `import gui_chrome_flow` 且引用了已删除的 `ensure_chrome_ready` / `ChromeSetupCancelled` —— `test_gui_app.py` 的 import 会炸。**本步允许 `test_gui_app.py` 红**;Task 10 修 `gui_app.py`。在 commit 信息里注明。若要保持绿:本步可在 `gui_app._start_worker` 里把那两处引用先注释掉(Task 10 正式重写)。

- [ ] **Step 6: Commit**

```bash
git add gui_chrome_flow.py test_gui_chrome_flow.py
git commit -m "refactor(gui_chrome_flow): non-modal LoginGuide, drop blocking messagebox loop"
```

---

## Task 9: `gui_schedule` / `gui_accounts` 控件层 —— 编辑器 + 列表行 + 账号页 + Tooltip

**Files:**
- Modify: `gui_schedule.py`(加 `_Tooltip`、`TimeBlockEditor`、`ScheduleList`)
- Modify: `gui_accounts.py`(加 `AccountsPage`)
- Test: 无新自动化测试(tk 控件层,沿用阶段1 惯例 —— 逻辑已在 Task 6/7 覆盖)。**手动冒烟**见文末 checklist。

**Interfaces:**
- Consumes: `gui_schedule.validate_block` / `block_to_active` / `WEEKDAY_LABELS` / `_safe_dirname`;`gui_map_picker.MapPicker`;`gui_accounts.add_profile` / `rename_profile` / `delete_profile`;`gui_chrome_flow.LoginGuide`
- Produces:
  - `class _Tooltip` —— `_Tooltip(widget, text)`;`<Enter>` 后 `after(400)` 弹 `tk.Toplevel(overrideredirect=True)` 里一个 `Label`;`<Leave>` / `<ButtonPress>` → destroy
  - `class TimeBlockEditor(ctk.CTkToplevel)` —— `__init__(self, master, *, block, others, profiles, on_save, login_guide)`;字段见下;`[取消]/[保存]`,保存前 `validate_block`,失败红字不关窗
  - `class ScheduleList(ctk.CTkScrollableFrame)` —— `__init__(self, master, *, get_cfg, save_cfg, open_editor, set_readonly)`;渲染折叠行(`☑启用`·星期简写·`HH:MM–HH:MM`·`profile · map`·`✎`·`🗑`);`＋ 新增时块`;`refresh()`;`set_readonly(bool)`
  - `class AccountsPage(ctk.CTkFrame)`(在 `gui_accounts.py`)—— `__init__(self, master, *, get_cfg, save_cfg, login_guide, set_readonly)`;profile 行(别名·目录·`登录`·`改名`·`删除`)+ `＋ 新建账号`;`refresh()`;`set_readonly(bool)`

- [ ] **Step 1: `_Tooltip`**

`gui_schedule.py` 顶部加 `import tkinter as tk` / `import customtkinter as ctk`,加:

```python
class _Tooltip:
    """悬停解释. CustomTkinter 没内置, 纯 tkinter 手搓: <Enter> 后 400ms 弹一个
    无边框 Toplevel, <Leave> / 点击销毁。"""
    def __init__(self, widget, text):
        self._w = widget
        self._text = text
        self._tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._job = self._w.after(400, self._show)

    def _show(self):
        if self._tip is not None:
            return
        x = self._w.winfo_rootx() + 12
        y = self._w.winfo_rooty() + self._w.winfo_height() + 6
        self._tip = tk.Toplevel(self._w)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip, text=self._text, justify="left", wraplength=280,
                 bg="#2b2b2b", fg="#e5e5e5", relief="solid", borderwidth=1,
                 padx=8, pady=5).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _cancel(self):
        if self._job is not None:
            self._w.after_cancel(self._job)
            self._job = None
```

- [ ] **Step 2: `TimeBlockEditor`**

`gui_schedule.py` 加类。构造:

```python
class TimeBlockEditor(ctk.CTkToplevel):
    _TIP_DURATION = ("一轮在刷怪区停留多少秒。也是『刷满』判定线 —— "
                     "一条命活过这个秒数才算这轮刷满。")
    _TIP_SHORT = ("连续这么多轮没撑到刷怪时长(被秒 / 到不了区), "
                  "且『自动换服务器』开着, 就自动跳服。")

    def __init__(self, master, *, block, others, profiles, on_save, login_guide):
        super().__init__(master)
        self.title("时块")
        self.transient(master)          # 不 grab_set, 不 -topmost —— 可最小化
        self._block = dict(block)
        self._others = others
        self._profiles = list(profiles)
        self._on_save = on_save
        self._login_guide = login_guide
        self._point = tuple(block["location"]) if block.get("location") else None
        self._area = ([tuple(block["farming_area"][0]), tuple(block["farming_area"][1])]
                      if block.get("farming_area") else None)
        self._build()

    def _build(self):
        # 星期 7 勾
        self._day_vars = []
        wk = ctk.CTkFrame(self, fg_color="transparent")
        wk.pack(anchor="w", padx=12, pady=(12, 4))
        for i, lab in enumerate(WEEKDAY_LABELS):
            v = ctk.IntVar(value=1 if i in self._block.get("days", []) else 0)
            ctk.CTkCheckBox(wk, text=lab, width=44, variable=v).pack(side="left", padx=2)
            self._day_vars.append(v)
        # 起止时间
        tr = ctk.CTkFrame(self, fg_color="transparent")
        tr.pack(anchor="w", padx=12, pady=4)
        self._start_e = ctk.CTkEntry(tr, width=70, placeholder_text="09:00")
        self._start_e.insert(0, self._block.get("start", ""))
        self._start_e.pack(side="left")
        ctk.CTkLabel(tr, text=" – ").pack(side="left")
        self._end_e = ctk.CTkEntry(tr, width=70, placeholder_text="12:00")
        self._end_e.insert(0, self._block.get("end", ""))
        self._end_e.pack(side="left")
        # 账号下拉 + 新建
        vals = [p["alias"] for p in self._profiles] + ["＋ 新建…"]
        self._acct = ctk.CTkOptionMenu(self, values=vals, command=self._on_acct_pick)
        self._acct.set(self._block.get("profile", vals[0]))
        self._acct.pack(anchor="w", padx=12, pady=4)
        # 地图
        self._map = ctk.CTkOptionMenu(self, values=list(app_config._VALID_MAPS),
                                      command=self._on_map_change)
        self._map.set(self._block.get("map", "desert"))
        self._map.pack(anchor="w", padx=12, pady=4)
        # 地图选择器
        from gui_map_picker import MapPicker
        self._picker = MapPicker(self, on_point_change=self._on_point,
                                 on_area_change=self._on_area)
        self._picker.pack(fill="both", expand=True, padx=12, pady=4)
        self._picker.load_map(self._map.get())
        self._picker.set_point(self._point)
        self._picker.set_area(self._area)
        # 索敌 / 换服
        self._enemy = ctk.CTkSwitch(self, text="索敌 AI")
        if self._block.get("enemy_ai_enabled", True):
            self._enemy.select()
        self._enemy.pack(anchor="w", padx=12, pady=(6, 0))
        ctk.CTkLabel(self, text="仅沙漠", font=("", 9), text_color="gray").pack(anchor="w", padx=12)
        self._autosw = ctk.CTkSwitch(self, text="自动换服务器", command=self._sync_short_enabled)
        if self._block.get("auto_switch_server", True):
            self._autosw.select()
        self._autosw.pack(anchor="w", padx=12, pady=6)
        # 高级选项(折叠)
        self._adv_open = False
        self._adv_btn = ctk.CTkButton(self, text="▸ 高级选项", anchor="w",
                                      fg_color="transparent", command=self._toggle_adv)
        self._adv_btn.pack(anchor="w", padx=12)
        self._adv = ctk.CTkFrame(self, fg_color="transparent")
        dur_row = ctk.CTkFrame(self._adv, fg_color="transparent"); dur_row.pack(anchor="w")
        ctk.CTkLabel(dur_row, text="刷怪时长(秒)").pack(side="left")
        q1 = ctk.CTkLabel(dur_row, text=" ? ", text_color="gray"); q1.pack(side="left")
        _Tooltip(q1, self._TIP_DURATION)
        self._dur_e = ctk.CTkEntry(self._adv, width=90)
        self._dur_e.insert(0, str(self._block.get("farming_duration", 300)))
        self._dur_e.pack(anchor="w", pady=(0, 6))
        sh_row = ctk.CTkFrame(self._adv, fg_color="transparent"); sh_row.pack(anchor="w")
        ctk.CTkLabel(sh_row, text="连续短局阈值").pack(side="left")
        q2 = ctk.CTkLabel(sh_row, text=" ? ", text_color="gray"); q2.pack(side="left")
        _Tooltip(q2, self._TIP_SHORT)
        self._short_e = ctk.CTkEntry(self._adv, width=90)
        self._short_e.insert(0, str(self._block.get("consecutive_short_round_limit", 2)))
        self._short_e.pack(anchor="w")
        self._sync_short_enabled()
        # 错误行 + 按钮
        self._err = ctk.CTkLabel(self, text="", text_color="#ff5555")
        self._err.pack(anchor="w", padx=12, pady=(6, 0))
        br = ctk.CTkFrame(self, fg_color="transparent"); br.pack(anchor="e", padx=12, pady=10)
        ctk.CTkButton(br, text="取消", width=70, command=self.destroy).pack(side="left", padx=4)
        ctk.CTkButton(br, text="保存", width=70, command=self._save).pack(side="left")

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        self._adv_btn.configure(text=("▾ 高级选项" if self._adv_open else "▸ 高级选项"))
        (self._adv.pack(anchor="w", padx=12) if self._adv_open else self._adv.pack_forget())

    def _sync_short_enabled(self):
        self._short_e.configure(state=("normal" if self._autosw.get() else "disabled"))

    def _on_acct_pick(self, val):
        if val != "＋ 新建…":
            return
        dlg = ctk.CTkInputDialog(text="新账号别名:", title="新建账号")
        alias = (dlg.get_input() or "").strip()
        if not alias:
            self._acct.set(self._profiles[0]["alias"]); return
        # 交给 host(App)去建 profile + 触发登录引导; 完成回调里刷新下拉
        self.master.event_generate  # noqa — 见 App 接线: 用回调更稳
        self._request_new_profile(alias)

    def _request_new_profile(self, alias):
        # 由 App 注入: self._new_profile_cb(alias, on_ready)
        cb = getattr(self, "_new_profile_cb", None)
        if cb is None:
            self._err.configure(text="新建账号未接线"); return
        def _ready(new_alias):
            vals = [p["alias"] for p in self._profiles] + ["＋ 新建…"]
            self._acct.configure(values=vals)
            self._acct.set(new_alias)
        cb(alias, _ready)

    def _on_map_change(self, name):
        self._point = None
        self._area = None
        self._picker.load_map(name)
        self._picker.set_point(None)
        self._picker.set_area(None)

    def _on_point(self, pt):
        self._point = pt

    def _on_area(self, area):
        self._area = [tuple(area[0]), tuple(area[1])]

    def _collect(self):
        from gui_app import resolve_point_and_area   # 复用阶段1 的补全
        point, area = resolve_point_and_area(self._point, self._area)
        days = [i for i, v in enumerate(self._day_vars) if v.get()]
        blk = dict(self._block)
        blk.update(
            days=days, start=self._start_e.get().strip(), end=self._end_e.get().strip(),
            profile=self._acct.get(), map=self._map.get(),
            location=list(point) if point else None,
            farming_area=[list(area[0]), list(area[1])] if area else None,
            enemy_ai_enabled=bool(self._enemy.get()),
            auto_switch_server=bool(self._autosw.get()),
        )
        try:
            blk["farming_duration"] = int(self._dur_e.get())
            blk["consecutive_short_round_limit"] = int(self._short_e.get())
        except ValueError:
            blk["farming_duration"] = blk.get("farming_duration") or 0
            blk["consecutive_short_round_limit"] = blk.get("consecutive_short_round_limit") or 0
        return blk

    def _save(self):
        blk = self._collect()
        err = validate_block(blk, self._others)
        if err:
            self._err.configure(text=err)
            return
        self._on_save(blk)
        self.destroy()
```

- [ ] **Step 3: `ScheduleList`**

`gui_schedule.py` 加类:折叠行渲染 + `＋`。行内 `☑` 即时写 `enabled` 并 `save_cfg`。`✎` 调 `open_editor(block)`;`🗑` 确认后从 `cfg["schedule"]` 移除 + `save_cfg` + `refresh`。`set_readonly(True)` 时所有行控件 + `＋` `state=disabled`。星期简写 = `"".join(WEEKDAY_LABELS[d] for d in block["days"])`。

```python
class ScheduleList(ctk.CTkScrollableFrame):
    def __init__(self, master, *, get_cfg, save_cfg, open_editor):
        super().__init__(master)
        self._get_cfg = get_cfg
        self._save_cfg = save_cfg
        self._open_editor = open_editor
        self._readonly = False
        self._add_btn = None
        self.refresh()

    def set_readonly(self, ro):
        self._readonly = ro
        self.refresh()

    def refresh(self):
        for w in list(self.winfo_children()):
            w.destroy()
        st = "disabled" if self._readonly else "normal"
        for blk in self._get_cfg()["schedule"]:
            row = ctk.CTkFrame(self)
            row.pack(fill="x", pady=3)
            ev = ctk.IntVar(value=1 if blk["enabled"] else 0)
            cb = ctk.CTkCheckBox(row, text="", width=28, variable=ev, state=st,
                                 command=lambda b=blk, v=ev: self._toggle(b, v))
            cb.pack(side="left", padx=4)
            days = "".join(WEEKDAY_LABELS[d] for d in blk["days"])
            ctk.CTkLabel(row, text=f"{days}  {blk['start']}–{blk['end']}  "
                                   f"{blk['profile']} · {blk['map']}", anchor="w").pack(
                side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="✎", width=32, state=st,
                          command=lambda b=blk: self._open_editor(b)).pack(side="left", padx=2)
            ctk.CTkButton(row, text="🗑", width=32, state=st, fg_color="#8a2b2b",
                          command=lambda b=blk: self._delete(b)).pack(side="left", padx=2)
        self._add_btn = ctk.CTkButton(self, text="＋ 新增时块", state=st,
                                      command=lambda: self._open_editor(None))
        self._add_btn.pack(fill="x", pady=(8, 2))

    def _toggle(self, blk, var):
        cfg = self._get_cfg()
        for b in cfg["schedule"]:
            if b["id"] == blk["id"]:
                b["enabled"] = bool(var.get())
        self._save_cfg(cfg)

    def _delete(self, blk):
        from tkinter import messagebox
        if not messagebox.askyesno("删除时块", f"删掉 {blk['start']}–{blk['end']} 这个时块?",
                                   parent=self):
            return
        cfg = self._get_cfg()
        cfg["schedule"] = [b for b in cfg["schedule"] if b["id"] != blk["id"]]
        self._save_cfg(cfg)
        self.refresh()
```

- [ ] **Step 4: `AccountsPage`**

`gui_accounts.py` 加 `import customtkinter as ctk` + 类。行:别名 · 目录(灰字)· `[登录]`(调 `login_guide.start(dir, on_done, on_cancel)`)· `[改名]`(`CTkInputDialog` → `rename_profile` → `os.rename` best-effort → `save_cfg` → `refresh`)· `[删除]`(`delete_profile`;`err` 非 None 弹 `messagebox.showwarning`)。`＋ 新建账号` → `CTkInputDialog` → `add_profile` → `os.makedirs(dir, exist_ok=True)` → `save_cfg` → `login_guide.start(...)` → `refresh`。`set_readonly(True)` 全禁用。

```python
class AccountsPage(ctk.CTkFrame):
    def __init__(self, master, *, get_cfg, save_cfg, login_guide):
        super().__init__(master)
        self._get_cfg = get_cfg
        self._save_cfg = save_cfg
        self._login = login_guide
        self._readonly = False
        self.refresh()

    def set_readonly(self, ro):
        self._readonly = ro
        self.refresh()

    def refresh(self):
        for w in list(self.winfo_children()):
            w.destroy()
        st = "disabled" if self._readonly else "normal"
        for p in self._get_cfg()["profiles"]:
            row = ctk.CTkFrame(self)
            row.pack(fill="x", pady=3, padx=4)
            ctk.CTkLabel(row, text=p["alias"], width=90, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=p["dir"], text_color="gray", anchor="w").pack(
                side="left", fill="x", expand=True)
            for txt, fn in (("登录", self._login_profile), ("改名", self._rename),
                            ("删除", self._delete)):
                ctk.CTkButton(row, text=txt, width=52, state=st,
                              command=lambda f=fn, a=p["alias"]: f(a)).pack(side="left", padx=2)
        ctk.CTkButton(self, text="＋ 新建账号", state=st, command=self._new).pack(
            fill="x", pady=(8, 2), padx=4)

    # _login_profile / _rename / _delete / _new: 见接线细节, 逻辑委托给纯函数 + login_guide
```

- [ ] **Step 5: 全量回归 + 语法自检**

Run: `python -c "import gui_schedule, gui_accounts"`（本机装了 tk;确认 import 不炸）
Run: `pytest -q`
Expected: 已有测试不受影响(纯函数没动),全绿(除 `test_gui_app.py`,Task 10 修)。

- [ ] **Step 6: Commit**

```bash
git add gui_schedule.py gui_accounts.py
git commit -m "feat(gui): TimeBlockEditor + ScheduleList + AccountsPage + _Tooltip"
```

---

## Task 10: `gui_app` —— 侧栏改名 + 接页 + 删旧引导 + `plan_transition` 纯函数

**Files:**
- Modify: `gui_app.py`
- Modify: `test_gui_app.py`

**Interfaces:**
- Consumes: `gui_schedule.ScheduleList` / `TimeBlockEditor` / `block_to_active`;`gui_accounts.AccountsPage`;`gui_chrome_flow.LoginGuide`;`app_config.active_block` / `next_start`
- Produces:
  - `plan_transition(running_id: str | None, new_block: dict | None, chrome_profile: str | None) -> dict`
    - `new_block is None` → `{"action": "noop"}`(本来就空档)或 `{"action": "idle"}`(要从跑着变空档)
    - `new_block["id"] == running_id` → `{"action": "noop"}`
    - 否则 `{"action": "run", "relaunch_chrome": new_block["profile"] != chrome_profile, "profile": new_block["profile"]}`
  - `App` 里 `_start_worker` 删掉 `-topmost` / 切全屏 `messagebox` / `gui_chrome_flow.ensure_chrome_ready` / `ChromeSetupCancelled` 引用
  - 保留:`worker_command` / `parse_positive_ints` / `resolve_point_and_area` / `_DERIVED_AREA_HALF` / `start_afk` / `_clamp_px` / `_MAP_PX`(编辑器与调度都还用)
  - 删除:`build_worker_config`(被 `gui_schedule.block_to_active` 取代)—— 同步删 `test_gui_app.py::test_build_worker_config_shapes_values`

- [ ] **Step 1: 写失败测试**

`test_gui_app.py`:删 `test_build_worker_config_shapes_values`;加:

```python
def test_plan_transition_noop_same_block():
    blk = {"id": "b1", "profile": "默认"}
    assert gui_app.plan_transition("b1", blk, "默认") == {"action": "noop"}


def test_plan_transition_idle_when_leaving_to_gap():
    assert gui_app.plan_transition("b1", None, "默认") == {"action": "idle"}


def test_plan_transition_noop_when_already_idle():
    assert gui_app.plan_transition(None, None, None) == {"action": "noop"}


def test_plan_transition_run_same_profile_no_relaunch():
    blk = {"id": "b2", "profile": "默认"}
    assert gui_app.plan_transition("b1", blk, "默认") == {
        "action": "run", "relaunch_chrome": False, "profile": "默认"}


def test_plan_transition_run_other_profile_relaunches():
    blk = {"id": "b2", "profile": "小号2"}
    assert gui_app.plan_transition("b1", blk, "默认") == {
        "action": "run", "relaunch_chrome": True, "profile": "小号2"}


def test_plan_transition_run_from_idle_after_worker_crash():
    blk = {"id": "b1", "profile": "默认"}
    # worker 崩了 -> _running_block_id 清成 None, chrome 还在『默认』
    assert gui_app.plan_transition(None, blk, "默认") == {
        "action": "run", "relaunch_chrome": False, "profile": "默认"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test_gui_app.py -q`
Expected: FAIL —— import 阶段就炸(`gui_chrome_flow.ensure_chrome_ready` 没了)或 `plan_transition` 未定义

- [ ] **Step 3: 最小实现**

`gui_app.py`:

1. 顶部 `import`:`import gui_chrome_flow` 保留;去掉对 `ensure_chrome_ready` / `ChromeSetupCancelled` 的用法。加 `import gui_schedule`、`import gui_accounts`。
2. 删 `build_worker_config` 函数。
3. 加模块级:

```python
def plan_transition(running_id, new_block, chrome_profile):
    """给定当前在跑的时块 id + 此刻命中的时块 + 当前 Chrome 用的 profile,
    算出调度该干什么。纯函数, 无 I/O。"""
    if new_block is None:
        return {"action": "idle"} if running_id is not None else {"action": "noop"}
    if new_block["id"] == running_id:
        return {"action": "noop"}
    return {
        "action": "run",
        "relaunch_chrome": new_block["profile"] != chrome_profile,
        "profile": new_block["profile"],
    }
```

4. `App.__init__`:侧栏 3 按钮 `("控制台","账号","时间表")` → `("时间表","账号")`。`控制台` 页的地图选择器/右栏那一大坨换成:
   - `self._sched_list = gui_schedule.ScheduleList(content, get_cfg=self._get_cfg, save_cfg=self._save_cfg, open_editor=self._open_editor)`
   - `self._accounts = gui_accounts.AccountsPage(content, get_cfg=self._get_cfg, save_cfg=self._save_cfg, login_guide=self._login_guide)`
   - `_show_page` 真正切换这两个 frame 的 `grid`/`grid_remove`
   - 引导区:`self._guide_frame = ctk.CTkFrame(...)`;`self._login_guide = gui_chrome_flow.LoginGuide(_GuideHost(self._guide_frame, ...), after=self.after)`(`_GuideHost` 包 show=`grid()` / hide=`grid_remove()` + 三行文字 + 完成/取消按钮 —— 完成按钮 `command=self._login_guide.finish`)
   - 日志框 `self.log_box` / 状态行 / `▶ 开始调度` 按钮保留
5. `_get_cfg` → `return self._cfg`;`_save_cfg(cfg)` → `app_config.save_config(cfg); self._cfg = app_config.load_config()`。
6. `_open_editor(block)`:调度运行中直接 `return`。`others = [b for b in self._cfg["schedule"] if b is not block]`;`ed = gui_schedule.TimeBlockEditor(self, block=block or _new_block_template(self._cfg), others=others, profiles=self._cfg["profiles"], on_save=self._save_block, login_guide=self._login_guide)`;注入 `ed._new_profile_cb = self._make_profile_then`.
7. `_new_block_template(cfg)`:`{"id": _fresh_block_id(cfg), "enabled": True, "days": [], "start": "09:00", "end": "12:00", "profile": cfg["profiles"][0]["alias"], "map": "desert", "location": None, "farming_area": None, "farming_duration": 300, "consecutive_short_round_limit": 2, "enemy_ai_enabled": True, "auto_switch_server": True}`。`_fresh_block_id`:`f"blk-{max 已有编号 + 1}"`。
8. `_save_block(blk)`:替换 `self._cfg["schedule"]` 里同 id 的,没有就 append;`self._save_cfg`;`self._sched_list.refresh()`。
9. `_start_worker` → 拆成调度启停:
   - `_on_start_stop`:`self._sched_running` 取反。开:`schedule` 空 → 日志「先加时块」return;`self._sched_running=True`;锁编辑(`self._sched_list.set_readonly(True)`、`self._accounts.set_readonly(True)`);按钮「■ 停止调度」;`self._sched_tick()`。停:`self._sched_running=False`;`self._stop_worker()`;取消 `self._tick_job`;解锁;按钮「▶ 开始调度」。
   - `_sched_tick`:
     ```python
     import time
     lt = time.localtime()
     weekday = lt.tm_wday                    # Python: 周一=0 —— 跟我们编号一致
     hhmm = "%02d:%02d" % (lt.tm_hour, lt.tm_min)
     blk = app_config.active_block(self._cfg["schedule"], weekday, hhmm)
     plan = plan_transition(self._running_block_id, blk, self._chrome_profile)
     if plan["action"] == "idle":
         self._stop_worker(); self._running_block_id = None
         self._log_line("⏸ 空档, worker 已停\n")
     elif plan["action"] == "run":
         self._enter_block(blk, plan["relaunch_chrome"])
     self._update_status(blk, weekday, hhmm)
     if self._sched_running:
         self._tick_job = self.after(30_000, self._sched_tick)
     ```
   - `_enter_block(blk, relaunch)`:
     ```python
     self._stop_worker()
     if relaunch:
         pdir = gui_accounts.profile_dir(self._cfg, blk["profile"])
         if pdir is None:
             self._log_line(f"⚠️ 账号『{blk['profile']}』不存在, 跳过时块 {blk['id']}\n")
             self._running_block_id = None; return
         try:
             cdp_bridge.launch_chrome_for_profile(_abs_profile(pdir), fullscreen=True)
         except RuntimeError as e:
             self._log_line(f"⚠️ 起 Chrome 失败: {e}, 跳过时块 {blk['id']}\n")
             self._running_block_id = None; return
         if cdp_bridge.wait_for_florr_tab(30) is None:
             self._log_line(f"⚠️ 账号『{blk['profile']}』未登录 / florr.io 没起来, "
                            f"跳过时块 {blk['id']}\n")
             self._running_block_id = None; return
         self._chrome_profile = blk["profile"]
     self._cfg["active"] = gui_schedule.block_to_active(blk)
     app_config.save_config(self._cfg)
     self._spawn_worker()               # = 阶段1 _start_worker 里 Popen + reader 那段
     self._running_block_id = blk["id"]
     self._log_line(f"▶ 进入时块 {blk['id']}({blk['profile']} / {blk['map']}) "
                    f"{blk['start']}–{blk['end']}\n")
     ```
   - `_abs_profile(rel)`:`rel` 若相对则拼到 `sys.argv[0]` 目录下。
   - `_spawn_worker()`:把阶段1 `_start_worker` 从 `kwargs = {...}` 到 `self._reader.start()` 那段搬进来(Popen + PYTHONUNBUFFERED/IOENCODING + stdin=PIPE + reader 线程),不带任何 `messagebox` / `-topmost`。
   - `_on_worker_exit`:末尾加 `if self._sched_running: self._running_block_id = None`(下次 tick 自动重进当前时块 = 崩溃自愈)。
   - `_update_status(blk, weekday, hhmm)`:`nb = app_config.next_start(self._cfg["schedule"], weekday, hhmm)`;拼「当前:blk-1(默认/desert) · 下一个:周三 09:00」/「空档 · 下一个 …」。
10. `App.__init__` 里新增实例属性:`self._sched_running = False`、`self._running_block_id = None`、`self._chrome_profile = None`、`self._tick_job = None`。
11. `_make_profile_then(alias, on_ready)`:`cfg, err = gui_accounts.add_profile(self._cfg, alias)`;`err` → 日志 + return;`os.makedirs(_abs_profile(...), exist_ok=True)`;`self._save_cfg(cfg)`;`self._login_guide.start(_abs_profile(dir), on_done=lambda: on_ready(alias), on_cancel=lambda: None)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest test_gui_app.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `pytest -q`
Expected: 全绿。

- [ ] **Step 6: 手动冒烟(本机 Mac,有显示器)**

```bash
python main.py
```
- [ ] 侧栏只有「时间表 / 账号」两页,底部 AFK 开关在(Mac 上灰,带「仅 Windows」)
- [ ] 「＋ 新增时块」弹编辑器,窗口能最小化、不置顶
- [ ] 编辑器:勾星期、填 `09:00`/`12:00`、选地图、点目标点画十字、拖框画矩形、`?` 悬停出解释、关「自动换服务器」→ 短局阈值输入框变灰
- [ ] 存一个时块 → 列表出现折叠行;再存一个时间重叠的 → 编辑器红字拦下
- [ ] 行内 `☑` 取消 → `config.json` 里该块 `enabled:false`
- [ ] 账号页:新建「测试号」→ 弹登录引导区(Chrome 开 florr.io,非模态),点「取消」收起
- [ ] 账号页:改名「测试号」→「测试号b」→ 引用它的时块 `profile` 跟着变;删一个没被引用的 → 成功,被引用的 → 弹拦截提示
- [ ] 点「▶ 开始调度」→ 列表 + 账号页变灰只读;状态行显示「当前 … / 下一个 …」;点「■ 停止调度」→ 解锁

- [ ] **Step 7: Commit**

```bash
git add gui_app.py test_gui_app.py
git commit -m "feat(gui_app): weekly scheduler state machine + schedule/accounts pages, drop modal chrome flow"
```

---

## Task 11: `gui_app` 接线收尾 —— 账号页动作 + 引导区宿主 + 启动恢复

**Files:**
- Modify: `gui_app.py`, `gui_accounts.py`

**Interfaces:**
- Consumes: 上文所有
- Produces:
  - `gui_accounts.AccountsPage` 的 `_login_profile` / `_rename` / `_delete` / `_new` 实体(委托纯函数 + `login_guide` + `os.rename`/`os.makedirs` best-effort + `messagebox` 报错)
  - `gui_app` 里 `_GuideHost`(包 `CTkFrame`:`show`=`grid()` + 起轮询文案、`hide`=`grid_remove()`)
  - `App` 启动时:`afk_enabled` 恢复逻辑(阶段1 已有)保持;**不**自动开始调度(用户手动点)

- [ ] **Step 1: `gui_accounts` 动作实体**

`AccountsPage` 补齐(逻辑全部走 Task 7 纯函数):

```python
    def _login_profile(self, alias):
        d = _abs(self._get_cfg(), alias)
        if d is None:
            return
        self._login.start(d, on_done=self.refresh, on_cancel=lambda: None)

    def _rename(self, alias):
        dlg = ctk.CTkInputDialog(text=f"把『{alias}』改成:", title="改名")
        new = (dlg.get_input() or "").strip()
        if not new:
            return
        cfg = self._get_cfg()
        old_dir = _abs_path(cfg, alias)
        cfg, err = rename_profile(cfg, alias, new)
        if err:
            _warn(err); return
        try:
            if old_dir and os.path.isdir(old_dir):
                os.rename(old_dir, os.path.join(os.path.dirname(old_dir), new))
        except OSError:
            _warn("目录改名失败(可能该账号的 Chrome 还开着), 配置已改、目录名没改")
        self._save_cfg(cfg)
        self.refresh()

    def _delete(self, alias):
        cfg, err = delete_profile(self._get_cfg(), alias)
        if err:
            _warn(err); return
        self._save_cfg(cfg)
        self.refresh()

    def _new(self):
        dlg = ctk.CTkInputDialog(text="新账号别名:", title="新建账号")
        alias = (dlg.get_input() or "").strip()
        if not alias:
            return
        cb = getattr(self, "new_profile_cb", None)
        if cb:
            cb(alias, lambda *_: self.refresh())
```

(`_abs` / `_abs_path` / `_warn` 小助手:`_warn` = `from tkinter import messagebox; messagebox.showwarning("账号", msg)`;`_abs_path(cfg, alias)` = `profile_dir` 拼 `sys.argv[0]` 目录。)`App` 建 `AccountsPage` 后注入 `self._accounts.new_profile_cb = self._make_profile_then`。

- [ ] **Step 2: `_GuideHost`**

`gui_app.py`:

```python
class _GuideHost:
    """把主窗口里一块 CTkFrame 包成 LoginGuide 要的 show()/hide() 接口。"""
    def __init__(self, frame, grid_kw):
        self._frame = frame
        self._grid_kw = grid_kw

    def show(self):
        self._frame.grid(**self._grid_kw)

    def hide(self):
        self._frame.grid_remove()
```

引导区 frame 里放三行 `CTkLabel`(① 已在 Chrome 打开 florr.io ② 在那个窗口登录你的账号 ③ 登录完点右边) + `[完成]` 按钮(`command=self._login_guide.finish`)+ `[取消]`(`command=self._login_guide.cancel`)。

- [ ] **Step 3: 全量回归**

Run: `pytest -q`
Expected: 全绿(本任务纯接线,不新增自动化测试;逻辑已被 Task 6/7/8/10 覆盖)

- [ ] **Step 4: 手动冒烟**

```bash
python main.py
```
- [ ] 账号页三个按钮(登录/改名/删除)都不炸;新建 → 登录引导 → 完成后下拉/列表刷新
- [ ] `config.json` 里出现 `version:2` / `profiles` / `schedule` / `active`

- [ ] **Step 5: Commit**

```bash
git add gui_app.py gui_accounts.py
git commit -m "feat(gui_app): wire accounts-page actions + login-guide host"
```

---

## Task 12: 文档 —— README / PACKAGING 更新,删「自备 .pt」

**Files:**
- Modify: `README.md`, `PACKAGING.md`

**Interfaces:** 无代码

- [ ] **Step 1: `README.md`**

- 把描述 GUI 的段落改成:「双击 = 控制面板。`时间表` 页按星期几 + 时间段配时块(账号 / 地图 / 目标点 / 刷怪区 / 索敌 / 换服),`账号` 页管理 Chrome profile。点『开始调度』后按周计划自动切账号、切图、起停。」
- 索敌那段(line ~32-39, 57):删掉 `models/desert.pt` / `.pt` 安全警告整段,改成:「索敌 AI 现在解码游戏 canvas 的绘制调用识别怪物(不需要模型文件),默认开启,目前只对沙漠生效。」
- 加一句 `config.json` 在 exe 同级、格式 v2、`chrome-profiles/<别名>/` 存各账号登录态。

- [ ] **Step 2: `PACKAGING.md`**

- line ~21, 25-28, 33, 50, 85:删所有 `torch` / `desert.pt` / `sandstorm.pt` / YOLO 相关行。
- line 50 的功能清单项改成:「时间表调度 + 账号管理(`chrome-profiles/` 运行时生成,不进包)」。
- 确认没有别的地方还写「dist ~1GB dominated by torch」(canvas decode + drop-torch 之后已经不是了)。

- [ ] **Step 3: 检查**

Run: `grep -rn "desert.pt\|sandstorm.pt\|ultralytics\|自备.*权重\|YOLO 模型" README.md PACKAGING.md`
Expected: 无输出

- [ ] **Step 4: Commit**

```bash
git add README.md PACKAGING.md
git commit -m "docs: GUI phase 2 (schedule + accounts), drop stale .pt/torch instructions"
```

---

## Task 13: 收尾清理 —— 删孤儿 .pt + bilibili 脚本 + 残留 YOLO 注释

**Files:**
- Delete: `models/desert.pt`, `models/sandstorm.pt`
- Modify: `docs/bilibili/视频1-演示片-脚本与分镜.md`, `docs/bilibili/视频2-安装教程-脚本与分镜.md`, `docs/bilibili/视频开头-配音稿.md`
- Modify(低优先,纯注释): `enemy_detect.py`, `main.py`

- [ ] **Step 1: 删 .pt**

```bash
git rm models/desert.pt models/sandstorm.pt
```
`models/` 目录若空了就留个 `.gitkeep` 或删掉整目录(确认没有代码 `os.path.join(..., "models", ...)` 硬引用 —— `grep -rn "models/" *.py` 只应命中 `afk_watch.py` 的 `afk-seg.pt` / `afk-det.pt`,那是 florr-auto-afk 的,别动)。

- [ ] **Step 2: bilibili 脚本**

- `视频1`:第 6 镜(1:30–1:55)「YOLO 模型认怪 / 需自备模型文件」→「解码游戏画面认怪,按稀有度追或躲,不用额外文件」。删 line 51 的「需要 `models/desert.pt`」制作注记。
- `视频2`:第 4 镜(2:00–2:40)整段「可选:放 `desert.pt`」删掉,分镜表顺延;line 69 的制作注记删。
- `视频开头`:line 28 把 `YOLO` 从「不要出现的技术名词」清单里留着没关系(它本就说不出现),不用改;若提到模型文件则删。
- 加一处(视频1 或视频2 合适的镜)提「新版是按星期几的时间表调度 + 多账号」——可选,不强求。

- [ ] **Step 3: 残留 YOLO 注释(低优先)**

`enemy_detect.py` line 286 / 360、`main.py` line 15 / 25 / 537:把注释里的「YOLO」改成「canvas 解码」/「索敌」。**不改任何代码逻辑、变量名、常量名**(`CHASE_MIN_CONF` 等留着 —— 换名会牵动一堆调用点,超出本 spec)。

- [ ] **Step 4: 检查 + 回归**

Run: `grep -rn "YOLO" *.py`
Expected: 无输出(或只剩明确判断不值得动的)

Run: `pytest -q`
Expected: 全绿(没动代码逻辑)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: rm orphan .pt weights, dewYOLO bilibili scripts + comments"
```

---

## Self-Review

**1. Spec coverage:**

| Spec 要求 | 对应 Task |
|---|---|
| `config.json` v2(`profiles`/`schedule`/`active`/`afk_enabled`) | Task 2 |
| `_coerce` v2(坏块丢弃 / 悬空 profile 禁用 / profiles 补默认 / 别名去重) | Task 2 |
| v1→v2 迁移(单「默认」profile + 全周全天时块 + `chrome-profile` 改名 + 写回) | Task 3 |
| `_valid_time` / `expand_block_days` / `blocks_overlap` / `active_block` | Task 1 |
| 状态行「下一个时块」 | Task 1(`next_start`)+ Task 10(`_update_status`) |
| 进程模型 + 调度状态机(tick / 转移 / 空档 / 崩溃自愈) | Task 10(`plan_transition` + `_sched_tick` + `_enter_block` + `_on_worker_exit`) |
| 换 profile 关开 Chrome + 未登录跳过 | Task 4(`launch_chrome_for_profile`)+ Task 10(`_enter_block`) |
| `--start-fullscreen` + `https://florr.io` + 零人工 | Task 4 + Task 10 |
| 主界面:侧栏 2 页 + 时块列表 + 折叠行 + ＋ | Task 9(`ScheduleList`)+ Task 10(接线) |
| 时块编辑器(星期 7 勾 / 时间 / 账号下拉带新建 / 地图 / MapPicker / 索敌 / 换服 / 高级折叠 / 校验拦重叠) | Task 9(`TimeBlockEditor`)+ Task 6(`validate_block`) |
| 刷怪时长 / 短局阈值 `?` tooltip + 文案 | Task 9(`_Tooltip` + `_TIP_*`) |
| 自动换服务器关 → 短局阈值禁用 | Task 9(`_sync_short_enabled`) |
| 账号页(新建 / 登录 / 改名同步时块 / 删除拦引用) | Task 7(纯函数)+ Task 9/11(`AccountsPage`) |
| profile 目录 `chrome-profiles/<别名>/` + `_safe_dirname` | Task 6(`_safe_dirname`)+ Task 7(`add_profile`) |
| 登录引导非模态、不置顶、可最小化、自动开 florr.io、轮询点亮完成 | Task 8(`LoginGuide`)+ Task 11(`_GuideHost`) |
| 调度运行中锁编辑 | Task 9(`set_readonly`)+ Task 10(`_on_start_stop`) |
| worker 读 `cfg["active"]` + 回退链 | Task 5 |
| 索敌默认开 + 去 YOLO 文案 | Task 2(`DEFAULTS` 已 `False`→ 注意:spec 要默认**开**,见下)+ Task 9(开关默认 `select`)+ Task 12 |
| README / PACKAGING 更新 + 删 .pt 段 | Task 12 |
| 删孤儿 .pt + bilibili + 注释 | Task 13 |

**修正:索敌默认值** —— spec 要「默认开」。阶段1 `DEFAULTS["enemy_ai_enabled"] = False`。迁移(Task 3)用的是**用户 v1 文件里的实际值**,不该强行翻成 True(用户没开就是没开)。但**新建时块**默认开:Task 9 `_new_block_template` 里 `enemy_ai_enabled=True`、编辑器 `_enemy.select()` 默认。`DEFAULTS_V2["active"]` 从 `DEFAULTS` 取仍是 `False` —— 这只在「全新装 + 空 schedule」时当占位,worker 不会真跑(没时块)。**结论:不动 `DEFAULTS`;新建路径默认开。** Task 2 的 `DEFAULTS_V2` 注释里点明这一点。

**2. Placeholder scan:** 无 "TBD"/"TODO"/"待补"。Task 9/10/11 的控件代码给了完整可粘贴实现;接线细节(`_abs` 等小助手)在 Task 11 明确。

**3. Type consistency:**
- `plan_transition` 返回 `{"action","relaunch_chrome","profile"}` —— Task 10 定义、`_sched_tick` 消费,一致。
- `validate_block(block, others) -> str|None` —— Task 6 定义,Task 9 `TimeBlockEditor._save` 消费,一致。
- `block_to_active` 在 `gui_schedule`(Task 6),Task 10 `_enter_block` 用 `gui_schedule.block_to_active`,一致(不是 `gui_app.build_worker_config` —— 那个已删)。
- `add_profile`/`rename_profile`/`delete_profile` 都 `(cfg, err|None)` —— Task 7 定义,Task 9/11 消费,一致。
- `profile_dir` 在 `gui_accounts`(Task 7),Task 10 `_enter_block` 用 `gui_accounts.profile_dir`,一致。
- `LoginGuide.start(profile_dir, *, on_done, on_cancel)` + `.finish()` + `.cancel()` —— Task 8 定义,Task 10/11 调,一致。
- `_CHROME_PROFILE_DIR` 改值(Task 4)后 `cdp_bridge` 内部 + `app_config` 迁移目标 `chrome-profiles/默认` 一致。
- 星期编号 0=周一:`_sched_tick` 用 `time.localtime().tm_wday`(周一=0)—— 跟 `expand_block_days` / `WEEKDAY_LABELS` 一致。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-02-gui-phase2-schedule.md`. Two execution options:

1. **Subagent-Driven (recommended)** - fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - execute tasks in this session with checkpoints

Which approach?
