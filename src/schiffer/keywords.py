"""Keyword help: show images so the assistant can propose keywords, use the user's own
keyword list, and write different keywords to different images in one reviewed batch.

Registered by the metadata module (no separate SCHIFFER_APPS entry needed).
"""

from __future__ import annotations

import json
import os
import re

from . import imaging as I
from . import metadata as M
from .core import ALLOW_WRITE

try:
    from mcp.server.fastmcp import Image as McpImage  # mcp 1.x
except ModuleNotFoundError:  # pragma: no cover
    from mcp.server.mcpserver import Image as McpImage  # mcp 2.x

VOCAB_ENV = "SCHIFFER_KEYWORDS"

INSTRUCTIONS = """Keywords (part of the metadata module).
- Flow: metadata_keyword_list (the user's vocabulary, if any) -> metadata_keyword_sheet
  (look at the images) -> propose keywords per image or group in a table -> the user
  corrects -> metadata_write_batch with dry_run=true -> show the plan -> dry_run=false.
- Use terms from the user's keyword list where they fit, with its hierarchy
  ("Utstyr|Kamera"). Mark terms that are not in the list as NEW so the list does not
  grow five spellings of the same thing. Match the language of the list.
- Only describe what is visible plus facts the user gave (client, place, project).
  Never name people unless the user names them. Never guess age, ethnicity, religion,
  health, gender identity or other personal traits; "children" or "youth" only when the
  user confirms it. No brand names unless clearly visible and relevant.
- Keep it short: 5-12 keywords per image is plenty. Same situation, same keywords.
- Description (IPTC caption) is optional: one plain sentence of what is in the picture.
"""


# ---------- keyword list (Lightroom / Bridge export format) ----------

