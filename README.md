# Schiffer

An MCP server that lets an AI assistant steer photo apps on macOS. Capture One first.

Ask in plain language for session setup, renaming, ratings, keywords, process recipes, culling or batch work, and have it done in the app or on the files.

Made by a photographer for production work: the repetitive parts of a shoot day that keyboard shortcuts don't cover.

> Not affiliated with, endorsed by or sponsored by Capture One A/S, Adobe or Anthropic. Capture One is a trademark of Capture One A/S.

---

## Why the name

*Schiffer* is German for skipper: the one who steers.

---

## Modules

Turn modules on with `SCHIFFER_APPS` (default `captureone,metadata,cull,match`). The assistant only sees the tools and instructions of enabled modules.

| Module | Works with | How |
|---|---|---|
| `captureone` | Capture One (sessions and catalogs) | Capture One's AppleScript/JXA dictionary |
| `metadata` | Files on disk, read by Bridge, Lightroom, Capture One and Photo Mechanic | ExifTool, no app needs to be open |
| `cull` | Folders of images (previews or originals) | Grouping, focus and contact sheets the assistant looks at |
| `match` | A client's PDF, screenshot or contact sheet | Pixel comparison against your folders |

Planned: Adobe Bridge.

### Read-only by default

Schiffer starts in read-only mode. The assistant can read sessions, metadata and images, analyse and suggest, but the tools that change anything are not even loaded:

| | Read-only (default) | Write mode (`SCHIFFER_ALLOW_WRITE=1`) |
|---|---|---|
| Capture One | status, dictionary, menu script list, AppleScript that only reads | + scripts that change, JXA, run and save menu scripts |
| Metadata | status, read, find, keyword list, keyword sheet | + write, batch write, rename, undo rename |
| Cull | analyse, sheets, regroup, suggest, save a list | + labels on picks, list next to the images |
| Match | everything (it only reads) | same |

In read-only mode, `capture_one_run_applescript` refuses scripts that make, delete, move, process or set properties, using the writable properties in your installed version's dictionary. The installer asks which mode you want; you can change it later in the config.

### Capture One tools

| Tool | What it does |
|---|---|
| `capture_one_status` | Is Capture One running, which session or catalog is open |
| `capture_one_dictionary` | Reads the scripting dictionary of *your* installed version, filtered by keyword |
| `capture_one_run_applescript` | Runs AppleScript against Capture One |
| `capture_one_run_jxa` | Runs JavaScript for Automation (handy for JSON output) |
| `capture_one_list_menu_scripts` | Lists scripts in Capture One's Scripts menu |
| `capture_one_run_menu_script` | Runs one of those scripts |
| `capture_one_save_menu_script` | Saves a script to the Scripts menu, so you can run it later with a keyboard shortcut, no AI needed |

What can be automated is whatever Capture One exposes to AppleScript: most adjustments, variants, ratings, color tags, keywords, IPTC, albums, process recipes and session structure.

### Metadata tools

| Tool | What it does |
|---|---|
| `metadata_status` | Shows the ExifTool in use |
| `metadata_read` | Rating, label, keywords, IPTC, capture date and camera for files or folders |
| `metadata_find` | Find images by rating, label, keyword or date range |
| `metadata_write` | Set rating (-1 to 5), label, keywords (add, remove, set, hierarchical) and IPTC: creator, copyright, usage terms, headline, title, description, job ID |
| `metadata_rename` | Rename by pattern, e.g. `Client_{date}_{seq}`. RAW+JPG pairs, `.xmp` sidecars and same-name PSD/TIFF move together |
| `metadata_undo_rename` | Undo a rename from its log |
| `metadata_keyword_list` | Your keyword vocabulary: a list exported from Lightroom or Bridge, plus keywords already used in chosen folders |
| `metadata_keyword_sheet` | Shows the images with numbers, names and current keywords, so the assistant can propose keywords |
| `metadata_write_batch` | Writes different keywords, captions and other fields to different images in one reviewed step (dry run first) |

**Keyword help.** The assistant looks at the images, proposes keywords per image or group from your own keyword list (new terms are marked), you correct the table, and it writes after a dry run. It describes only what is visible plus facts you give (client, place, project): no names unless you give them, and no guessing age, ethnicity or other personal traits. Point `SCHIFFER_KEYWORDS` at an exported keyword list to use it by default.

- `metadata_write` and `metadata_rename` are **dry runs by default** and show the plan first.
- **RAW files are never written.** Their metadata goes in an XMP sidecar (`basename.xmp`), the convention Bridge, Lightroom and Capture One use. JPEG, TIFF, PSD and DNG get embedded XMP plus IPTC.
- **Labels:** the label is one text field, and apps read it differently. Capture One and Lightroom read colour names (Red, Yellow, Green, Blue, Purple); Bridge shows a label only if the text matches its own label names (default Select, Second, Approved, Review, To Do). Tested with Capture One 16: "Approved" is ignored, "Green" becomes the green tag. Schiffer writes colour names by default. **If you use Bridge, rename its labels to Red, Yellow, Green, Blue, Purple** (Preferences > Labels) so all three apps agree. To keep Bridge's default names, set `SCHIFFER_LABEL_STYLE=bridge`.
- **Rename refuses folders that belong to a Capture One session.** Capture One stores adjustments by file name, so rename those in Capture One.

