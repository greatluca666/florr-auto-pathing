# 时块时间输入容错 — design

## Problem

时块编辑器([gui_schedule.py](../../../gui_schedule.py) 的 `TimeBlockEditor`)两个时间输入框
(`_start_e` / `_end_e`)存盘时只 `.strip()`,校验直接怼 `app_config._valid_time`:

```python
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
```

严格到只认 `HH:MM` 零填充 24h。用户常见的合法意图全被红字 `时间格式要是 HH:MM` 顶回去:

- `9:00`(小时不补零)
- `09：00`(中文输入法全角冒号 `：` U+FF1A)
- `900` / `0930`(不打冒号)
- `9`(只填小时)
- `9.00` / `9 00`(用别的分隔符)
- `０９:００`(全角数字,同一个输入法坑)

[app_config._coerce_block](../../../app_config.py) 读 config.json 时同样用 `_valid_time`,
一个 `9:00` 会让**整块时块被丢弃**(`_coerce_block` 返回 `None`)—— 跟这份文件
「读不出来不抛、尽量兜」的既定风格不一致(见文件头 docstring)。

## Goal

加一个纯函数 `app_config.normalize_time(s) -> "HH:MM" | None`:把宽松写法规整成规范
`HH:MM`,越界 / 无法解析返回 `None`。三处接入:

1. `TimeBlockEditor._collect()` —— 存之前先 `normalize_time`,规整不了就原样传下去,
   让现有 `validate_block` 红字报错(行为不变)。
2. 两个时间框绑 `<FocusOut>` —— 失焦时把能规整的就地改写成规范形,用户在点保存前
   就看到最终值。
3. `app_config._coerce_block()` —— start/end 先过 `normalize_time` 再校验,旧文件 /
   手改文件写 `9:00` 不再整块丢。

## Non-goals

- **不放宽 `_valid_time` / `_TIME_RE` / `validate_block`。** 校验层保持严格 ——
  只判定、不变换。`normalize_time` 是独立的变换层,跑在校验之前。现有
  `test_gui_schedule.py` / `test_app_config.py` 里针对严格校验的用例不受影响。
- **不碰跨午夜 / 全天语义。** `00:00–00:00` = 全天、`起 >= 止` = 跨午夜 这些判断
  在 `validate_block` / `expand_block_days` 里,`normalize_time` 只负责把单个字段
  规整成 `HH:MM`,不看另一个字段。
- **不做 `9am` / `9:00 PM` / `下午3点` 这类含义解析。** 只认数字 + 分隔符。带字母
  的一律 `None`,交给校验层红字。
- **不加秒。** `12:00:00` → 多于一个分隔段 → `None`。
- **不改 GUI 之外的输入路径**(afk_watch 等没有时间输入框)。

## `normalize_time` 规则

输入任意字符串,按顺序:

1. **去空白**:`s.strip()` 再去全角空格 `　`。空串 → `None`。
2. **全角折 ASCII**:全角数字 `０-９`(U+FF10–FF19)→ `0-9`;全角冒号 `：`(U+FF1A)、
   全角句点 `．`(U+FF0E)、全角连字符 `－`(U+FF0D)→ 对应半角。
3. **统一分隔符**:`.` `-` 都当成 `:`。规整后串里 `:` 多于一个 → `None`。
4. **拆分**:
   - **含 `:`**:按 `:` 拆成 `h_str` / `m_str`。两段都必须非空、纯数字、各 1–2 位。
     否则 `None`。`h = int(h_str)`,`m = int(m_str)`(`9:5` → h=9 m=5 → `09:05`)。
   - **不含 `:`、纯数字**:按长度:
     | 长度 | 解释 | 例 |
     |---|---|---|
     | 1 | `H` | `9` → `09:00` |
     | 2 | `HH` | `18` → `18:00` |
     | 3 | `Hmm` | `930` → `09:30` |
     | 4 | `HHmm` | `1830` → `18:30` |
     | 其它(0 / ≥5) | `None` | `12345` → `None` |
   - **其它**(含字母等)→ `None`。
5. **范围**:`0 <= h <= 23` 且 `0 <= m <= 59`,否则 `None`。
   (`25:00`、`9:70`、`2400` 都在这里被拒。)
6. 返回 `"%02d:%02d" % (h, m)`。

### 用例表(进 `test_app_config.py`)

| 输入 | 输出 |
|---|---|
| `"09:00"` | `"09:00"` |
| `" 9:00 "` | `"09:00"` |
| `"9:5"` | `"09:05"` |
| `"09：00"`(全角冒号) | `"09:00"` |
| `"０９:００"`(全角数字) | `"09:00"` |
| `"9.00"` | `"09:00"` |
| `"9-30"` | `"09:30"` |
| `"9"` | `"09:00"` |
| `"18"` | `"18:00"` |
| `"930"` | `"09:30"` |
| `"0930"` | `"09:30"` |
| `"1830"` | `"18:30"` |
| `"0"` | `"00:00"` |
| `"2400"` | `None` |
| `"25:00"` | `None` |
| `"9:70"` | `None` |
| `"12:00:00"` | `None` |
| `"9am"` | `None` |
| `""` / `"   "` | `None` |
| `None` / `123`(非 str) | `None` |

