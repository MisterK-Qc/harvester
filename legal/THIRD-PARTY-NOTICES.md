# Third-Party Notices — Harvester

This file lists every third-party component distributed with, or invoked by, **Harvester**
(formerly "PlexHarvester"), together with its license. It must be kept alongside any
distributed copy of the software (Docker image, installer, source archive, Unraid template, etc.).

Generated: 2026-11-15, from `requirements.txt` + `Dockerfile` of the `plex-compare` codebase.
Regenerate whenever a dependency is added, removed, or upgraded (see "How to regenerate" below).

---

## 1. Python packages (installed via `requirements.txt`)

Harvester directly depends on:

| Package | Version | License |
|---|---|---|
| Flask | 2.3.3 | BSD License |
| plexapi | 4.18.1 | BSD-3-Clause |
| gunicorn | 27.0.1 | MIT |
| pymediainfo | 7.0.1 | MIT License |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| spotdl | 4.6.0 | MIT |

`spotdl` pulls in a large dependency tree (yt-dlp, mutagen, pykakasi, Unidecode, fastapi,
pymongo, etc.). The **full list of ~70 packages actually installed**, each with its
complete verbatim license text, author, and project URL, is in:

➡️ **[`THIRD-PARTY-LICENSES-PYTHON.txt`](./THIRD-PARTY-LICENSES-PYTHON.txt)**

Three packages in that tree use a **copyleft (GPL-family) license**:

| Package | License | Pulled in by |
|---|---|---|
| mutagen | GPL-2.0-or-later | spotdl |
| pykakasi | GPL-3.0-or-later | spotdl (syncedlyrics/ytmusicapi chain) |
| Unidecode | GPL-2.0-or-later | spotdl (slugify chain) |
| text-unidecode | GPL-2.0+ / Artistic (dual) | spotdl |

One package reports **no license metadata at all**:

| Package | License | Note |
|---|---|---|
| spotapi | UNKNOWN | Pulled in by spotdl. Check the upstream repository by hand before any commercial release — see `LICENSING-RISK-NOTES.md` §3. |

See **`LICENSING-RISK-NOTES.md`** for what this means for a paid/commercial distribution of
Harvester and why it is currently **not a blocking problem** given how `spotdl` is invoked.

---

## 2. External binaries (bundled in the Docker image, called as separate OS processes)

These are **not** Python libraries — they are standalone programs installed via `apt-get`
(or compiled in a build stage) and invoked through `subprocess`. Harvester's own code never
links against them; it only shells out to them and reads their output/exit code.

| Tool | License | Role in Harvester | Upstream source |
|---|---|---|---|
| MKVToolNix (`mkvmerge`, `mkvpropedit`) | GPL-2.0-or-later (with some LGPL-2.1+ components) | Remux / edit MKV track metadata (`mkvtoolnix_service.py`) | https://gitlab.com/mbunkus/mkvtoolnix |
| MediaInfo (`mediainfo` CLI / libmediainfo) | BSD-2-Clause | Probe media file technical metadata (`pymediainfo` wraps it) | https://github.com/MediaArea/MediaInfoLib |
| FFmpeg | LGPL-2.1-or-later, with GPL-2-or-later components when built with `--enable-gpl` (Debian's `apt` package is a GPL build) | Audio track extraction/analysis (Multi Audio Merger, `multi_audio_service.py`) | https://ffmpeg.org / https://salsa.debian.org/multimedia-team/ffmpeg |
| alass-cli | GPL-3.0 | Automatic subtitle synchronization | https://github.com/kaegi/alass |

Because the compiled binaries themselves are **copied into and shipped inside the Docker
image**, distributing that image counts as "distribution" of GPL-licensed software, which
carries its own obligations — see `LICENSING-RISK-NOTES.md` §2. The relevant license texts
are included verbatim in [`licenses/GPL-2.0.txt`](./licenses/GPL-2.0.txt) and
[`licenses/GPL-3.0.txt`](./licenses/GPL-3.0.txt).

---

## 3. Frontend resources loaded by the web UI

| Resource | License / Terms | Loaded from |
|---|---|---|
| Chart.js | MIT License | `https://cdn.jsdelivr.net/npm/chart.js` (CDN, not vendored) |
| flagcdn.com flag images | Free service by Flagpedia.net; no explicit license, backlink appreciated but not contractually required | `https://flagcdn.com/...` (CDN, not vendored) |

Because both are loaded live from a CDN rather than bundled into the app, Harvester itself
does not "distribute" these files — but if you later vendor (self-host) either one, add its
license text to this document and to `licenses/`.

---

## 4. Trademarks

See [`TRADEMARKS.md`](./TRADEMARKS.md) for Harvester's branding and disclaimer policy
regarding the third-party platforms and services it integrates with.

---

## How to regenerate the Python license list

```bash
python -m venv /tmp/license-check && /tmp/license-check/bin/pip install -r requirements.txt pip-licenses
PYTHONUTF8=1 /tmp/license-check/bin/python -m piplicenses --format=plain-vertical --with-license-file --no-license-path --with-urls --with-authors > legal/THIRD-PARTY-LICENSES-PYTHON.txt
```

Re-run this after any change to `requirements.txt`, and re-check §1/§2 above for any newly
introduced copyleft or no-license packages.
