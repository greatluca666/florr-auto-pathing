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

Don't pre-open or pre-arrange anything in Chrome first — just double-click
`florr-auto-pathing.exe` (or run it from a terminal if you want to see the
round-by-round console output, which the build keeps — it's a console app,
not windowed). The exe's bootstrap takes care of Chrome itself:

1. It prints a warning and waits for Enter before force-closing all existing
   Chrome windows (any pre-opened florr.io tab gets killed along with
   everything else, so there's no point opening it beforehand).
2. It launches a dedicated Chrome window of its own.
3. It prompts you to migrate your florr account and open florr.io in that
   new window, then press Enter.
4. It shows a confirm dialog — go fullscreen (F11) manually and click the
   button to start the bot loop.

A `chrome-profile\` folder will appear next to the exe after the first run
(the dedicated Chrome's persistent profile, so you don't have to re-migrate
your account on every launch). Do **not** include it when zipping up the exe
folder to distribute it — it holds a live florr.io session.

## Distributing it

The dist folder is large (~1GB, dominated by torch) and self-contained
(bundles its own Python + all deps) — anyone running it needs no Python
install. Zip `dist\florr-auto-pathing\` as a whole; the exe will not run
correctly if separated from its `_internal\` folder.