### Cull tools

| Tool | What it does |
|---|---|
| `cull_analyze` | Groups images by situation (capture time + similarity), measures focus and blown highlights, returns an overview sheet |
| `cull_sheet` | More overview pages, or a focus sheet: each image of a group next to a crop at its sharpest area |
| `cull_regroup` | Fix the grouping: merge or split groups by file name |
| `cull_suggest` | One pick per group, trimmed or filled to a target number, plus alternatives |
| `cull_save` | Saves the agreed picks as a CSV and contact sheet; labels in write mode |

The grouping is a proposal. In testing on 98 images from one shoot it split "same situation" from "new situation" correctly about 86 to 88 percent of the time, so the assistant reviews the sheets and fixes groups before picking. The focus score measures the sharpest area of the frame, which is not always the eye; the focus crops are there to check that.

### Match tool

| Tool | What it does |
|---|---|
| `match_client_selection` | Finds the files behind each picture in a client's PDF, screenshot or contact sheet. Returns them in reading order, flags pictures crossed out in red, writes a CSV and returns a sheet with the client's picture next to the matched file |

### Example requests

- "Which session is open, and how many variants are rated 4 stars or more?"
- "Create capture folders from this shot list: 01_Hero, 02_Detail, 03_Lifestyle."
- "Process all green-tagged variants with the 'Web 2048px' recipe."
- "Give all 4- and 5-star images in this folder the keywords Client and Dyr > Hund, copyright © 2026 Jeton Kacaniku and usage terms '3 years, digital'."
- "Rename the selects to Client_{date}_{seq} and keep the PSDs with them."
- "Reduce these 98 images to 20, one per situation."
- "Here is the client's PDF of picks. Find the filenames."
- "Suggest keywords for this folder from my keyword list, and add a one-line caption to each picture."

---

## Requirements

- macOS
- Capture One with AppleScript support (for the `captureone` module)
- Claude Desktop or another MCP client
- Internet access during install (for `uv`, Python packages and ExifTool if missing)

## Install

```bash
git clone https://github.com/jetcccn/schiffer-mcp.git
cd schiffer-mcp
bash install.sh
```

Then quit Claude Desktop (Cmd+Q) and open it again. The first time Schiffer talks to Capture One, macOS asks whether Claude may control Capture One. Click OK. If you click Don't Allow, re-enable it under System Settings > Privacy & Security > Automation.

The installer:
- creates a separate Python environment in `~/.schiffer` and installs Schiffer there
- asks whether to allow changes (write mode) or stay read-only
- uses your ExifTool if you have one, otherwise downloads it to `~/.schiffer/exiftool`
- detects your Capture One app name
- adds a `schiffer` entry to `claude_desktop_config.json`, keeping your other servers and your own Schiffer settings, and saving a backup first

### Manual config

```json
{
  "mcpServers": {
    "schiffer": {
      "command": "/Users/YOU/.schiffer/venv/bin/schiffer",
      "env": {
        "SCHIFFER_APPS": "captureone,metadata,cull,match",
        "C1_APP_NAME": "Capture One"
      }
    }
  }
}
```

| Variable | Default | Purpose |
|---|---|---|
| `SCHIFFER_APPS` | `captureone,metadata,cull,match` | Modules to enable |
| `SCHIFFER_ALLOW_WRITE` | off | Set to `1` to load the tools that change apps and files |
| `C1_APP_NAME` | `Capture One` | App name, if yours differs (e.g. `Capture One 23`) |
| `SCHIFFER_EXIFTOOL` | found automatically | Path to `exiftool` |
| `SCHIFFER_KEYWORDS` | none | Keyword list exported from Lightroom or Bridge (tab-indented text) |
| `SCHIFFER_LABEL_STYLE` | `color` | `color` writes Red, Green and so on; `bridge` writes Bridge's default label names |
| `SCHIFFER_ALLOW_SHELL` | off | Set to `1` to allow AppleScript/JXA that runs shell commands |
| `SCHIFFER_CACHE` | `~/.schiffer/cache` | Thumbnails, contact sheets, analyses and saved lists |

---

## Safety

Read this before use.

