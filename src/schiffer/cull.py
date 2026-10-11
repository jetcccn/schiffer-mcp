"""Cull module: reduce a selection to N images, one per situation.

The tool proposes groups (capture time + visual similarity), measures focus and clipping,
and renders contact sheets and focus crops that the assistant looks at to decide.
Grouping by pixels is a proposal: in testing it separated "same situation" from
"new situation" correctly about 86-88% of the time, so the sheets must be reviewed.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import os
import uuid

import numpy as np
from PIL import Image

from . import imaging as I
from . import metadata as M
from .core import ALLOW_WRITE, cache_path

try:
    from mcp.server.fastmcp import Image as McpImage  # mcp 1.x
except ModuleNotFoundError:  # pragma: no cover
    from mcp.server.mcpserver import Image as McpImage  # mcp 2.x

NAME = "cull"
PALETTE = [(255, 214, 102), (153, 204, 255), (178, 223, 138), (255, 170, 170), (204, 179, 255), (255, 204, 153),
           (170, 230, 230), (230, 200, 230)]

INSTRUCTIONS = """Cull module (reduce many images to a selection).
- Flow: cull_analyze -> look at the overview sheet(s) -> fix the grouping with
  cull_regroup if needed -> cull_suggest -> check focus with cull_sheet kind="focus"
  -> agree picks with the user -> cull_save.
- Groups are a proposal from capture time and pixel similarity (right ~86-88% of the
  time in testing). Merge frames that are the same situation (same setup, pose and
  framing, small head turns) and split different ones before suggesting picks.
- Judge focus on the eye of the subject in the focus crops. The focus score measures
  the sharpest area of the whole frame, which can be hands, fabric or furniture.
- For a client, prefer variety across situations over two near-identical frames.
- Do not change the user's ratings; save picks as a list, an album (Capture One) or a
  label (metadata, write mode).
