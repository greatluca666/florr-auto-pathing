# Packaging into an .exe

Build **on Windows** — PyInstaller doesn't cross-compile, so running this on
the Mac dev box produces a macOS binary, not a `.exe`. (This repo's
`main.spec` was sanity-checked on macOS to confirm the import graph resolves
cleanly; the actual `.exe` still has to come from a Windows build.)

## Steps (on the Windows machine)

```bat
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\build_windows.bat
```

(or just run `build_windows.bat` from a shell that already has the venv
active on PATH.)

This runs PyInstaller against [main.spec](main.spec) and produces
`dist\florr-auto-pathing\florr-auto-pathing.exe` (one-dir build, ~1GB —
mostly torch — not one-file, so startup stays fast and nothing has to
re-extract on every launch).

The script also copies `maps\` next to the exe. **You still have to copy
`models\desert.pt`** (and `sandstorm.pt` if used) into
`dist\florr-auto-pathing\models\` yourself — those are gitignored,
third-party YOLO weights not in this repo (see README.md for where to get
them and the warning about verifying `.pt` sources before use).

## Why not bundle maps/models via PyInstaller `--add-data`

`utils.py`/`enemy_detect.py` open `maps/…` and `models/…` as paths relative
to the process's cwd (`./maps/desert.png`, `models/desert.pt`). For a
double-clicked exe, cwd is the exe's own folder — but PyInstaller's
`--add-data` extracts into its internal `_internal\` subfolder (default
since PyInstaller 6.0), not the top-level dist folder, so a relative
`./maps` lookup wouldn't find it. Copying `maps\` next to the exe after the
build keeps the exact same relative-path behavior the unpackaged
`python main.py` already relies on, with no code changes.

## Running the built exe

Same as running `main.py` unpackaged: open florr.io in Chrome, fullscreen,
tab on top, then double-click `florr-auto-pathing.exe` (or run it from a
terminal if you want to see the round-by-round console output, which the
build keeps — it's a console app, not windowed).

## Distributing it

The dist folder is large (~1GB, dominated by torch) and self-contained
(bundles its own Python + all deps) — anyone running it needs no Python
install. Zip `dist\florr-auto-pathing\` as a whole; the exe will not run
correctly if separated from its `_internal\` folder.