- **Start in read-only mode** and turn on write mode when you trust the setup.
- **Scripts run with your user's permissions.** AppleScript can change many variants at once. Test on a copy of a session first.
- **Back up catalogs** before letting any tool write to them.
- **Check where output goes.** A catalog's output folder can point at a real delivery folder. Use a recipe with its own output location for tests.
- **Shell commands are blocked by default.** AppleScript's `do shell script` would give full access to your Mac. Schiffer refuses scripts containing it unless you set `SCHIFFER_ALLOW_SHELL=1`. This is a guard, not a sandbox.
- **Renaming in Capture One renames the real file.** If a session references images where they are, renaming them in Capture One renames the originals on disk.
- **Metadata writes change files** (except RAW). They are dry runs until you confirm, and file dates are preserved.
- **Read what the assistant proposes** before approving tool calls on client work.

---

## Status

Early alpha (0.4.1). Tested on macOS 27.0.1 with Capture One 16.7.2.

**Capture One, session with 38 referenced JPGs**

| Test | Result |
|---|---|
| Status, scripting dictionary | OK |
| Read names, ratings, color tags, EXIF | OK |
| Set and clear color tags on 25 variants | OK |
| Rename 38 files by capture time, with log, then restore | OK |
| Create a process recipe and process 38 variants (JPEG, 2048 px, sRGB) | OK, all outputs verified |
| Save a script to the Scripts menu and run it | OK |

**Capture One, catalog with 298 Nikon D850 NEF files (copy of a finished job)**

| Test | Result |
|---|---|
| Read and set adjustments (exposure, white balance, contrast, shadows) | OK, confirmed in exported files |
| Clone variants and edit only the clone | OK, originals unchanged |
| Copy adjustments from one variant to others, auto adjust, apply a style | OK |
| Process originals and clones to a custom folder | OK, 12 of 12 |
| Groups, albums, smart albums by color tag and keyword | OK, update live |
| Hierarchical keywords and IPTC (creator, copyright, usage terms, headline, job ID) | OK, embedded as XMP and IPTC in exported JPEG |
| Match a 16-image series to a graded reference | OK by copying the reference's adjustments |
| Capture One "normalize" via script | Not reliable in a series; see server instructions |
| Cull 98 rated images to 20 (one per situation, eye focus checked) | OK, picks and alternatives in albums; ratings untouched |
| Find filenames for 29 images in a client's PDF among 568 files, incl. crossed-out ones | 29 of 29 matched, confirmed side by side |

**Metadata module (ExifTool 13.59; NEF, CR2, RW2, DNG, JPEG)**

| Test | Result |
|---|---|
| Dry run writes nothing | OK |
| Rating, label, keywords, IPTC written; RAW to new sidecars, others embedded | OK, RAW checksums unchanged |
| Adding the same keyword twice | No duplicates |
| Read back, including sidecar over embedded for RAW | OK |
| Rename by pattern with RAW+JPG pairs, sidecars and PSD kept together, then undo | OK |
| Refuse duplicate names, unknown tokens and Capture One session folders | OK |

**Cull and match modules (Linux test run against the same images; MCP 1.x and 2.x)**

| Test | Result |
|---|---|
| Client PDF, 29 pictures against 568 candidates | 29 of 29 correct, all high confidence, same 5 crossed out, about 3 s |
| Same page as a screenshot (split into pictures automatically) | 29 of 29 correct, same 5 crossed out |
| Cull 98 images | 23 proposed groups for 25 real situations (pair precision 0.60, recall 0.78); sheets and focus crops returned as images |
| Read-only mode | 15 tools loaded; write mode 22 |

**Metadata written by Schiffer, read on macOS (17 Nikon D850 NEF, Bridge and Capture One 16)**

| Test | Result |
|---|---|
| Rating, keywords incl. hierarchy, creator, copyright in sidecars | Read by Capture One |
| Label "Approved" (Bridge default name) | Shown in Bridge, ignored by Capture One |
| Label "Green" | Green tag in Capture One after reload metadata |
| Existing ratings in sidecars from other apps | Kept |
| Rating, colour tag and keyword set in Capture One, then `sync metadata` | Written to the sidecar (rating 4, label Red, keyword) |
| Same without `sync metadata`, Auto Sync Sidecar XMP not on Full Sync | Sidecar unchanged |

Not yet tested: match on macOS with RAW files; tethered capture; metadata written by Schiffer as seen in Bridge and Capture One on a real job.

Provided as is. Issues and pull requests are welcome, and handled when time allows.

## Related

- [capture-one-mcp](https://github.com/byjustinjones/capture-one-mcp): a broad Capture One MCP server with many fixed tools, read-only by default. Schiffer differs by working across apps and files (metadata, culling, client selections) and by leaving open-ended Capture One scripting to the assistant.
- [c1cmd](https://github.com/kiri11/c1cmd): a Capture One CLI and MCP server focused on image editing, with previews, snapshots and restore. Schiffer is aimed at production workflow and open-ended scripting.
- [ExifTool](https://exiftool.org) by Phil Harvey, used by the metadata module.

## Credits

Concept, requirements and testing by Jeton Kacaniku ([jeton.no](https://jeton.no)). Code written with Claude.

## License

MIT
