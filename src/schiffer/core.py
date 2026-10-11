"""Shared helpers for all Schiffer modules."""

from __future__ import annotations

import os
import re
import subprocess


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


ALLOW_SHELL = _flag("SCHIFFER_ALLOW_SHELL")
ALLOW_WRITE = _flag("SCHIFFER_ALLOW_WRITE")
MAX_TIMEOUT = 600
CACHE_DIR = os.path.expanduser(os.environ.get("SCHIFFER_CACHE", "~/.schiffer/cache"))

_SHELL_PATTERN = re.compile(r"do\s+shell\s+script|doShellScript", re.IGNORECASE)

READ_ONLY_NOTE = (
    "Schiffer is in read-only mode. Tools that change apps or files are not available. "
    "To allow changes, set SCHIFFER_ALLOW_WRITE=1 in the Schiffer config (or run install.sh "
    "and answer yes) and restart Claude Desktop."
)

INSTRUCTIONS = f"""Schiffer steers photo apps on this Mac. Write mode: {"ON" if ALLOW_WRITE else "OFF (read-only)"}.
General rules:
- Ask the user before changes that touch files on disk or client work. Prefer a dry
  run or a test copy first, and say what will change.
- Never modify RAW originals. Metadata for RAW files goes in XMP sidecars.
- AppleScript is case-insensitive ("P" and "p" are the same variable). Reserved
  words cannot be variable names: before, after, ref, st, nd, rd, it.
- Shell commands inside AppleScript/JXA are blocked unless SCHIFFER_ALLOW_SHELL=1.
- Verify results (read values back, export and look) before reporting success.
""" + ("" if ALLOW_WRITE else f"- {READ_ONLY_NOTE}\n")


def run(args, stdin=None, timeout=60):
    p = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "").strip() or f"{args[0]} exited with code {p.returncode}")
    return (p.stdout or "").strip()


def clamp(timeout: int) -> int:
    return min(max(int(timeout), 1), MAX_TIMEOUT)


def guard(script: str) -> None:
    """Block shell access from scripts unless explicitly allowed.

    This is a guard against accidental or injected shell commands, not a sandbox.
    AppleScript can still control other apps the user has granted permission to.
    """
    if not ALLOW_SHELL and _SHELL_PATTERN.search(script):
        raise PermissionError(
            "Script uses shell commands, which Schiffer blocks by default. "
            "Set SCHIFFER_ALLOW_SHELL=1 in the server config to allow it."
        )


def osascript(script: str, language: str = "AppleScript", timeout: int = 60) -> str:
    guard(script)
    args = ["osascript"]
    if language == "JavaScript":
        args += ["-l", "JavaScript"]
    args.append("-")
    return run(args, stdin=script, timeout=clamp(timeout))


def cache_path(*parts: str) -> str:
    p = os.path.join(CACHE_DIR, *parts)
    os.makedirs(os.path.dirname(p) if os.path.splitext(p)[1] else p, exist_ok=True)
    return p
