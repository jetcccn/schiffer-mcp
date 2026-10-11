"""Shared image helpers: cached thumbnails, descriptors, focus maps and contact sheets."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .core import cache_path

Image.MAX_IMAGE_PIXELS = None
_SIPS = shutil.which("sips") or ("/usr/bin/sips" if os.path.exists("/usr/bin/sips") else None)


# ---------- thumbnails ----------

def thumbnail(path: str, size: int = 320) -> str:
    """Return a cached JPEG thumbnail (long side = size). Uses macOS sips for RAW."""
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{size}".encode()).hexdigest()
    out = cache_path("thumbs", key[:2], key + ".jpg")
    if os.path.exists(out):
        return out
    made = False
    if _SIPS:
        p = subprocess.run([_SIPS, "-s", "format", "jpeg", "-s", "formatOptions", "80", "-Z", str(size),
                            path, "--out", out], capture_output=True, text=True, timeout=120)
        made = p.returncode == 0 and os.path.exists(out)
        if made:
            _fix_rotation(path, out)
    if not made:
        with Image.open(path) as im:
            if im.format == "JPEG":
                im.draft("RGB", (size, size))  # decode at reduced size: much faster for large JPEGs
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((size, size), Image.LANCZOS)
            im.save(out, "JPEG", quality=82)
    return out


def _source_orientation(path: str) -> int:
    from . import metadata as M  # local import: metadata does not need imaging
    et = M.find_exiftool()
    if not et:
        return 1
    try:
        p = subprocess.run([et, "-n", "-s3", "-Orientation", path], capture_output=True, text=True, timeout=30)
        return int((p.stdout or "1").split()[0])
    except Exception:
        return 1


def _fix_rotation(src: str, thumb: str) -> None:
    """sips does not apply the orientation stored in RAW files: portrait frames come out lying down.
    Rotate when the source says portrait (5-8) but the thumbnail is landscape. Already-rotated
    output (as sips gives for JPEG) is left alone."""
    o = _source_orientation(src)
    if o not in (5, 6, 7, 8):
        return
    with Image.open(thumb) as im:
        if im.size[0] <= im.size[1]:
            return
        fixed = im.transpose({6: Image.Transpose.ROTATE_270, 8: Image.Transpose.ROTATE_90,
                              5: Image.Transpose.TRANSPOSE, 7: Image.Transpose.TRANSVERSE}[o])
        fixed.load()
    fixed.save(thumb, "JPEG", quality=82)


def load_thumb(path: str, size: int = 320) -> Image.Image:
    return Image.open(thumbnail(path, size)).convert("RGB")


def short_name(path: str, n: int) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem if len(stem) <= n else "..." + stem[-(n - 3):]


def try_thumb(path: str, size: int = 320):
    """Thumbnail, or None if the file cannot be decoded (damaged file, unsupported RAW)."""
    try:
        return load_thumb(path, size)
    except Exception:
        return None


def placeholder(size: int = 320, text: str = "cannot read") -> Image.Image:
    im = Image.new("RGB", (size, size * 2 // 3), (225, 225, 225))
    ImageDraw.Draw(im).text((10, 10), text, fill=(120, 0, 0), font=_font(18, True))
    return im


# ---------- descriptors ----------

def color_desc(im: Image.Image) -> np.ndarray:
    w, h = im.size
    d = np.asarray(im.resize((32, 21) if w >= h else (21, 32), Image.BILINEAR), dtype=np.float32).ravel()
    return (d - d.mean()) / (d.std() + 1e-6)


def scene_desc(im: Image.Image) -> np.ndarray:
    """Coarse, blur-tolerant descriptor for 'same situation' grouping."""
    w, h = im.size
    small = im.resize((12, 8) if w >= h else (8, 12), Image.BILINEAR).convert("RGB")
    d = np.asarray(small, dtype=np.float32).ravel()
    d = (d - d.mean()) / (d.std() + 1e-6)
    hsv = np.asarray(im.resize((64, 64)).convert("HSV"), dtype=np.float32)
    hist = np.concatenate([np.histogram(hsv[..., 0], 12, (0, 256))[0], np.histogram(hsv[..., 1], 6, (0, 256))[0],
                           np.histogram(hsv[..., 2], 6, (0, 256))[0]]).astype(np.float32)
    hist = hist / (hist.sum() + 1e-6)
    return np.concatenate([d * 0.1, hist * 10])


def corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return -1.0
    return float(np.dot(a, b) / len(a))


def mad(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute difference after resizing b to a."""
    b = b.resize(a.size, Image.LANCZOS)
    return float(np.abs(np.asarray(a, np.float32) - np.asarray(b, np.float32)).mean())


