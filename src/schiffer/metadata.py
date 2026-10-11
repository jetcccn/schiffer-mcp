"""Metadata module: reads and writes XMP/IPTC with ExifTool, independent of any app.

Ratings, labels, keywords and IPTC written here are read by Adobe Bridge, Lightroom,
Capture One and Photo Mechanic. RAW files are never modified: their metadata goes in
an XMP sidecar (basename.xmp), the convention used by Bridge, Lightroom and Capture One.

Environment:
  SCHIFFER_EXIFTOOL  Path to exiftool if it is not found automatically.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import tempfile

from .core import ALLOW_WRITE

NAME = "metadata"

RAW_EXT = {
    ".3fr", ".ari", ".arw", ".bay", ".cr2", ".cr3", ".crw", ".dcr", ".erf", ".fff", ".gpr",
    ".iiq", ".k25", ".kdc", ".mef", ".mos", ".mrw", ".nef", ".nrw", ".orf", ".pef", ".raf",
    ".raw", ".rw2", ".rwl", ".sr2", ".srf", ".srw", ".x3f",
}
EMBED_EXT = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".heic", ".heif", ".psd", ".psb", ".dng", ".webp"}
IMAGE_EXT = RAW_EXT | EMBED_EXT
IPTC_EXT = {".jpg", ".jpeg", ".tif", ".tiff", ".psd", ".psb", ".dng"}

# Bridge's default label names, by colour. Lightroom uses the colour names themselves.
BRIDGE_LABELS = {"red": "Select", "yellow": "Second", "green": "Approved", "blue": "Review", "purple": "To Do"}
COLOR_LABELS = {"red": "Red", "yellow": "Yellow", "green": "Green", "blue": "Blue", "purple": "Purple"}
LABEL_STYLE = os.environ.get("SCHIFFER_LABEL_STYLE", "color").strip().lower() or "color"

MAX_FILES = 5000

EMPTY_XMP = (
    "<?xpacket begin='﻿' id='W5M0MpCehiHzreSzNTczkc9d'?>\n"
    "<x:xmpmeta xmlns:x='adobe:ns:meta/'>\n"
    "<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>\n"
    "</rdf:RDF>\n</x:xmpmeta>\n<?xpacket end='w'?>\n"
)

INSTRUCTIONS = """Metadata module (ExifTool, works on files without any app open).
- metadata_write and metadata_rename are dry runs unless dry_run=false. Show the user
  the plan from the dry run before running it for real on client work.
- RAW files get an XMP sidecar (basename.xmp); JPEG/TIFF/PSD/DNG get embedded XMP
  plus IPTC. RAW files themselves are never written.
- Labels are one text field. Capture One and Lightroom read colour names (Red, Yellow,
  Green, Blue, Purple). Bridge shows a label only if the text matches its label names
  (Bridge default: Select/Second/Approved/Review/To Do). Tested: Capture One 16 ignores
  "Approved" and maps "Green" to the green tag. The default style is "color"; the user
  should rename Bridge's labels to colour names (Preferences > Labels) so all three agree.
  label_style="bridge" writes Bridge's default names instead. Current default: "{LABEL_STYLE}".
- Keywords: "Dyr|Hund" or "Dyr > Hund" adds a hierarchical keyword plus each level
  as a flat keyword. Adding never duplicates.
- Rename keeps RAW+JPG pairs, sidecars and same-name PSDs together, writes a log next
  to the files and can be undone with metadata_undo_rename. It refuses folders that
  belong to a Capture One session (rename those in Capture One).
- Bridge picks up changes when it refreshes the folder. Capture One needs
  "reload metadata" on the affected variants.
