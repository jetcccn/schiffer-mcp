#!/bin/bash
# Installs Schiffer for Claude Desktop on macOS.
# Usage: run from the repository folder:  bash install.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.schiffer"
CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

if [ "$(uname)" != "Darwin" ]; then
  echo "Schiffer only runs on macOS."; exit 1
fi
if [ ! -f "$HERE/pyproject.toml" ]; then
  echo "Run this script from the Schiffer repository folder."; exit 1
fi

echo "1/5 Setting up Python environment in $DEST (via uv)"
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
mkdir -p "$DEST"
"$UV" venv --quiet --allow-existing --python 3.12 "$DEST/venv"

echo "2/5 Installing Schiffer"
"$UV" pip install --quiet --reinstall --python "$DEST/venv/bin/python" "$HERE"

echo "3/5 Checking ExifTool (for the metadata module)"
EXIFTOOL=""
for c in "$(command -v exiftool 2>/dev/null || true)" /opt/homebrew/bin/exiftool /usr/local/bin/exiftool "$DEST/exiftool/exiftool"; do
  if [ -n "$c" ] && [ -x "$c" ]; then EXIFTOOL="$c"; break; fi
done
if [ -z "$EXIFTOOL" ]; then
  if ! command -v perl >/dev/null 2>&1; then
    echo "    Perl is missing, so ExifTool cannot run. Install it with Homebrew (brew install exiftool) and run this again."
  else
    TMP="$(mktemp -d)"
    VER="$(curl -fsSL https://exiftool.org/ver.txt 2>/dev/null | tr -d '[:space:]' || true)"
    # exiftool.org download links point to SourceForge; GitHub is the fallback
    if [ -n "$VER" ] && curl -fsL -o "$TMP/et.tgz" "https://sourceforge.net/projects/exiftool/files/Image-ExifTool-$VER.tar.gz/download" 2>/dev/null \
       && tar tzf "$TMP/et.tgz" >/dev/null 2>&1; then
      echo "    Downloaded ExifTool $VER (SourceForge)"
    elif [ -n "$VER" ] && curl -fsL -o "$TMP/et.tgz" "https://exiftool.org/Image-ExifTool-$VER.tar.gz" 2>/dev/null \
       && tar tzf "$TMP/et.tgz" >/dev/null 2>&1; then
      echo "    Downloaded ExifTool $VER (exiftool.org)"
    else
      echo "    Using the GitHub copy of ExifTool"
      curl -fsSL -o "$TMP/et.tgz" "https://github.com/exiftool/exiftool/archive/refs/heads/master.tar.gz"
    fi
    rm -rf "$DEST/exiftool" && mkdir -p "$DEST/exiftool"
    tar xzf "$TMP/et.tgz" -C "$DEST/exiftool" --strip-components=1
    rm -rf "$TMP"
    EXIFTOOL="$DEST/exiftool/exiftool"
  fi
fi
if [ -n "$EXIFTOOL" ]; then
  echo "    ExifTool $("$EXIFTOOL" -ver) at $EXIFTOOL"
fi

echo "4/5 Looking for Capture One"
APP_PATH="$(ls -d /Applications/Capture\ One*.app 2>/dev/null | head -1 || true)"
if [ -z "$APP_PATH" ]; then
  APP_NAME="Capture One"
  echo "    Capture One not found in /Applications. Using \"$APP_NAME\"."
else
  APP_NAME="$(basename "$APP_PATH" .app)"
  echo "    Found: $APP_PATH"
fi

echo "5/5 Adding Schiffer to the Claude Desktop config"
CURRENT_WRITE="$("$DEST/venv/bin/python" -c 'import json,sys
try: print(json.load(open(sys.argv[1]))["mcpServers"]["schiffer"]["env"].get("SCHIFFER_ALLOW_WRITE",""))
except Exception: print("")' "$CONFIG" 2>/dev/null || true)"
ALLOW_WRITE="$CURRENT_WRITE"
if [ -t 0 ] || [ -r /dev/tty ]; then
  echo "    Schiffer starts in read-only mode: it can read, analyse and suggest, but not change apps or files."
  if [ "$CURRENT_WRITE" = "1" ]; then DEF="Y/n"; else DEF="y/N"; fi
  read -r -p "    Allow changes (ratings, labels, renaming, Capture One scripts)? [$DEF] " ANSWER </dev/tty || ANSWER=""
  case "$ANSWER" in
    [yYjJ]*) ALLOW_WRITE="1" ;;
    [nN]*) ALLOW_WRITE="" ;;
  esac
fi
mkdir -p "$(dirname "$CONFIG")"
if [ -f "$CONFIG" ]; then
  cp "$CONFIG" "$CONFIG.backup-$(date +%Y%m%d-%H%M%S)"
fi
"$DEST/venv/bin/python" - "$CONFIG" "$DEST" "$APP_NAME" "$EXIFTOOL" "$ALLOW_WRITE" <<'PY'
import json, os, sys
config, dest, app, exiftool, allow_write = sys.argv[1:6]
data = {}
if os.path.exists(config) and os.path.getsize(config) > 0:
    with open(config) as f:
        data = json.load(f)
servers = data.setdefault("mcpServers", {})
# Remove earlier pre-release entries (capture-one, kripler) if present
for key, marker in (("capture-one", ".capture-one-mcp"), ("kripler", ".kripler")):
    if marker in json.dumps(servers.get(key, {})):
        servers.pop(key)
        print(f"    Removed old '{key}' entry.")
env = dict(servers.get("schiffer", {}).get("env", {}))   # keep your own settings
env["C1_APP_NAME"] = app
if env.get("SCHIFFER_APPS", "captureone,metadata") == "captureone,metadata":
    env["SCHIFFER_APPS"] = "captureone,metadata,cull,match"   # old default -> new default
if allow_write == "1":
    env["SCHIFFER_ALLOW_WRITE"] = "1"
else:
    env.pop("SCHIFFER_ALLOW_WRITE", None)
if exiftool:
    env["SCHIFFER_EXIFTOOL"] = exiftool
servers["schiffer"] = {"command": f"{dest}/venv/bin/schiffer", "args": [], "env": env}
with open(config, "w") as f:
    json.dump(data, f, indent=2)
mode = "write mode ON" if env.get("SCHIFFER_ALLOW_WRITE") == "1" else "read-only"
print(f"    Added 'schiffer' with modules: {env['SCHIFFER_APPS']} ({mode}). Other servers kept; backup saved next to the config.")
PY

echo
echo "Done. Quit Claude Desktop completely (Cmd+Q) and open it again."
echo "The first time Schiffer controls Capture One, macOS asks for permission. Click OK."
