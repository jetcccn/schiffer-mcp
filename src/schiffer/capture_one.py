"""Capture One module: drives Capture One through its AppleScript/JXA dictionary.

Environment:
  C1_APP_NAME  Application name if yours differs from "Capture One" (e.g. "Capture One 23").
"""

from __future__ import annotations

import glob
import os
import plistlib
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import re

from .core import ALLOW_SHELL, ALLOW_WRITE, READ_ONLY_NOTE, clamp, guard, osascript, run

NAME = "captureone"
APP_NAME = os.environ.get("C1_APP_NAME", "Capture One")
SCRIPTS_DIR = os.path.expanduser("~/Library/Scripts/Capture One Scripts")

INSTRUCTIONS = """Capture One module (AppleScript/JXA).

Before writing a script, look up the exact terms with capture_one_dictionary.
Lessons from real use:
- Set every recipe property you rely on and read it back. A new recipe defaults
  to inches and Adobe RGB, and an unknown color profile name is ignored silently
  (sRGB is "sRGB Color Space Profile").
- Avoid variable names that are also Capture One terms (e.g. "queued", "name",
  "rating"); AppleScript then targets the app property and the script fails.
- When changing items found with a "whose" filter, store the result in a variable
  first and loop over that list. A live "every ... whose" reference shrinks while
  you change it and fails with an invalid index.
- Setting an image's name renames the file on disk, including referenced files
  outside the session. Write a log of original names before renaming.
- Process with: process <variant> recipe "<name>"; output lands in the document's
  output folder plus the recipe's sub folder, asynchronously.
- To check output files, use System Events for listings and Image Events for
  dimensions; coerce paths to alias outside the Image Events tell block.
- Before processing, read the document's output folder. In a catalog it can point
  at a real client delivery folder. Use a recipe with a custom location for tests.
- Recipe output location: set "root folder location" first, then
  "root folder type" to custom location, then read both back. In the other order
  the change is silently ignored.
- "apply adjustments" takes one variant, not a list. Run "copy adjustments" on the
  source once, then apply to each target in a loop.
- "apply style ... named" targets a layer, e.g. current layer of the variant.
- To edit without touching the original, "clone variant" first and edit the clone
  (variant position 2, 3, ...).
- Read a property into a variable before coercing or rounding it.
- Collections: make new collection with properties {name:..., kind:group|album};
  nest with "at end of collections of <group>"; add with: add inside <album> variants <list>.
- Smart albums need a rules XML string at creation, e.g.
  <?xml version="1.0" encoding="UTF-8"?><MatchOperator Kind="AND"><Condition Enabled="YES">
  <Key>IB_S_BASIC_URGENCY</Key><Operator>0</Operator><Criterion>4</Criterion></Condition></MatchOperator>
  Known keys: IB_S_BASIC_URGENCY (color tag 0-7), IB_S_CONTENT_KEYWORDS (keyword text).
  For other keys, ask the user to make a smart album in the UI and read its "rules".
- Keywords cannot be created on the document. Use: tell <variant> to make new keyword
  with properties {name:"Hund", parent:"Dyr"}. Then "apply keyword <kw> to <list of variants>".
- IPTC fields are variant properties (contact creator, status copyright notice,
  status rights usage terms, content headline, status job identifier, ...). They are
  written to exported files as XMP and IPTC-IIM.
- Clones inherit rating and color tag, so filters by rating also catch clones.
- "Can't get current document" can be transient right after the app or client restarts; retry once.
- Matching a series to a reference image:
  * First read the reference's full adjustments and styles. A "reference" is often a
    graded image (style, curves, highlights), so matching usually means
    "copy adjustments" from it, not only white balance.
  * If camera settings and light are the same across the series (check EXIF and
    capture times), copying the reference's settings is the correct normalization.
    Variation in the camera's auto white balance is then noise, not information.
  * "pick normalize"/"apply normalize" (points in full-resolution pixels) gave wrong,
    reproducible results across a series in testing, even though the reference
    normalized to itself was fine. Do not trust it without measuring the output.
  * When correcting an outlier by measurement, confirm on a second, neutral area
    (e.g. a white wall or cushion). One reference surface can be fooled by
    reflections; in testing it pushed a correct image far too warm.
  * Verify by exporting small previews and comparing them; numbers alone are not enough.
- Culling (reducing a selection to N images):
  * Export small previews of the candidates and group them by situation (same setup,
    pose and framing) by looking at contact sheets in capture-time order. Pixel
    similarity alone splits near-identical frames and over-counts groups.
  * Pick one image per situation; judge focus on the eye, not the whole frame. A
    "sharpest area" metric often lands on hands, fabric or furniture.
  * Fill to N with variety across situations, keep a short list of alternatives,
    and put picks in an album. Do not change the user's ratings or tags.
  * Albums show every variant of an added image, including clones.
- Finding filenames from a client's PDF or screenshots of a selection:
  * Extract the embedded images from the PDF (pdfimages) and find their positions and
    any marks (e.g. red crosses for rejects) on a rendered page.
  * Make small thumbnails of the candidate files on the Mac with JXA and AppKit
    (NSImage, NSBitmapImageRep). Image Events could not open files on an external
    drive in testing. Combine thumbnails into one atlas image plus a JSON index so
    only two files need to be transferred.
  * Match by normalized colour thumbnails, then confirm close calls at higher
    resolution; burst neighbours can differ only slightly. Verify with a
    side-by-side sheet before reporting filenames.
- Ratings, color tags and keywords for images in a Capture One session or catalog:
  change them in Capture One. If XMP was written to those files with the metadata
  tools, run "reload metadata" on the affected variants so Capture One picks it up.
- The other way: ratings, colour tags and keywords set in Capture One reach the XMP
  sidecar (and so Bridge/Lightroom) only with Preferences > Image > Metadata > Auto Sync
  Sidecar XMP = Full Sync, or after "sync metadata <variant>". If the user works across
  apps and Full Sync is off, run sync metadata on the variants you changed (tested:
  rating, colour tag as label "Red"/"Green"..., and keywords are written).
- Colour tags are numbers: 0 none, 1 red, 2 orange, 3 yellow, 4 green, 5 blue, 6 pink,
  7 purple. Capture One maps XMP labels by English colour name (Green -> 4); Bridge's
  default label names such as "Approved" are ignored.
- Renaming files that belong to a session must be done in Capture One, not on disk,
  or the adjustments (stored by file name) are lost.
"""