"""
INSTRUCTIONS = INSTRUCTIONS.replace("{LABEL_STYLE}", LABEL_STYLE)


# ---------- exiftool ----------

def find_exiftool() -> str | None:
    env = os.environ.get("SCHIFFER_EXIFTOOL")
    if env and os.path.exists(env):
        return env
    found = shutil.which("exiftool")
    if found:
        return found
    for c in ("/opt/homebrew/bin/exiftool", "/usr/local/bin/exiftool",
              os.path.expanduser("~/.schiffer/exiftool/exiftool")):
        if os.path.exists(c):
            return c
    return None


def _exiftool(args: list[str], files: list[str] | None = None, timeout: int = 600) -> tuple[int, str, str]:
    exe = find_exiftool()
    if not exe:
        raise RuntimeError("ExifTool was not found. Run install.sh again or set SCHIFFER_EXIFTOOL.")
    lines = list(args) + list(files or [])
    with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        argfile = f.name
    try:
        p = subprocess.run([exe, "-charset", "filename=utf8", "-@", argfile],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    finally:
        os.unlink(argfile)


# ---------- paths ----------

def is_raw(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in RAW_EXT


def sidecar_for(path: str) -> str:
    return os.path.splitext(path)[0] + ".xmp"


def expand_paths(paths: list[str], recursive: bool = False) -> list[str]:
    out = []
    for p in paths:
        p = os.path.abspath(os.path.expanduser(p))
        if os.path.isdir(p):
            if recursive:
                for root, dirs, files in os.walk(p):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    out += [os.path.join(root, f) for f in files]
            else:
                out += [os.path.join(p, f) for f in os.listdir(p)]
        elif os.path.isfile(p):
            out.append(p)
        else:
            raise FileNotFoundError(f"Not found: {p}")
    files = sorted({f for f in out
                    if not os.path.basename(f).startswith(".")
                    and os.path.splitext(f)[1].lower() in IMAGE_EXT})
    if len(files) > MAX_FILES:
        raise ValueError(f"{len(files)} files is more than the limit of {MAX_FILES}. Narrow the selection.")
    return files


def _as_list(v):
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


# ---------- read ----------

READ_TAGS = [
    "-XMP-xmp:Rating", "-XMP-xmp:Label", "-XMP-dc:Subject", "-IPTC:Keywords",
    "-XMP-lr:HierarchicalSubject", "-XMP-dc:Creator", "-IPTC:By-line", "-XMP-dc:Rights",
    "-IPTC:CopyrightNotice", "-XMP-xmpRights:UsageTerms", "-XMP-photoshop:Headline",
    "-XMP-dc:Title", "-XMP-dc:Description", "-XMP-photoshop:TransmissionReference",
    "-EXIF:DateTimeOriginal", "-XMP-exif:DateTimeOriginal", "-EXIF:Model", "-Composite:ImageSize",
    "-ImageWidth", "-ImageHeight", "-Orientation",
]


def _read_raw(files: list[str]) -> dict[str, dict]:
    if not files:
        return {}
    rc, out, err = _exiftool(["-json", "-G1", "-n", "-api", "largefilesupport=1"] + READ_TAGS, files)
    if not out.strip():
        raise RuntimeError(err.strip() or "ExifTool returned nothing.")
    return {os.path.abspath(d["SourceFile"]): d for d in json.loads(out)}


def _orientation(d: dict) -> int:
    for k, v in d.items():
        if k.endswith(":Orientation") and isinstance(v, int):
            return v
    return 1


def _size(d: dict):
    """Full image size as displayed (rotated for portrait orientation)."""
    comp = d.get("Composite:ImageSize")
    if isinstance(comp, str) and len(comp.replace("x", " ").split()) == 2:
        w, h = (int(float(x)) for x in comp.replace("x", " ").split())
    else:  # largest width/height pair found
        ws = [v for k, v in d.items() if k.endswith(":ImageWidth") and isinstance(v, (int, float))]
        hs = [v for k, v in d.items() if k.endswith(":ImageHeight") and isinstance(v, (int, float))]
        if not ws or not hs:
            return None
        w, h = int(max(ws)), int(max(hs))
    if _orientation(d) in (5, 6, 7, 8):
        w, h = h, w
    return [w, h]


def read_metadata(files: list[str]) -> list[dict]:
    sidecars = [sidecar_for(f) for f in files if is_raw(f) and os.path.exists(sidecar_for(f))]
    raw = _read_raw(files + sidecars)
    result = []
    for f in files:
        d = dict(raw.get(f, {}))
        source = "embedded"
        sc = sidecar_for(f)
        if is_raw(f) and sc in raw:
            for k, v in raw[sc].items():
                if k.startswith("XMP-") and not k.startswith("XMP-x:"):
                    d[k] = v
            source = "sidecar"
        date = d.get("ExifIFD:DateTimeOriginal") or d.get("XMP-exif:DateTimeOriginal") or ""
        result.append({
            "path": f,
            "metadata_source": source if (source == "sidecar" or not is_raw(f)) else "embedded (no sidecar)",
            "rating": d.get("XMP-xmp:Rating"),
            "label": d.get("XMP-xmp:Label"),
            "keywords": _as_list(d.get("XMP-dc:Subject")) or _as_list(d.get("IPTC:Keywords")),
            "hierarchical_keywords": _as_list(d.get("XMP-lr:HierarchicalSubject")),
            "creator": ", ".join(map(str, _as_list(d.get("XMP-dc:Creator")) or _as_list(d.get("IPTC:By-line")))) or None,
            "copyright": d.get("XMP-dc:Rights") or d.get("IPTC:CopyrightNotice"),
            "usage_terms": d.get("XMP-xmpRights:UsageTerms"),
            "headline": d.get("XMP-photoshop:Headline"),
            "title": d.get("XMP-dc:Title"),
            "description": d.get("XMP-dc:Description"),
            "job_id": d.get("XMP-photoshop:TransmissionReference"),
            "capture_date": str(date) or None,
            "camera": d.get("IFD0:Model"),
            "size": _size(d),
        })
    return result


# ---------- write ----------

def _split_keyword(kw: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\s*(?:\||>)\s*", kw) if p.strip()]
    return parts


def build_write_args(target_is_sidecar: bool, ext: str, *, rating=None, label=None,
                     keywords_add=None, keywords_remove=None, keywords_set=None,
                     creator=None, copyright=None, usage_terms=None, headline=None,
                     title=None, description=None, job_id=None, label_style=None) -> list[str]:
    iptc = (not target_is_sidecar) and ext in IPTC_EXT
    a: list[str] = []
    if rating is not None:
        r = int(rating)
        if r < -1 or r > 5:
            raise ValueError("rating must be -1 (reject) to 5")
        a.append(f"-XMP-xmp:Rating={r}")
    if label is not None:
        key = str(label).strip().lower()
        if key in ("", "none", "clear"):
            a.append("-XMP-xmp:Label=")
        else:
            table = BRIDGE_LABELS if (label_style or LABEL_STYLE) == "bridge" else COLOR_LABELS
            text = table.get(key)
            if text is None:
                # allow passing the label text itself, e.g. "Select" or a custom Bridge label
                text = str(label).strip()
            a.append(f"-XMP-xmp:Label={text}")

    def flat_and_hier(kws):
        flat, hier = [], []
        for kw in kws or []:
            parts = _split_keyword(kw)
            if not parts:
                continue
            flat += parts
            if len(parts) > 1:
                hier.append("|".join(parts))
        return list(dict.fromkeys(flat)), list(dict.fromkeys(hier))

    if keywords_set is not None:
        flat, hier = flat_and_hier(keywords_set)
        a.append("-XMP-dc:Subject=")
        a.append("-XMP-lr:HierarchicalSubject=")
        if iptc:
            a.append("-IPTC:Keywords=")
        for w in flat:
            a.append(f"-XMP-dc:Subject+={w}")
            if iptc:
                a.append(f"-IPTC:Keywords+={w}")
        for h in hier:
            a.append(f"-XMP-lr:HierarchicalSubject+={h}")
    if keywords_add:
        flat, hier = flat_and_hier(keywords_add)
        for w in flat:
            a += [f"-XMP-dc:Subject-={w}", f"-XMP-dc:Subject+={w}"]
            if iptc:
                a += [f"-IPTC:Keywords-={w}", f"-IPTC:Keywords+={w}"]
        for h in hier:
            a += [f"-XMP-lr:HierarchicalSubject-={h}", f"-XMP-lr:HierarchicalSubject+={h}"]
    if keywords_remove:
        for kw in keywords_remove:
            parts = _split_keyword(kw)
            if not parts:
                continue
            leaf = parts[-1]
            a.append(f"-XMP-dc:Subject-={leaf}")
            if iptc:
                a.append(f"-IPTC:Keywords-={leaf}")
            if len(parts) > 1:
                a.append(f"-XMP-lr:HierarchicalSubject-={'|'.join(parts)}")
    pairs = [
        (creator, "-XMP-dc:Creator", "-IPTC:By-line"),
        (copyright, "-XMP-dc:Rights", "-IPTC:CopyrightNotice"),
        (usage_terms, "-XMP-xmpRights:UsageTerms", None),
        (headline, "-XMP-photoshop:Headline", "-IPTC:Headline"),
        (title, "-XMP-dc:Title", "-IPTC:ObjectName"),
        (description, "-XMP-dc:Description", "-IPTC:Caption-Abstract"),
        (job_id, "-XMP-photoshop:TransmissionReference", "-IPTC:OriginalTransmissionReference"),
    ]
    for value, xmp_tag, iptc_tag in pairs:
        if value is None:
            continue
        a.append(f"{xmp_tag}={value}")
        if iptc and iptc_tag:
            a.append(f"{iptc_tag}={value}")
    if iptc and any(x.startswith("-IPTC:") for x in a):
        a.append("-IPTC:CodedCharacterSet=UTF8")
    return a


def write_metadata(files: list[str], dry_run: bool = True, **fields) -> dict:
    groups: dict[tuple, list[str]] = {}
    plan = []
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if is_raw(f):
            target, side = sidecar_for(f), True
        else:
            target, side = f, False
        args = build_write_args(side, ext, **fields)
        if not args:
            raise ValueError("Nothing to write. Give at least one field.")
        groups.setdefault(tuple(args), []).append(target)
        plan.append({"file": f, "writes_to": target,
                     "creates_sidecar": side and not os.path.exists(target),
                     "changes": args})
    if dry_run:
        current = {m["path"]: m for m in read_metadata(files)}
        for p in plan:
            c = current.get(p["file"], {})
            p["current"] = {k: c.get(k) for k in ("rating", "label", "keywords", "hierarchical_keywords",
                                                  "creator", "copyright", "usage_terms", "headline",
                                                  "title", "description", "job_id")}
        return {"dry_run": True, "files": len(files),
                "sidecars_to_create": sum(p["creates_sidecar"] for p in plan),
                "plan": plan[:50], "note": "Nothing written. Run again with dry_run=false to apply."}
    created = 0
    for p in plan:
        if p["creates_sidecar"]:
            with open(p["writes_to"], "w", encoding="utf-8") as fh:
                fh.write(EMPTY_XMP)
            created += 1
    results, errors = [], []
    for args, targets in groups.items():
        rc, out, err = _exiftool(["-overwrite_original_in_place", "-P", "-api", "largefilesupport=1"] + list(args), targets)
        results.append(out.strip())
        if rc != 0 or "Error" in err:
            errors.append(err.strip())
    return {"dry_run": False, "files": len(files), "sidecars_created": created,
            "exiftool": results, "errors": errors}


# ---------- rename ----------

_TOKEN = re.compile(r"\{(\w+)(?::([^}]*))?\}")


def _safe(s: str) -> str:
    return re.sub(r'[\/:\x00-\x1f]', "-", s).strip()


def _capture_dt(meta: dict, path: str) -> _dt.datetime:
    s = meta.get("capture_date") or ""
    m = re.match(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", str(s))
    if m:
        return _dt.datetime(*map(int, m.groups()))
    return _dt.datetime.fromtimestamp(os.path.getmtime(path))


def _session_root(folder: str) -> str | None:
    cur = os.path.abspath(folder)
    for _ in range(4):
        if os.path.isdir(os.path.join(cur, "CaptureOne")) or any(
                n.endswith(".cosessiondb") for n in os.listdir(cur)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def plan_rename(files: list[str], pattern: str, start: int = 1, sort: str = "capture_time",
                include_companions: bool = True, allow_capture_one_folders: bool = False) -> list[tuple[str, str]]:
    if not files:
        raise ValueError("No image files given.")
    if not allow_capture_one_folders:
        for d in sorted({os.path.dirname(f) for f in files}):
            root = _session_root(d)
            if root:
                raise PermissionError(
                    f"{d} belongs to a Capture One session ({root}). Rename in Capture One, "
                    "or the adjustments are lost. Set allow_capture_one_folders=true only if you are sure.")
    meta = {m["path"]: m for m in read_metadata(files)}
    # group files that share folder + basename (RAW+JPG pairs) into one unit
    units: dict[tuple, list[str]] = {}
    for f in files:
        units.setdefault((os.path.dirname(f), os.path.splitext(os.path.basename(f))[0].lower()), []).append(f)
    if include_companions:
        for (d, base), members in units.items():
            for n in os.listdir(d):
                p = os.path.join(d, n)
                if os.path.isfile(p) and os.path.splitext(n)[0].lower() == base and p not in members \
                        and not n.startswith("."):
                    members.append(p)

    def primary(members):
        imgs = [m for m in members if m in meta]
        raws = [m for m in imgs if is_raw(m)]
        return (raws or imgs or members)[0]

    order = list(units.items())
    if sort == "name":
        order.sort(key=lambda kv: os.path.basename(primary(kv[1])).lower())
    else:
        order.sort(key=lambda kv: (_capture_dt(meta.get(primary(kv[1]), {}), primary(kv[1])),
                                   os.path.basename(primary(kv[1])).lower()))
    pairs = []
    for i, ((d, base), members) in enumerate(order):
        p = primary(members)
        m = meta.get(p, {})
        dt = _capture_dt(m, p)
        seq = start + i

        def sub(mt):
            tok, fmt = mt.group(1), mt.group(2)
            if tok == "seq":
                return str(seq).zfill(int(fmt) if fmt else 3)
            if tok == "date":
                return dt.strftime(fmt or "%Y%m%d")
            if tok == "time":
                return dt.strftime(fmt or "%H%M%S")
            if tok == "name":
                return os.path.splitext(os.path.basename(p))[0]
            if tok == "camera":
                return _safe(str(m.get("camera") or "camera")).replace(" ", "")
            if tok == "rating":
                return str(m.get("rating") or 0)
            if tok == "folder":
                return os.path.basename(d)
            raise ValueError(f"Unknown token {{{tok}}}. Use seq, date, time, name, camera, rating, folder.")

        newbase = _safe(_TOKEN.sub(sub, pattern))
        if not newbase:
            raise ValueError("Pattern produced an empty name.")
        for f in members:
            ext = os.path.splitext(f)[1]
            pairs.append((f, os.path.join(d, newbase + ext)))
    olds = {o for o, _ in pairs}
    news = [n for _, n in pairs]
    dupes = {n for n in news if news.count(n) > 1}
    if dupes:
        raise ValueError(f"Pattern gives duplicate names, e.g. {sorted(dupes)[:3]}. Add {{seq}}.")
    clash = [n for n in news if os.path.exists(n) and n not in olds]
    if clash:
        raise FileExistsError(f"Target names already exist: {clash[:3]}")
    return [(o, n) for o, n in pairs if o != n]


def _two_phase(pairs: list[tuple[str, str]]) -> None:
    tmp = []
    stamp = _dt.datetime.now().strftime("%H%M%S%f")
    for i, (o, n) in enumerate(pairs):
        t = os.path.join(os.path.dirname(o), f".schiffer_tmp_{stamp}_{i}")
        os.rename(o, t)
        tmp.append((t, n))
    for t, n in tmp:
        os.rename(t, n)


def apply_rename(pairs: list[tuple[str, str]]) -> str:
    folder = os.path.dirname(pairs[0][0])
    log = os.path.join(folder, f"_schiffer_rename_{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.tsv")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("old\tnew\n")
        for o, n in pairs:
            fh.write(f"{o}\t{n}\n")
    _two_phase(pairs)
    return log


def undo_rename(log_path: str) -> int:
    with open(log_path, encoding="utf-8") as fh:
        rows = [l.rstrip("\n").split("\t") for l in fh if l.strip()][1:]
    pairs = [(n, o) for o, n in rows]
    missing = [n for n, _ in pairs if not os.path.exists(n)]
    if missing:
        raise FileNotFoundError(f"Cannot undo, files missing: {missing[:3]}")
    clash = [o for _, o in pairs if os.path.exists(o) and o not in {n for n, _ in pairs}]
    if clash:
        raise FileExistsError(f"Cannot undo, original names are taken: {clash[:3]}")
    _two_phase(pairs)
    os.rename(log_path, log_path.replace(".tsv", ".undone.tsv"))
    return len(pairs)


# ---------- find ----------

def find_files(files: list[str], min_rating=None, max_rating=None, label=None, keyword=None,
               date_from=None, date_to=None) -> list[dict]:
    out = []
    for m in read_metadata(files):
        r = m["rating"] if m["rating"] is not None else 0
        if min_rating is not None and r < min_rating:
            continue
        if max_rating is not None and r > max_rating:
            continue
        if label is not None:
            want = str(label).lower()
            have = str(m["label"] or "").lower()
            names = {want, BRIDGE_LABELS.get(want, "").lower(), COLOR_LABELS.get(want, "").lower()} - {""}
            if (want in ("none", "") and have) or (want not in ("none", "") and have not in names):
                continue
        if keyword is not None:
            k = keyword.lower()
            if k not in [x.lower() for x in m["keywords"]] and \
                    not any(k == h.lower() or h.lower().endswith("|" + k) for h in m["hierarchical_keywords"]):
                continue
        d = str(m["capture_date"] or "")[:10].replace(":", "-")
        if date_from and (not d or d < date_from):
            continue
        if date_to and (not d or d > date_to):
            continue
        out.append(m)
    return out


# ---------- tools ----------

def register(mcp) -> None:

    @mcp.tool()
    def metadata_status() -> dict:
        """Show whether ExifTool is available and which version."""
        exe = find_exiftool()
        info = {"exiftool": exe}
        if exe:
            rc, out, err = _exiftool(["-ver"])
            info["version"] = out.strip()
        return info

    @mcp.tool()
    def metadata_read(paths: list[str], recursive: bool = False, max_results: int = 200) -> dict:
        """
        Read rating, label, keywords, IPTC (creator, copyright, usage terms, headline, title,
        description, job id), capture date and camera for image files or folders.
        For RAW files the XMP sidecar is used when it exists, as Bridge and Lightroom do.
        """
        try:
            files = expand_paths(paths, recursive)
            res = read_metadata(files)
            return {"files": len(files), "results": res[:max_results],
                    "truncated": len(res) > max_results}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def metadata_find(paths: list[str], recursive: bool = False, min_rating: int | None = None,
                      max_rating: int | None = None, label: str | None = None, keyword: str | None = None,
                      date_from: str | None = None, date_to: str | None = None,
                      max_results: int = 500) -> dict:
        """
        Find images by rating, label (colour or label text, or "none"), keyword, and capture
        date range (YYYY-MM-DD). Returns matching paths with their metadata.
        """
        try:
            files = expand_paths(paths, recursive)
            hits = find_files(files, min_rating, max_rating, label, keyword, date_from, date_to)
            return {"searched": len(files), "matches": len(hits), "results": hits[:max_results]}
        except Exception as e:
            return {"error": str(e)}

    if not ALLOW_WRITE:
        return

    @mcp.tool()
    def metadata_write(paths: list[str], recursive: bool = False, dry_run: bool = True,
                       rating: int | None = None, label: str | None = None,
                       keywords_add: list[str] | None = None, keywords_remove: list[str] | None = None,
                       keywords_set: list[str] | None = None, creator: str | None = None,
                       copyright: str | None = None, usage_terms: str | None = None,
                       headline: str | None = None, title: str | None = None,
                       description: str | None = None, job_id: str | None = None,
                       label_style: str | None = None) -> dict:
        """
        Write rating (-1 reject, 0-5), label (red/yellow/green/blue/purple, label text, or "none"),
        keywords (add/remove/set; "Parent|Child" for hierarchy) and IPTC fields.
        Dry run by default: returns the plan and current values without writing.
        RAW files get an XMP sidecar; other formats get embedded XMP and IPTC.
        """
        try:
            files = expand_paths(paths, recursive)
            return write_metadata(files, dry_run=dry_run, rating=rating, label=label,
                                  keywords_add=keywords_add, keywords_remove=keywords_remove,
                                  keywords_set=keywords_set, creator=creator, copyright=copyright,
                                  usage_terms=usage_terms, headline=headline, title=title,
                                  description=description, job_id=job_id, label_style=label_style)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def metadata_rename(paths: list[str], pattern: str, recursive: bool = False, start: int = 1,
                        sort: str = "capture_time", include_companions: bool = True,
                        dry_run: bool = True, allow_capture_one_folders: bool = False) -> dict:
        """
        Rename images with a pattern. Tokens: {seq} or {seq:4}, {date} or {date:%Y-%m-%d},
        {time}, {name} (original name), {camera}, {rating}, {folder}. Example:
        "Client_{date}_{seq}". Extensions are kept. RAW+JPG pairs, .xmp sidecars and
        same-name PSD/TIFF files move together. Dry run by default. A log is written next to
        the files; undo with metadata_undo_rename.
        """
        try:
            files = expand_paths(paths, recursive)
            pairs = plan_rename(files, pattern, start, sort, include_companions, allow_capture_one_folders)
            preview = [{"old": os.path.basename(o), "new": os.path.basename(n), "folder": os.path.dirname(o)}
                       for o, n in pairs]
            if dry_run:
                return {"dry_run": True, "renames": len(pairs), "plan": preview[:100],
                        "note": "Nothing renamed. Run again with dry_run=false to apply."}
            log = apply_rename(pairs)
            return {"dry_run": False, "renamed": len(pairs), "log": log}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def metadata_undo_rename(log_path: str) -> dict:
        """Undo a rename using the _schiffer_rename_*.tsv log it wrote."""
        try:
            n = undo_rename(os.path.expanduser(log_path))
            return {"restored": n}
        except Exception as e:
            return {"error": str(e)}