def parse_keyword_file(path: str) -> dict:
    """Tab-indented keyword list as exported by Lightroom Classic and Bridge.
    [Name] = category (not written as a keyword), {Name} = synonym of the line above."""
    terms, synonyms, stack = [], {}, []
    with open(os.path.expanduser(path), encoding="utf-8-sig", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            depth = len(line) - len(line.lstrip("\t"))
            name = line.strip()
            if name.startswith("{") and name.endswith("}"):
                if stack:
                    synonyms.setdefault("|".join(stack), []).append(name[1:-1])
                continue
            category = name.startswith("[") and name.endswith("]")
            name = name.strip("[]")
            stack = stack[:depth] + [name]
            if not category:
                terms.append("|".join(stack))
    return {"terms": terms, "synonyms": synonyms}


def vocabulary(path: str | None, folders: list[str] | None, recursive: bool):
    out = {"source": [], "terms": [], "synonyms": {}}
    path = path or os.environ.get(VOCAB_ENV)
    if path:
        v = parse_keyword_file(path)
        out["source"].append(os.path.expanduser(path))
        out["terms"] += v["terms"]
        out["synonyms"].update(v["synonyms"])
    if folders:
        files = M.expand_paths(folders, recursive)
        used = set()
        for r in M.read_metadata(files):
            hier = r.get("hierarchical_keywords") or []
            used.update(hier)
            parts = {p for h in hier for p in h.split("|")}
            used.update(k for k in (r.get("keywords") or []) if k not in parts)
        out["source"].append(f"keywords already used in {len(files)} files")
        out["terms"] += sorted(used)
    out["terms"] = sorted(dict.fromkeys(out["terms"]), key=str.lower)
    return out


# ---------- sheet ----------

def keyword_sheet(files: list[str], page: int, per_page: int):
    pages = max(1, (len(files) + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    chunk = files[(page - 1) * per_page: page * per_page]
    meta = {m["path"]: m for m in M.read_metadata(chunk)} if M.find_exiftool() else {}
    cells, listing = [], []
    for i, f in enumerate(chunk, (page - 1) * per_page + 1):
        m = meta.get(f, {})
        hier = m.get("hierarchical_keywords") or []
        parts = {p for h in hier for p in h.split("|")}
        kws = list(dict.fromkeys(hier + [k for k in (m.get("keywords") or []) if k not in parts]))
        im = I.try_thumb(f, 512)
        cells.append({"image": im or I.placeholder(512),
                      "label": f"{i}. {I.short_name(f, 19)}" + (f" ({len(kws)})" if kws else "")})
        listing.append({"n": i, "file": os.path.basename(f), "path": f, "keywords": kws,
                        "description": m.get("description"), **({} if im else {"unreadable": True})})
    title = f"Keywords: {len(files)} images, page {page}/{pages}"
    sheet = I.save_sheet(I.contact_sheet(cells, cols=4, cell=300, title=title),
                         "keywords_" + re.sub(r"\W+", "_", os.path.dirname(files[0]))[-40:] + f"_{page}")
    return listing, pages, sheet


def register(mcp) -> None:

    @mcp.tool()
    def metadata_keyword_list(keyword_file: str | None = None, from_folders: list[str] | None = None,
                              recursive: bool = False) -> dict:
        """
        The user's keyword vocabulary, to propose consistent keywords. keyword_file: a list
        exported from Lightroom (Metadata > Export Keywords) or Bridge (Keywords panel menu >
        Export); default is SCHIFFER_KEYWORDS from the config. from_folders: also collect the
        keywords already used in these folders. Returns terms as "Parent|Child".
        """
        try:
            v = vocabulary(keyword_file, from_folders, recursive)
            return {"sources": v["source"] or ["none: no keyword file set and no folders given"],
                    "count": len(v["terms"]), "terms": v["terms"][:3000], "synonyms": v["synonyms"]}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def metadata_keyword_sheet(paths: list[str], recursive: bool = False, page: int = 1, per_page: int = 16):
        """
        Contact sheet of images with numbers and file names (number of keywords in brackets),
        plus a list of each image's current keywords,
        so the assistant can look at the pictures and propose keywords per image or group.
        Read-only. Use page for more than per_page images.
        """
        try:
            files = M.expand_paths(paths, recursive)
            if not files:
                return json.dumps({"error": "No images found."})
            listing, pages, sheet = keyword_sheet(files, page, max(4, min(per_page, 36)))
            return [json.dumps({"images": len(files), "page": page, "pages": pages, "on_this_page": listing},
                               ensure_ascii=False), McpImage(path=sheet)]
        except Exception as e:
            return json.dumps({"error": str(e)})

    if not ALLOW_WRITE:
        return

    @mcp.tool()
    def metadata_write_batch(assignments: list[dict], dry_run: bool = True) -> dict:
        """
        Write different metadata to different images in one reviewed step. assignments is a
        list of {"paths": [...], and any of: "keywords_add", "keywords_remove", "keywords_set",
        "description", "headline", "title", "rating", "label", "creator", "copyright",
        "usage_terms", "job_id"}. Dry run by default: returns the plan per group.
        """
        allowed = {"keywords_add", "keywords_remove", "keywords_set", "description", "headline", "title",
                   "rating", "label", "creator", "copyright", "usage_terms", "job_id", "label_style"}
        results = []
        try:
            for n, a in enumerate(assignments, 1):
                unknown = set(a) - allowed - {"paths"}
                if unknown:
                    return {"error": f"Assignment {n}: unknown fields {sorted(unknown)}"}
                files = M.expand_paths(a.get("paths") or [], False)
                fields = {k: v for k, v in a.items() if k in allowed}
                r = M.write_metadata(files, dry_run=dry_run, **fields)
                results.append({"assignment": n, "files": len(files), "result": r if dry_run else
                                {k: r.get(k) for k in ("files", "sidecars_created", "exiftool", "errors")}})
            return {"dry_run": dry_run, "assignments": len(results), "results": results,
                    "note": "Nothing written. Run again with dry_run=false to apply." if dry_run else "Written."}
        except Exception as e:
            return {"error": str(e), "done_before_error": results}