def _find_app_paths():
    paths = sorted(glob.glob("/Applications/Capture One*.app"))
    paths += sorted(glob.glob(os.path.expanduser("~/Applications/Capture One*.app")))
    if not paths:
        try:
            out = run(
                ["mdfind", "kMDItemContentType == 'com.apple.application-bundle' && kMDItemFSName == 'Capture One*'"],
                timeout=15,
            )
            paths = [line for line in out.splitlines() if line.endswith(".app")]
        except Exception:
            pass
    return paths


def _preferred_app_path():
    paths = _find_app_paths()
    exact = [p for p in paths if os.path.basename(p) == f"{APP_NAME}.app"]
    return (exact or paths or [None])[0]


def read_sdef(app_path: str) -> str:
    """Read the app's scripting definition straight from the bundle.

    Falls back to the `sdef` command, which needs Xcode Command Line Tools.
    """
    res = os.path.join(app_path, "Contents", "Resources")
    candidates = []
    try:
        with open(os.path.join(app_path, "Contents", "Info.plist"), "rb") as f:
            name = plistlib.load(f).get("OSAScriptingDefinition")
        if name:
            candidates.append(os.path.join(res, name))
    except Exception:
        pass
    candidates += sorted(glob.glob(os.path.join(res, "*.sdef")))
    for c in candidates:
        if os.path.isfile(c):
            with open(c, encoding="utf-8") as f:
                return f.read()
    return run(["sdef", app_path], timeout=30)


