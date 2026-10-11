"""Match module: find the original files for images a client sent back.

Clients often return a PDF, contact sheet or screenshots of their picks without
filenames. This module extracts each picture, detects red crosses (rejected picks),
and finds the matching file among candidate folders by pixel comparison.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import os

import numpy as np
from PIL import Image

from . import imaging as I
from . import metadata as M
from .core import cache_path

try:
    from mcp.server.fastmcp import Image as McpImage  # mcp 1.x
except ModuleNotFoundError:  # pragma: no cover
    from mcp.server.mcpserver import Image as McpImage  # mcp 2.x

NAME = "match"
MAX_CANDIDATES = 20000
RED_CROSSED = 0.03     # share of strong red pixels added on top of the picture
HIGH_RATIO = 2.0       # runner-up must be this much further away for "high" confidence
SAME_IMAGE_MAD = 4.0   # two candidates closer than this are the same picture

INSTRUCTIONS = """Match module (find filenames for a client's picks).
- match_client_selection takes the client's PDF or screenshots plus the folders the
  pictures can come from (an export or selects folder is faster than all RAW files).
- It returns, in reading order: position, file, path, whether the picture is crossed
  out in red, and a confidence. Always look at the verification sheet before reporting:
  left is the client's picture, right the matched file.
- "low" confidence or "same image in two places" needs a look; crossed-out pictures
  are listed but should not be treated as picks unless the user says so.
- The result is a list. To mark the files, use metadata_write or Capture One (write mode).
"""


# ---------- extracting the client's pictures ----------

def _red_share(im: Image.Image) -> float:
    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return float(((r > 170) & (g < 90) & (b < 90)).mean())


def _from_pdf(path: str):
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    out = []
    pdf = pdfium.PdfDocument(path)
    for pno in range(len(pdf)):
        page = pdf[pno]
        W, H = page.get_size()
        scale = min(3.0, 2600 / max(W, H))
        rendered = page.render(scale=scale).to_pil().convert("RGB")
        for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=5):
            l, b, r, t = obj.get_bounds()
            if (r - l) < W * 0.03 or (t - b) < H * 0.03:
                continue  # logos and icons
            try:
                pic = obj.get_bitmap(render=False).to_pil().convert("RGB")
            except Exception:
                pic = None
            box = (int(l * scale), int((H - t) * scale), int(r * scale), int((H - b) * scale))
            shown = rendered.crop(box)
            if pic is None or abs(pic.size[0] / pic.size[1] - shown.size[0] / max(1, shown.size[1])) > 0.05:
                pic = shown  # cropped or rotated in the layout: use what is visible
            red = max(0.0, _red_share(shown) - _red_share(pic.resize(shown.size)))
            out.append({"page": pno + 1, "box": box, "image": pic, "red": red})
    return out


def _segment(im: Image.Image, min_frac: float = 0.06):
    """Split a screenshot or contact sheet into pictures on a plain background (XY-cut)."""
    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(border, axis=0)
    fg = np.abs(a - bg).sum(axis=2) > 30
    H, W = fg.shape
    boxes = []

    def runs(v, minlen):
        res, start = [], None
        for i, on in enumerate(v):
            if on and start is None:
                start = i
            elif not on and start is not None:
                if i - start >= minlen:
                    res.append((start, i))
                start = None
        if start is not None and len(v) - start >= minlen:
            res.append((start, len(v)))
        return res

    def cut(x0, y0, x1, y1, depth=0):
        sub = fg[y0:y1, x0:x1]
        rows = runs(sub.mean(axis=1) > 0.02, max(4, int(H * min_frac / 2)))
        cols = runs(sub.mean(axis=0) > 0.02, max(4, int(W * min_frac / 2)))
        if depth < 6 and len(rows) > 1:
            for a0, a1 in rows:
                cut(x0, y0 + a0, x1, y0 + a1, depth + 1)
        elif depth < 6 and len(cols) > 1:
            for a0, a1 in cols:
                cut(x0 + a0, y0, x0 + a1, y1, depth + 1)
        elif rows and cols:
            bx = (x0 + cols[0][0], y0 + rows[0][0], x0 + cols[-1][1], y0 + rows[-1][1])
            if (bx[2] - bx[0]) >= W * min_frac and (bx[3] - bx[1]) >= H * min_frac:
                boxes.append(bx)

    cut(0, 0, W, H)
    return boxes


def _from_image(path: str):
    with Image.open(path) as src:
        im = src.convert("RGB")
    boxes = _segment(im)
    if len(boxes) <= 1:
        return [{"page": 1, "box": (0, 0, *im.size), "image": im, "red": _red_share(im)}]
    out = []
    for bx in boxes:
        pic = im.crop(bx)
        out.append({"page": 1, "box": bx, "image": pic, "red": _red_share(pic)})
    return out


def _reading_order(items):
    """Rows by vertical overlap (pictures of different height share a row), then left to right."""
    items = sorted(items, key=lambda d: (d["page"], (d["box"][1] + d["box"][3]) / 2))
    rows = []
    for it in items:
        y0, y1 = it["box"][1], it["box"][3]
        for row in rows:
            if row["page"] != it["page"]:
                continue
            overlap = min(y1, row["y1"]) - max(y0, row["y0"])
            if overlap > 0.5 * min(y1 - y0, row["y1"] - row["y0"]):
                row["items"].append(it)
                row["y0"], row["y1"] = min(y0, row["y0"]), max(y1, row["y1"])
                break
        else:
            rows.append({"page": it["page"], "y0": y0, "y1": y1, "items": [it]})
    rows.sort(key=lambda r: (r["page"], r["y0"]))
    ordered = []
    for r in rows:
        ordered += sorted(r["items"], key=lambda d: d["box"][0])
    return ordered


def extract(sources):
    items = []
    for s in sources:
        s = os.path.expanduser(s)
        if s.lower().endswith(".pdf"):
            items += [dict(d, source=s) for d in _from_pdf(s)]
        else:
            items += [dict(d, source=s) for d in _from_image(s)]
    if not items:
        raise ValueError("No pictures found in the source.")
    out = []
    for src in dict.fromkeys(d["source"] for d in items):
        out += _reading_order([d for d in items if d["source"] == src])
    for i, d in enumerate(out, 1):
        d["position"] = i
        d["crossed"] = d["red"] >= RED_CROSSED
    return out


# ---------- matching ----------

def _small(im: Image.Image, n: int = 128) -> Image.Image:
    im = im.copy()
    im.thumbnail((n, n), Image.LANCZOS)
    return im


def match(sources, search, recursive=True):
    picks = extract(sources)
    files = [f for f in M.expand_paths(search, recursive) if not f.lower().endswith(".pdf")]
    if not files:
        raise ValueError("No candidate images in the search folders.")
    if len(files) > MAX_CANDIDATES:
        raise ValueError(f"{len(files)} candidates. Narrow the search to at most {MAX_CANDIDATES} files.")
    cand = []
    for f in files:
        try:
            im = I.load_thumb(f, 320)
        except Exception:
            continue
        cand.append({"path": f, "desc": I.color_desc(im), "landscape": im.size[0] >= im.size[1], "im": im})
    by_shape = {}
    for c in cand:
        by_shape.setdefault(c["desc"].shape, []).append(c)
    stacks = {k: np.stack([c["desc"] for c in v]) for k, v in by_shape.items()}

    results = []
    for p in picks:
        q = I.color_desc(p["image"])
        pool = by_shape.get(q.shape, [])
        if not pool:
            results.append({**p, "file": None, "confidence": "none"})
            continue
        scores = stacks[q.shape] @ q / len(q)
        top = np.argsort(-scores)[:5]
        qs = _small(p["image"])
        ranked = sorted(((I.mad(qs, _small(pool[i]["im"])), pool[i]) for i in top), key=lambda t: t[0])
        best_mad, best = ranked[0]
        runner_mad, runner = ranked[1] if len(ranked) > 1 else (None, None)
        same_elsewhere = []
        conf = "high"
        if runner is not None:
            if I.mad(_small(best["im"]), _small(runner["im"])) < SAME_IMAGE_MAD:
                same_elsewhere = [r["path"] for m, r in ranked[1:] if I.mad(_small(best["im"]), _small(r["im"])) < SAME_IMAGE_MAD]
            elif runner_mad < best_mad * HIGH_RATIO:
                conf = "low"
        if best_mad > 20:
            conf = "low"
        results.append({**p, "file": os.path.basename(best["path"]), "path": best["path"], "match_im": best["im"],
                        "mad": round(best_mad, 2), "runner_up": os.path.basename(runner["path"]) if runner else None,
                        "runner_mad": round(runner_mad, 2) if runner_mad is not None else None,
                        "same_image_also_at": same_elsewhere, "confidence": conf})
    return results, len(cand)


def _sheet(results, title):
    cells = []
    for r in results:
        imgs = [r["image"]] + ([r["match_im"]] if r.get("match_im") is not None else [])
        lab = f"{r['position']}. {os.path.splitext(r['file'])[0] if r.get('file') else 'no match'}"
        if r["crossed"]:
            lab += "  CROSSED"
        if r.get("confidence") == "low":
            lab += "  CHECK"
        cells.append({"image": imgs, "label": lab,
                      "color": (255, 170, 170) if r["crossed"] else ((255, 214, 102) if r.get("confidence") != "high" else None)})
    return I.contact_sheet(cells, cols=4, cell=220, title=title)


def register(mcp) -> None:

    @mcp.tool()
    def match_client_selection(sources: list[str], search_folders: list[str], recursive: bool = True):
        """
        Find the filenames of pictures a client sent back without names. sources: PDF files,
        screenshots or contact sheets. search_folders: where the originals or exports are
        (JPEG exports match fastest; RAW is read via macOS previews). Returns the matches in
        reading order with red-cross detection, a CSV path, and a verification sheet
        (client picture left, matched file right). Read-only.
        """
        try:
            results, n = match(sources, search_folders, recursive)
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            out_csv = cache_path("selections", f"kundeutvalg_{stamp}.csv")
            with open(out_csv, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(["position", "crossed", "file", "path", "confidence"])
                for r in results:
                    w.writerow([r["position"], "yes" if r["crossed"] else "", r.get("file") or "", r.get("path") or "", r["confidence"]])
            kept = sum(1 for r in results if not r["crossed"])
            sheet = I.save_sheet(_sheet(results, f"{len(results)} pictures, {kept} kept, {len(results) - kept} crossed out"),
                                 f"match_{stamp}")
            rows = [{k: r.get(k) for k in ("position", "crossed", "file", "path", "confidence", "mad", "runner_up",
                                           "runner_mad", "same_image_also_at", "page")} for r in results]
            summary = {"pictures": len(results), "kept": kept, "crossed_out": len(results) - kept, "candidates_checked": n,
                       "low_confidence": [r["position"] for r in results if r["confidence"] != "high"],
                       "csv": out_csv, "sheet": sheet, "matches": rows}
            return [json.dumps(summary, ensure_ascii=False), McpImage(path=sheet)]
        except Exception as e:
            return json.dumps({"error": str(e)})