"""


def _short(path: str, n: int) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem if len(stem) <= n else "..." + stem[-(n - 3):]


def _now_id() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def _store(a: dict) -> None:
    with open(cache_path("analyses", a["id"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False)


def _load(aid: str) -> dict:
    p = cache_path("analyses", aid + ".json")
    if not os.path.exists(p):
        raise FileNotFoundError(f"No analysis with id {aid}. Run cull_analyze first.")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _exif_time(path: str):
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            s = ex.get_ifd(0x8769).get(36867) or ex.get(306)
            if s:
                return _dt.datetime.strptime(str(s)[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def _times_and_meta(files):
    meta = {}
    if M.find_exiftool():
        try:
            meta = {m["path"]: m for m in M.read_metadata(files)}
        except Exception:
            meta = {}
    out = {}
    for f in files:
        m = meta.get(f, {})
        t = None
        s = str(m.get("capture_date") or "")
        try:
            t = _dt.datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
        except ValueError:
            t = _exif_time(f)
        if t is None:
            t = _dt.datetime.fromtimestamp(os.path.getmtime(f))
        out[f] = {"time": t.isoformat(), "rating": m.get("rating"), "label": m.get("label")}
    return out


def _group(items, time_gap, threshold):
    """items sorted by time: list of dicts with desc, landscape, ts"""
    groups = []
    for it in items:
        if groups:
            cur = groups[-1]
            last = cur[-1]
            gap = it["ts"] - last["ts"]
            dmin = min((float(np.linalg.norm(it["desc"] - o["desc"])) if it["landscape"] == o["landscape"] else 99)
                       for o in cur[-3:])
            if gap <= time_gap and dmin < threshold:
                cur.append(it)
                continue
        groups.append([it])
    return groups


def overview_sheets(a: dict, per_page: int = 42):
    files = a["files"]
    pages = []
    for p in range(0, len(files), per_page):
        cells = []
        for f in files[p:p + per_page]:
            im = I.load_thumb(f["path"], 320)
            r = f.get("rating")
            cells.append({"image": im, "color": PALETTE[(f["group"] - 1) % len(PALETTE)],
                          "label": f"G{f['group']}  {_short(f['path'], 14)}"
                                   + (f"  {'*' * int(r)}" if r and r > 0 else "")})
        title = f"{a['id']}  overview {p // per_page + 1}/{(len(files) + per_page - 1) // per_page}  ({len(a['groups'])} groups, {len(files)} images)"
        pages.append(I.save_sheet(I.contact_sheet(cells, cols=7, cell=230, title=title), f"{a['id']}_overview_{p // per_page + 1}"))
    return pages


def focus_sheet(a: dict, group: int) -> str:
    members = [f for f in a["files"] if f["group"] == group]
    if not members:
        raise ValueError(f"No group {group}.")
    best = max(m["focus"] for m in members) or 1
    cells = []
    for m in members:
        big = I.load_thumb(m["path"], 1024)
        x, y = m["focus_xy"]
        h = min(160, big.size[0] // 2, big.size[1] // 2)
        x0 = min(max(0, x - h), big.size[0] - 2 * h)
        y0 = min(max(0, y - h), big.size[1] - 2 * h)
        crop = big.crop((x0, y0, x0 + 2 * h, y0 + 2 * h))
        small = big.copy()
        small.thumbnail((320, 320))
        cells.append({"image": [small, crop],
                      "label": f"{_short(m['path'], 18)}  focus {m['focus'] / best:.2f}"
                               + (f"  clip {m['clip_hi'] * 100:.1f}%" if m["clip_hi"] > 0.01 else "")})
    return I.save_sheet(I.contact_sheet(cells, cols=3, cell=300,
                                        title=f"{a['id']}  group {group}: full frame + crop at the sharpest area"),
                        f"{a['id']}_focus_{group}")


def analyze(paths, recursive=False, time_gap=40.0, threshold=2.0) -> dict:
    files = M.expand_paths(paths, recursive)
    if not files:
        raise ValueError("No images found.")
    tm = _times_and_meta(files)
    items, skipped = [], []
    for f in files:
        im, big = I.try_thumb(f, 320), I.try_thumb(f, 1024)
        if im is None or big is None:
            skipped.append(f)
            continue
        score, (fx, fy), hi, lo = I.focus_map(big)
        t = _dt.datetime.fromisoformat(tm[f]["time"])
        items.append({"path": f, "time": tm[f]["time"], "ts": t.timestamp(), "rating": tm[f]["rating"],
                      "label": tm[f]["label"], "desc": I.scene_desc(im), "landscape": im.size[0] >= im.size[1],
                      "focus": score, "focus_xy": [fx, fy], "clip_hi": hi, "clip_lo": lo})
    if not items:
        raise ValueError("None of the images could be read.")
    items.sort(key=lambda d: (d["ts"], os.path.basename(d["path"])))
    groups = _group(items, time_gap, threshold)
    for gi, g in enumerate(groups, 1):
        for it in g:
            it["group"] = gi
    a = {"id": _now_id(), "created": _dt.datetime.now().isoformat(), "params": {"time_gap": time_gap, "threshold": threshold},
         "files": [{k: v for k, v in it.items() if k != "desc"} for it in items],
         "groups": [[it["path"] for it in g] for g in groups], "skipped": skipped}
    _store(a)
    return a


def regroup(a: dict, groups: list[list[str]]) -> dict:
    by_name = {}
    for f in a["files"]:
        by_name[os.path.basename(f["path"])] = f
        by_name[os.path.splitext(os.path.basename(f["path"]))[0]] = f
        by_name[f["path"]] = f
    seen = set()
    new_groups = []
    for g in groups:
        members = []
        for n in g:
            f = by_name.get(n) or by_name.get(os.path.basename(n))
            if f is None:
                raise ValueError(f"Unknown image {n}")
            if f["path"] in seen:
                raise ValueError(f"{n} is in more than one group")
            seen.add(f["path"])
            members.append(f)
        new_groups.append(members)
    old = {}
    for f in a["files"]:  # images not mentioned stay in their current group
        if f["path"] not in seen:
            old.setdefault(f["group"], []).append(f)
    new_groups += list(old.values())
    new_groups.sort(key=lambda g: min(m["time"] for m in g))
    for gi, g in enumerate(new_groups, 1):
        for m in g:
            m["group"] = gi
    a["files"].sort(key=lambda f: (f["group"], f["time"]))
    a["groups"] = [[m["path"] for m in g] for g in new_groups]
    _store(a)
    return a


def suggest(a: dict, target: int | None):
    groups = {}
    for f in a["files"]:
        groups.setdefault(f["group"], []).append(f)

    def rank(members):
        best = max(m["focus"] for m in members) or 1
        return sorted(members, key=lambda m: (-(m["focus"] / best - 2 * max(0.0, m["clip_hi"] - 0.02)),
                                              -(m.get("rating") or 0)))

    ranked = {g: rank(ms) for g, ms in groups.items()}
    picks = [ranked[g][0] for g in sorted(ranked)]
    alts = []
    if target and len(picks) > target:
        # keep the target number, preferring groups the user rated higher and larger groups
        picks.sort(key=lambda m: (-(max((x.get("rating") or 0) for x in groups[m["group"]])), -len(groups[m["group"]]), m["time"]))
        alts = picks[target:]
        picks = sorted(picks[:target], key=lambda m: m["time"])
    elif target and len(picks) < target:
        extra = []
        for g in sorted(ranked, key=lambda g: -len(ranked[g])):
            extra += ranked[g][1:2]
        picks = sorted(picks + extra[:target - len(picks)], key=lambda m: m["time"])
    for g in sorted(ranked):
        alts += [m for m in ranked[g][1:2] if m not in picks and m not in alts]
    return picks, alts


def register(mcp) -> None:

    @mcp.tool()
    def cull_analyze(paths: list[str], recursive: bool = False, time_gap_seconds: float = 40.0,
                     threshold: float = 2.0):
        """
        Analyse images for culling: proposes groups of the same situation (capture time +
        visual similarity), measures focus and highlight clipping, and returns the first
        overview contact sheet (colour band = group). Use small previews (e.g. JPEGs exported
        from Capture One) or originals; RAW is read with macOS sips. Lower threshold splits more.
        """
        try:
            a = analyze(paths, recursive, time_gap_seconds, threshold)
            pages = overview_sheets(a)
            summary = {"analysis_id": a["id"], "images": len(a["files"]), "groups": len(a["groups"]),
                       "group_sizes": [len(g) for g in a["groups"]], "overview_pages": len(pages),
                       "unreadable": [os.path.basename(f) for f in a.get("skipped", [])],
                       "next": "Review the sheet. Use cull_sheet for more pages or focus crops, cull_regroup to fix groups."}
            return [json.dumps(summary), McpImage(path=pages[0])]
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def cull_sheet(analysis_id: str, kind: str = "overview", page: int = 1, group: int | None = None):
        """
        Show a sheet from an analysis. kind="overview" (page 1..n) shows all images with group
        colours. kind="focus" with group=N shows each image of the group with a crop at its
        sharpest area, to judge whether the eye is in focus.
        """
        try:
            a = _load(analysis_id)
            if kind == "focus":
                if group is None:
                    return json.dumps({"error": "Give group=N for a focus sheet."})
                return McpImage(path=focus_sheet(a, group))
            pages = overview_sheets(a)
            return McpImage(path=pages[max(1, min(page, len(pages))) - 1])
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def cull_regroup(analysis_id: str, groups: list[list[str]]):
        """
        Fix the proposed grouping. groups is a list of new groups, each a list of file names
        (with or without extension). Listed images move to their new group; images not
        mentioned stay where they were. To merge groups, list all their images as one group;
        to split one, list each part. Returns the new first overview sheet.
        """
        try:
            a = regroup(_load(analysis_id), groups)
            pages = overview_sheets(a)
            return [json.dumps({"analysis_id": a["id"], "groups": len(a["groups"]),
                                "group_sizes": [len(g) for g in a["groups"]]}), McpImage(path=pages[0])]
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def cull_suggest(analysis_id: str, target_count: int | None = None) -> str:
        """
        Suggest one pick per group (best focus score, no blown highlights, user rating as a
        tie-break), trimmed or filled to target_count, plus alternatives. Check the picks with
        cull_sheet kind="focus" before agreeing them with the user.
        """
        try:
            a = _load(analysis_id)
            picks, alts = suggest(a, target_count)
            fmt = lambda m: {"file": os.path.basename(m["path"]), "group": m["group"], "path": m["path"],
                             "focus_rel": round(m["focus"] / (max(x["focus"] for x in a["files"] if x["group"] == m["group"]) or 1), 2),
                             "rating": m.get("rating")}
            return json.dumps({"picks": [fmt(m) for m in picks], "alternatives": [fmt(m) for m in alts]}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def cull_save(analysis_id: str, picks: list[str], alternatives: list[str] | None = None,
                  csv_folder: str | None = None, label_picks: str | None = None,
                  label_alternatives: str | None = None):
        """
        Save the agreed selection: always writes a CSV and a contact sheet of the picks.
        csv_folder puts the CSV next to the images (write mode); otherwise it goes in the
        Schiffer cache. label_picks / label_alternatives (e.g. "green", "yellow") write XMP
        labels with the metadata module (write mode; RAW gets sidecars).
        """
        try:
            a = _load(analysis_id)
            by = {}
            for f in a["files"]:
                for k in (f["path"], os.path.basename(f["path"]), os.path.splitext(os.path.basename(f["path"]))[0]):
                    by[k] = f
            P = [by[n] for n in picks]
            A = [by[n] for n in (alternatives or [])]
            if csv_folder and not ALLOW_WRITE:
                return json.dumps({"error": "Writing next to the images needs write mode. Leave csv_folder empty to save in the cache."})
            folder = os.path.expanduser(csv_folder) if csv_folder else os.path.dirname(cache_path("selections", "x.csv"))
            out = os.path.join(folder, f"utvalg_{a['id']}.csv")
            with open(out, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(["status", "file", "group", "path"])
                for f in P:
                    w.writerow(["pick", os.path.basename(f["path"]), f["group"], f["path"]])
                for f in A:
                    w.writerow(["alternative", os.path.basename(f["path"]), f["group"], f["path"]])
            sheet = I.save_sheet(I.contact_sheet(
                [{"image": I.load_thumb(f["path"], 320), "label": os.path.splitext(os.path.basename(f["path"]))[0]} for f in P],
                cols=5, cell=300, title=f"Selection: {len(P)} of {len(a['files'])}"), f"{a['id']}_selection")
            result = {"csv": out, "sheet": sheet, "picks": len(P), "alternatives": len(A)}
            if label_picks or label_alternatives:
                if not ALLOW_WRITE:
                    result["labels"] = "Not written: read-only mode."
                else:
                    if label_picks:
                        result["labels_picks"] = M.write_metadata([f["path"] for f in P], dry_run=False, label=label_picks)
                    if label_alternatives and A:
                        result["labels_alternatives"] = M.write_metadata([f["path"] for f in A], dry_run=False, label=label_alternatives)
            return [json.dumps(result, ensure_ascii=False), McpImage(path=sheet)]
        except KeyError as e:
            return json.dumps({"error": f"Unknown image {e}"})
        except Exception as e:
            return json.dumps({"error": str(e)})
