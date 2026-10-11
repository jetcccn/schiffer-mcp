"""
Schiffer: an MCP server that steers photo apps on macOS.

Modules (enable with SCHIFFER_APPS, comma separated; default "captureone,metadata,cull,match"):
  captureone  Capture One via its AppleScript/JXA dictionary
  metadata    Ratings, labels, keywords (incl. keyword suggestions), IPTC and renaming with ExifTool
  cull        Reduce many images to a selection: grouping, focus crops, contact sheets
  match       Find filenames for pictures a client sent back in a PDF or screenshots

Environment variables:
  SCHIFFER_APPS         Modules to enable.
  C1_APP_NAME           Capture One app name if it differs from "Capture One".
  SCHIFFER_EXIFTOOL     Path to exiftool if it is not found automatically.
  SCHIFFER_LABEL_STYLE  "color" (default: Red, Green ...) or "bridge" (Select, Approved ...).
  SCHIFFER_KEYWORDS     Path to a keyword list exported from Lightroom or Bridge.
  SCHIFFER_ALLOW_WRITE  Set to "1" to enable tools that change apps or files (default: read-only).
  SCHIFFER_ALLOW_SHELL  Set to "1" to allow shell commands inside AppleScript/JXA.
"""

from __future__ import annotations

import os

try:
    from mcp.server.fastmcp import FastMCP  # mcp 1.x
except ModuleNotFoundError:  # pragma: no cover
    from mcp.server.mcpserver import MCPServer as FastMCP  # mcp 2.x

from . import capture_one, core, cull, keywords, match, metadata

MODULES = {m.NAME: m for m in (capture_one, metadata, cull, match)}
ALIASES = {"capture_one": "captureone", "c1": "captureone", "exiftool": "metadata", "xmp": "metadata", "culling": "cull", "client": "match"}
DEFAULT_APPS = "captureone,metadata,cull,match"


def enabled_modules() -> list:
    names = [n.strip().lower() for n in os.environ.get("SCHIFFER_APPS", DEFAULT_APPS).split(",") if n.strip()]
    mods = []
    for n in names:
        n = ALIASES.get(n, n)
        if n in MODULES and MODULES[n] not in mods:
            mods.append(MODULES[n])
    return mods


def build() -> FastMCP:
    mods = enabled_modules()
    parts = [core.INSTRUCTIONS]
    for m in mods:
        parts.append(m.INSTRUCTIONS)
        if m is metadata:
            parts.append(keywords.INSTRUCTIONS)  # keyword tools ship with the metadata module
    server = FastMCP("schiffer", instructions="\n".join(parts))
    for m in mods:
        m.register(server)
        if m is metadata:
            keywords.register(server)
    return server


mcp = build()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