def format_sdef(xml_text: str, query: str = "") -> str:
    """Turn sdef XML into readable text, optionally filtered by a search term.

    Commands, classes and enumerations whose own name or description match are
    shown in full, exact name matches first. Where only some members match,
    just those members are shown.
    """
    root = ET.fromstring(xml_text)
    q = query.lower().strip()
    ranked = []

    def hit(*texts):
        return any(q in (t or "").lower() for t in texts)

    for suite in root.iter("suite"):
        sname = suite.get("name", "")
        for el in suite:
            tag = el.tag
            if tag not in ("command", "class", "class-extension", "enumeration", "record-type"):
                continue
            name = el.get("name", "")
            desc = el.get("description", "")
            label = name or el.get("extends", "")
            members, matching = [], []
            for c in el:
                if c.tag in ("property", "parameter", "direct-parameter", "enumerator", "element", "responds-to", "result"):
                    cname = c.get("name") or c.get("type") or c.get("command") or ""
                    ctype = c.get("type", "")
                    cdesc = c.get("description", "")
                    access = c.get("access", "")
                    extra = f" ({ctype})" if ctype and ctype != cname else ""
                    extra += f" [{access}]" if access else ""
                    line = f"    - {c.tag} {cname}{extra}: {cdesc}".rstrip(": ")
                    members.append(line)
                    if q and hit(cname, cdesc):
                        matching.append(line)
            head = f"[{sname}] {tag} {label}" + (f": {desc}" if desc else "")
            if not q:
                ranked.append((2, "\n".join([head] + members)))
            elif label.lower() == q:
                ranked.append((0, "\n".join([head] + members)))
            elif hit(label, desc):
                ranked.append((1, "\n".join([head] + members)))
            elif matching:
                hidden = len(members) - len(matching)
                note = [f"    ({hidden} other members hidden)"] if hidden else []
                ranked.append((2, "\n".join([head] + matching + note)))
    ranked.sort(key=lambda r: r[0])
    return "\n\n".join(b for _, b in ranked) if ranked else f"No matches for '{query}'."



_WRITE_VERBS = re.compile(
    r"(?m)(?:^|\bto\s+|\(\s*|\bthen\s+)\s*(make\s+new|delete|duplicate|move\s+inside|add\s+inside|process|apply\s+(adjustments|style|keyword|normalize|LCC)|"
    r"clone\s+variant|autoadjust|reset\s+adjustments|rotate\s+(left|right)|import(?!\s+(?:settings|counter|naming|job|copyright|description|collection|options))|export\s+originals|batch\s+rename|"
    r"pick\s+normalize|capture(?!\s+(?:name|counter|done|metadata|profile|orientation|date|pilot))|synchronize|backup\s+now|create\s+(LCC|template)|promote|demote|relink|pack|unpack|"
    r"reload\s+metadata|sync\s+metadata|quit|silently\s+quit|close|save|open|copy\s+adjustments|"
    r"begin\s+live\s+view|end\s+live\s+view|apply\s+workspace|upgrade\s+engine|maximum\s+crop|autocrop)\b",
    re.IGNORECASE)
_STRINGS = re.compile(r'"(?:[^"\\]|\\.)*"')
_SET_OF = re.compile(r"\bset\s+(?:(?!\bto\b)[^\n])*\bof\b(?:(?!\bto\b)[^\n])*\bto\b", re.IGNORECASE)
_PROP_RE = None


def _property_setter_regex():
    """Regex for 'set <Capture One property> to/of', built from the installed dictionary."""
    global _PROP_RE
    if _PROP_RE is None:
        names = set()
        try:
            root = ET.fromstring(read_sdef(_preferred_app_path()))
            for prop in root.iter("property"):
                if prop.get("access", "rw") != "r" and prop.get("name"):
                    names.add(prop.get("name"))
        except Exception:
            names = {"rating", "color tag", "name", "exposure", "temperature", "tint", "contrast"}
        alts = "|".join(re.escape(n).replace("\\ ", "\\s+") for n in sorted(names, key=len, reverse=True))
        _PROP_RE = re.compile(r"\bset\s+(?:" + alts + r")\s+(?:to|of)\b", re.IGNORECASE)
    return _PROP_RE


def read_only_violation(script: str) -> str | None:
    """Best-effort check that a script only reads. Not a sandbox."""
    script = _STRINGS.sub('""', script)
    for rx, why in ((_WRITE_VERBS, "a command that changes Capture One"),
                    (_SET_OF, "setting a property"),
                    (_property_setter_regex(), "setting a Capture One property")):
        m = rx.search(script)
        if m:
            return f"{why} ('{m.group(0).strip()[:40]}')"
    return None


