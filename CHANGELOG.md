# Changelog

## 0.4.1

- Keyword help in the metadata module: `metadata_keyword_list` (your Lightroom/Bridge keyword list plus keywords already in use), `metadata_keyword_sheet` (images with numbers and current keywords for the assistant to look at) and `metadata_write_batch` (different fields to different images in one dry-run-first step). Rules: only what is visible and what the user states; no names or guessed personal traits.
- Capture One instructions: changes made in Capture One reach the XMP sidecar only with Auto Sync Sidecar XMP set to Full Sync or after `sync metadata` (tested). Colour tag numbers documented.
- Contact sheets and culling skip files that cannot be decoded instead of failing, and list them.

- Fixed: RAW thumbnails of portrait frames came out lying down (macOS `sips` ignores the orientation stored in RAW files). Thumbnails are now rotated from the file's orientation tag.
- Labels: default is now colour names (Red, Green ...), which Capture One and Lightroom read. Tested: Capture One 16 ignores Bridge's default names such as "Approved". New setting `SCHIFFER_LABEL_STYLE` (`color` or `bridge`). README tells Bridge users to rename their labels to colour names.
- Fixed: `metadata_read` reported the embedded preview size for RAW files (e.g. 160 x 120 for a D850 NEF). It now reports the full image size, rotated for portrait frames.

## 0.4.0

- **Read-only by default.** Without `SCHIFFER_ALLOW_WRITE=1`, only tools that read are registered. `capture_one_run_applescript` stays available for reading and refuses scripts that change Capture One (checked against the writable properties in your version's dictionary). The installer asks whether to allow changes.
- New `cull` module: `cull_analyze`, `cull_sheet`, `cull_regroup`, `cull_suggest`, `cull_save`. Proposes groups of the same situation from capture time and pixel similarity, measures focus and clipping, and returns contact sheets and focus crops as images for the assistant to judge.
- New `match` module: `match_client_selection` finds the filenames of pictures in a client's PDF, screenshot or contact sheet, in reading order, with red-cross detection and a verification sheet.
- Thumbnails via macOS `sips` (handles RAW), cached in `~/.schiffer/cache`.
- New dependencies: Pillow, NumPy, pypdfium2.
- Installer downloads ExifTool from SourceForge (where exiftool.org now links), then exiftool.org, then GitHub, without printing a curl error.
- Default modules are now `captureone,metadata,cull,match`; the installer updates the old default.

## 0.3.0

- Split into a shared core and modules, enabled with `SCHIFFER_APPS` (default `captureone,metadata`). Only enabled modules' tools and instructions are shown to the assistant.
- New metadata module (ExifTool): read, find, write (rating, label, keywords incl. hierarchical, IPTC) and rename with undo. Dry run by default. RAW files are never written; their metadata goes in XMP sidecars. Rename keeps RAW+JPG pairs, sidecars and same-name PSD/TIFF together and refuses Capture One session folders.
- Installer finds or downloads ExifTool and keeps your own Schiffer settings when reinstalling.
- README: modules, metadata tools, fixed test tables.

## 0.2.0

- Renamed from Kripler to Schiffer (German: skipper). Package, command, config key and install folder are now `schiffer`; the shell switch is `SCHIFFER_ALLOW_SHELL`.
- The installer removes old `kripler` and `capture-one` entries from the Claude Desktop config.
- Versions below were released under the working name Kripler.

## 0.1.7

- Tested finding filenames from a client's selection PDF (29 images among 568, with crossed-out rejects).
- Server instructions: extracting images and marks from a PDF, making thumbnails with AppKit via JXA (Image Events fails on external drives), atlas transfer, verification.

## 0.1.6

- Tested culling 98 rated RAW images to 20.
- Server instructions: culling workflow, judging focus on the eye rather than a whole-frame sharpness metric, albums including clones.

## 0.1.5

- Tested matching a series to a reference image on 16 RAW files.
- Server instructions: how to match a series (read the reference's full grade first, copy settings when light and camera settings are constant), warning about scripted normalize, verifying outlier corrections on a neutral area, AppleScript case-insensitivity and the reserved word "ref".

## 0.1.4

- Tested albums, groups, smart albums, hierarchical keywords and IPTC metadata in a catalog.
- Server instructions: collection creation, smart album rules XML and known keys, how to create keywords on variants, IPTC fields, clones inheriting ratings, transient document errors.

## 0.1.3

- Tested on RAW (Nikon D850 NEF) in a catalog: adjustments, clones, copy/apply adjustments, auto adjust, styles, processing.
- Server instructions extended with RAW and catalog findings: check output folder before processing, order of recipe location settings, apply adjustments takes one variant, styles apply to layers, clone before editing, reserved words.
- README: RAW and catalog test results, note on output folders.

## 0.1.2

- Dictionary search shows exact name matches first, and only the matching members of large classes.
- Server instructions with lessons from testing: recipe defaults (inches, Adobe RGB), silent profile-name failures, variable names that clash with Capture One terms, looping over "whose" results, renaming referenced files on disk, checking output files.
- README: test results and a safety note on renaming referenced files.

## 0.1.1

- Dictionary is read directly from the app bundle. The `sdef` command needs Xcode Command Line Tools and is now only a fallback.
- Script errors are returned as text, so the assistant can read them and correct the script.

## 0.1.0 (unreleased)

- First version.
- Tools: status, scripting dictionary reader, AppleScript and JXA runners, Scripts menu list/run/save.
- Shell commands inside scripts are blocked by default (`KRIPLER_ALLOW_SHELL=1` to allow).