`normalize_time(normalize_time(x))` 对任何返回非 `None` 的 `x` 幂等(输出已是规范
`HH:MM`,再跑一遍走「含 `:`、两段各 2 位、范围内」原样回来)。

## 接入 1:`TimeBlockEditor._collect()`

[gui_schedule.py](../../../gui_schedule.py) 当前:

```python
blk.update(
    days=days, start=self._start_e.get().strip(), end=self._end_e.get().strip(),
    ...
```

改成:

```python
start_raw = self._start_e.get().strip()
end_raw = self._end_e.get().strip()
blk.update(
    days=days,
    start=app_config.normalize_time(start_raw) or start_raw,
    end=app_config.normalize_time(end_raw) or end_raw,
    ...
```

`normalize_time` 返回 `None` 时落回 `*_raw`,`validate_block(blk, ...)` 照旧报
`时间格式要是 HH:MM`。`gui_schedule` 已 `import app_config`,无新 import。

## 接入 2:两个时间框 `<FocusOut>`

`TimeBlockEditor._build()` 里建完 `_start_e` / `_end_e` 后各绑一次:

```python
for e in (self._start_e, self._end_e):
    e.bind("<FocusOut>", lambda ev, w=e: self._normalize_entry(w), add="+")
```

```python
def _normalize_entry(self, entry):
    """失焦: 能规整就把输入框内容替换成规范 HH:MM; 规整不了就原样留着,
    交给保存时的 validate_block 红字."""
    raw = entry.get().strip()
    fixed = app_config.normalize_time(raw)
    if fixed is not None and fixed != entry.get():
        entry.delete(0, "end")
        entry.insert(0, fixed)
```

纯 UI 便利,不参与校验闭环(`_collect` 自己也会规整)。`add="+"` 不覆盖 CTkEntry
可能自带的 `<FocusOut>`(placeholder 逻辑)。

## 接入 3:`app_config._coerce_block()`

当前:

```python
start, end = raw.get("start"), raw.get("end")
if not (_valid_time(start) and _valid_time(end)):
    return None
if start == end and start != "00:00":
    return None
```

改成:

```python
start = normalize_time(raw.get("start"))
end = normalize_time(raw.get("end"))
if start is None or end is None:
    return None
if start == end and start != "00:00":
    return None
```

后面 `return {... "start": start, "end": end ...}` already 用局部 `start`/`end`,
自动写回规范值。`migrate_v1` 造的块 start/end 是硬编码 `"00:00"`,`normalize_time`
原样返回,不受影响。

`_valid_time` 本身**保留**(`normalize_time` 内部不复用它;别处若还有调用不动)。

## 测试

### `test_app_config.py` 扩

- `test_normalize_time` —— 上面用例表整张 `@pytest.mark.parametrize`。
- `test_normalize_time_idempotent` —— 对用例表里所有非 `None` 输出,
  `normalize_time(out) == out`。
- `test_coerce_block_normalizes_loose_time` —— 构造一份 v2 raw cfg,某时块
  `start="9:00"` / `end="1230"`,过 `load_config`(或直接 `_coerce`),断言该块
  **没被丢**且 `start == "09:00"` / `end == "12:30"`。
- `test_coerce_block_rejects_unparseable_time` —— `start="9am"` 的块仍被整块丢
  (`_coerce_schedule` 打 `第 N 个时块不合法` 警告,结果列表里没有它)。

### 现有测试

`test_gui_schedule.py` 的 `test_validate_bad_time`(`start="9am"`)、
`test_validate_equal_times`(`09:00`/`09:00`)等针对 `validate_block` 的用例
**不改** —— `validate_block` 没动。`test_app_config.py` 现有用例全绿。

`_collect()` / `_normalize_entry()` 要 tk,不单测(项目现有约定:控件层不单测,
纯函数才单测)。规整逻辑本体在 `normalize_time`,已被 `test_app_config` 覆盖。

## 改动文件

- `app_config.py` —— 加 `normalize_time()`;`_coerce_block()` 两行改用它。
- `gui_schedule.py` —— `_collect()` 规整;`_build()` 绑 `<FocusOut>`;加
  `_normalize_entry()`。
- `test_app_config.py` —— 上述 4 个测试。

无新文件,无新依赖,无打包变化。

## Self-review

- **占位符**:无 TBD / TODO。
- **一致性**:三处接入都是「先 `normalize_time`,`None` 就走原有失败路径」——
  GUI 落回 raw 让红字报错,coerce 丢块。校验层严格性不变。
- **歧义**:`9:5` 明确 = `09:05`(左补零,`int("5")`),不是 `09:50`;用例表钉死。
  `2400` 明确 = `None`(全天要写 `00:00`),用例表钉死。
- **范围**:一个纯函数 + 三处小改 + 4 个测试。单 plan 够。