def register(mcp) -> None:

    @mcp.tool()
    def capture_one_status() -> dict:
        """Check whether Capture One is installed and running, and which document (session or catalog) is open."""
        paths = _find_app_paths()
        try:
            running = run(["pgrep", "-fl", "Capture One"], timeout=10)
        except Exception:
            running = ""
        result = {
            "configured_app_name": APP_NAME,
            "installed_apps": paths,
            "running": bool(running),
            "shell_allowed": ALLOW_SHELL,
            "write_allowed": ALLOW_WRITE,
        }
        if running:
            try:
                result["current_document"] = osascript(
                    f'tell application "{APP_NAME}" to get name of current document', timeout=15
                )
            except Exception as e:
                result["current_document_error"] = str(e)
        return result


    @mcp.tool()
    def capture_one_dictionary(query: str = "", max_chars: int = 20000) -> str:
        """
        Read Capture One's scripting dictionary (sdef) for the installed version.
        Use query to filter, e.g. "variant", "recipe", "exposure", "rating", "process", "capture".
        Call this before writing scripts so commands and properties match the installed version.
        """
        path = _preferred_app_path()
        if not path:
            return "Capture One was not found in /Applications or ~/Applications."
        try:
            xml_text = read_sdef(path)
            text = format_sdef(xml_text, query)
        except Exception as e:
            return f"ERROR reading dictionary: {e}"
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[Truncated at {max_chars} characters. Use a narrower query.]"
        return text


    @mcp.tool()
    def capture_one_run_applescript(script: str, timeout: int = 60) -> str:
        """
        Run AppleScript on this Mac, typically inside: tell application "Capture One" ... end tell.
        In read-only mode, scripts that appear to change Capture One are refused.
        Returns the script's result. Read the dictionary first with capture_one_dictionary.
        Shell commands are blocked unless SCHIFFER_ALLOW_SHELL=1.
        """
        if not ALLOW_WRITE:
            bad = read_only_violation(script)
            if bad:
                return f"ERROR: blocked in read-only mode: {bad}. {READ_ONLY_NOTE}"
        try:
            return osascript(script, "AppleScript", timeout) or "(no return value)"
        except (RuntimeError, PermissionError, subprocess.TimeoutExpired) as e:
            return f"ERROR: {e}"


    @mcp.tool()
    def capture_one_list_menu_scripts() -> list:
        """List the scripts in Capture One's Scripts menu (~/Library/Scripts/Capture One Scripts)."""
        if not os.path.isdir(SCRIPTS_DIR):
            return []
        return sorted(f for f in os.listdir(SCRIPTS_DIR) if f.endswith((".scpt", ".applescript", ".scptd")))


    if not ALLOW_WRITE:
        return

    @mcp.tool()
    def capture_one_run_jxa(script: str, timeout: int = 60) -> str:
        """
        Run JavaScript for Automation (JXA). Example:
        const c1 = Application("Capture One"); c1.currentDocument.name();
        Useful for lists and JSON output: end with JSON.stringify(...).
        Shell commands are blocked unless SCHIFFER_ALLOW_SHELL=1.
        """
        try:
            return osascript(script, "JavaScript", timeout) or "(no return value)"
        except (RuntimeError, PermissionError, subprocess.TimeoutExpired) as e:
            return f"ERROR: {e}"


    @mcp.tool()
    def capture_one_run_menu_script(filename: str, timeout: int = 120) -> str:
        """Run a saved script from Capture One's Scripts menu, by file name."""
        path = os.path.join(SCRIPTS_DIR, os.path.basename(filename))
        if not os.path.exists(path):
            raise FileNotFoundError(f"Not found: {path}")
        try:
            return run(["osascript", path], timeout=clamp(timeout)) or "(no return value)"
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            return f"ERROR: {e}"


    @mcp.tool()
    def capture_one_save_menu_script(name: str, script: str, overwrite: bool = False) -> str:
        """
        Compile and save an AppleScript to Capture One's Scripts menu, so the user can run it
        from the menu and assign a keyboard shortcut without the assistant. Saved as <name>.scpt.
        """
        guard(script)
        safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_().").strip()
        if not safe:
            raise ValueError("Invalid name.")
        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        out = os.path.join(SCRIPTS_DIR, f"{safe}.scpt")
        if os.path.exists(out) and not overwrite:
            raise FileExistsError(f"{out} exists. Set overwrite=true to replace it.")
        with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as tmp:
            tmp.write(script)
            src = tmp.name
        try:
            run(["osacompile", "-o", out, src], timeout=30)
        finally:
            os.unlink(src)
        return f"Saved: {out}. In Capture One: Scripts > Update Scripts Menu."