# ---------- focus ----------

def focus_map(im: Image.Image, tile: int = 32):
    """Laplacian variance per tile. Returns (score p97, (x, y) of sharpest tile centre, clip_hi, clip_lo)."""
    g = np.asarray(im.convert("L"), dtype=np.float32)
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    H, W = lap.shape
    vals, best = [], (0.0, W // 2, H // 2)
    for y in range(0, H - tile, tile):
        for x in range(0, W - tile, tile):
            v = float(lap[y:y + tile, x:x + tile].var())
            vals.append(v)
            if v > best[0]:
                best = (v, x + tile // 2, y + tile // 2)
    score = float(np.percentile(vals, 97)) if vals else 0.0
    return score, (best[1], best[2]), float((g >= 250).mean()), float((g <= 4).mean())


# ---------- sheets ----------

def _font(size: int, bold: bool = False):
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/Library/Fonts/Arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def contact_sheet(cells: list[dict], cols: int = 6, cell: int = 260, title: str | None = None) -> Image.Image:
    """cells: [{image: PIL.Image | list[PIL.Image], label: str, color: (r,g,b) | None, outline: (r,g,b) | None}]"""
    fb, fs = _font(16, True), _font(28, True)
    head = 44 if title else 0
    lab = 22
    n = max(1, len(cells))
    rows = (n + cols - 1) // cols
    widths = [len(c["image"]) if isinstance(c["image"], list) else 1 for c in cells] or [1]
    span = max(widths)
    cw = cell * span + 4 * (span - 1)
    S = Image.new("RGB", (cols * (cw + 8) + 8, head + rows * (cell + lab + 8) + 8), "white")
    d = ImageDraw.Draw(S)
    if title:
        d.text((10, 8), title, fill="black", font=fs)
    for k, c in enumerate(cells):
        x0 = 8 + (k % cols) * (cw + 8)
        y0 = head + 8 + (k // cols) * (cell + lab + 8)
        if c.get("color"):
            d.rectangle([x0, y0, x0 + cw, y0 + lab - 3], fill=c["color"])
        d.text((x0 + 3, y0 + 1), c.get("label", ""), fill="black", font=fb)
        imgs = c["image"] if isinstance(c["image"], list) else [c["image"]]
        for j, im in enumerate(imgs):
            k = min(cell / im.size[0], cell / im.size[1])  # fit the cell, also upscaling small previews
            t = im.resize((max(1, round(im.size[0] * k)), max(1, round(im.size[1] * k))), Image.LANCZOS)
            px = x0 + j * (cell + 4) + (cell - t.size[0]) // 2
            py = y0 + lab + (cell - t.size[1]) // 2
            S.paste(t, (px, py))
            if c.get("outline"):
                d.rectangle([px - 2, py - 2, px + t.size[0] + 1, py + t.size[1] + 1], outline=c["outline"], width=3)
    return S


def save_sheet(im: Image.Image, name: str) -> str:
    out = cache_path("sheets", name + ".jpg")
    if max(im.size) > 2400:
        im = im.copy()
        im.thumbnail((2400, 2400), Image.LANCZOS)
    im.save(out, "JPEG", quality=84)
    return out
