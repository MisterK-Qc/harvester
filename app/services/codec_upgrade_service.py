"""
Détection des titres qui pourraient être re-téléchargés dans un meilleur codec.

Trois niveaux :
  - "confirmed" : une release du même titre existe dans un meilleur codec
                  (index FTP ou serveur ami) — preuve que la version existe.
  - "same"      : le fichier vient d'un service (tag AMZN/NF/… dans le nom)
                  qui offre maintenant un meilleur codec.
  - "possible"  : le titre est disponible (TMDB, région configurée) sur un
                  service qui offre un meilleur codec.

Fichiers (CACHE_DIR) :
  codec_services.json       — table service → codecs offerts (éditable dans l'UI)
  codec_upgrade_skips.json  — titres marqués « vérifié » ou « ignoré »
"""

import glob
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone

from app.config_paths import CACHE_DIR

SERVICES_FILE = os.path.join(CACHE_DIR, "codec_services.json")
SKIPS_FILE = os.path.join(CACHE_DIR, "codec_upgrade_skips.json")

_lock = threading.Lock()

ALL_CODECS = ["h264", "hevc", "vp9", "av1"]

# Rang d'efficacité de compression (plus haut = meilleur / plus petit à qualité égale)
CODEC_RANK = {"av1": 4, "hevc": 3, "vp9": 3, "h264": 2}
LEGACY_RANK = 1  # mpeg2, mpeg4, vc1, msmpeg4…

# Taille cible / taille actuelle, à qualité perçue comparable (approximatif)
_SIZE_RATIO = {
    (1, "h264"): 0.6, (1, "hevc"): 0.4, (1, "vp9"): 0.4, (1, "av1"): 0.35,
    (2, "hevc"): 0.6, (2, "vp9"): 0.6, (2, "av1"): 0.5,
    (3, "av1"): 0.7,
}

DEFAULT_CONFIG = {
    "recheck_days": 90,
    "max_codec": "av1",
    "services": [
        {"tag": "NF",   "name": "Netflix",            "tmdb_names": ["Netflix"],
         "codecs": ["h264", "hevc", "vp9", "av1"], "hdr_codecs": ["hevc"]},
        {"tag": "AMZN", "name": "Amazon Prime Video", "tmdb_names": ["Amazon Prime Video", "Amazon Video"],
         "codecs": ["h264", "hevc", "av1"],        "hdr_codecs": ["hevc"]},
        {"tag": "DSNP", "name": "Disney+",            "tmdb_names": ["Disney Plus", "Disney+"],
         "codecs": ["h264", "hevc", "vp9"],        "hdr_codecs": ["hevc"]},
        {"tag": "ATVP", "name": "Apple TV+",          "tmdb_names": ["Apple TV Plus", "Apple TV+", "Apple TV"],
         "codecs": ["h264", "hevc"],               "hdr_codecs": []},
    ],
}

LEVEL_ORDER = {"confirmed": 0, "same": 1, "possible": 2}


# ─── Normalisation ───────────────────────────────────────────────────────────

def normalize_codec(codec) -> str | None:
    if not codec:
        return None
    c = str(codec).strip().lower().replace(".", "").replace("-", "")
    if c in ("av1", "av01"):
        return "av1"
    if c in ("hevc", "h265", "x265"):
        return "hevc"
    if c in ("vp9", "vp09"):
        return "vp9"
    if c in ("h264", "x264", "avc", "avc1"):
        return "h264"
    return c


def codec_rank(codec) -> int:
    c = normalize_codec(codec)
    if not c:
        return 0
    return CODEC_RANK.get(c, LEGACY_RANK)


_RELEASE_CODEC_RE = [
    (re.compile(r"(?i)(?<![a-z0-9])av1(?![a-z0-9])"), "av1"),
    (re.compile(r"(?i)(?<![a-z0-9])(?:x265|h\.?265|hevc)(?![a-z0-9])"), "hevc"),
    (re.compile(r"(?i)(?<![a-z0-9])vp9(?![a-z0-9])"), "vp9"),
    (re.compile(r"(?i)(?<![a-z0-9])(?:x264|h\.?264|avc)(?![a-z0-9])"), "h264"),
]

_HDR_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:dv|dovi|hdr10\+?|hdr10plus|hdr|hlg|dolby[ ._-]?vision)(?![a-z0-9])"
)


def codec_from_release_name(name: str) -> str | None:
    for rx, codec in _RELEASE_CODEC_RE:
        if rx.search(name or ""):
            return codec
    return None


_RES_RE = re.compile(r"(?i)(?<![0-9])(2160|1080|720|576|480)[pi](?![a-z0-9])")


def res_from_name(name: str) -> int | None:
    m = _RES_RE.search(name or "")
    if m:
        return int(m.group(1))
    if re.search(r"(?i)(?<![a-z0-9])(?:4k|uhd)(?![a-z0-9])", name or ""):
        return 2160
    return None


def hdr_from_name(name: str) -> bool:
    return bool(_HDR_RE.search(name or ""))


def source_tag_from_path(path: str, tags) -> str | None:
    """Tag de service (AMZN, NF…) présent dans le nom de fichier ou son dossier."""
    if not path or not tags:
        return None
    try:
        from app.services.mkvtoolnix_service import normalize_service_tag
    except Exception:
        normalize_service_tag = lambda s: s  # noqa: E731
    parts = re.split(r"[\\/]", path)[-2:]
    alt = "|".join(re.escape(t) for t in sorted(tags, key=len, reverse=True))
    rx = re.compile(rf"(?<![A-Za-z0-9])({alt})(?![A-Za-z0-9])")
    for part in reversed(parts):
        m = rx.search(normalize_service_tag(part))
        if m:
            return m.group(1)
    return None


def _norm_service_name(name: str) -> str:
    n = (name or "").lower().replace("+", " plus")
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def item_key(item: dict) -> str:
    """Clé stable d'un titre pour la liste des vérifiés/ignorés."""
    if item.get("guid"):
        return f"{item.get('type')}|{item['guid']}"
    return f"{item.get('type')}|{(item.get('title') or '').strip().lower()}|{item.get('year') or ''}"


# ─── Config (table des services) ────────────────────────────────────────────

def _read_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_services_config() -> dict:
    data = _read_json(SERVICES_FILE, None)
    if not isinstance(data, dict) or not isinstance(data.get("services"), list):
        return json.loads(json.dumps(DEFAULT_CONFIG))
    data.setdefault("recheck_days", DEFAULT_CONFIG["recheck_days"])
    if data.get("max_codec") not in CODEC_RANK:
        data["max_codec"] = DEFAULT_CONFIG["max_codec"]
    return data


def save_services_config(payload: dict) -> dict:
    """Valide et enregistre la table envoyée par l'UI. Retourne la config enregistrée."""
    current = {s["tag"]: s for s in load_services_config()["services"]}
    services = []
    for s in payload.get("services") or []:
        tag = re.sub(r"[^A-Za-z0-9]", "", str(s.get("tag") or "")).upper()[:10]
        if not tag:
            continue
        base = current.get(tag, {})
        codecs = [c for c in ALL_CODECS if c in (s.get("codecs") or [])]
        hdr_codecs = [c for c in ALL_CODECS if c in (s.get("hdr_codecs") or [])]
        services.append({
            "tag": tag,
            "name": str(s.get("name") or base.get("name") or tag)[:60],
            "tmdb_names": [str(n)[:60] for n in (s.get("tmdb_names") or base.get("tmdb_names") or [])][:10],
            "codecs": codecs,
            "hdr_codecs": hdr_codecs,
        })
    try:
        recheck = max(1, min(3650, int(payload.get("recheck_days", DEFAULT_CONFIG["recheck_days"]))))
    except (TypeError, ValueError):
        recheck = DEFAULT_CONFIG["recheck_days"]
    max_codec = payload.get("max_codec")
    if max_codec not in CODEC_RANK:
        max_codec = DEFAULT_CONFIG["max_codec"]
    data = {"recheck_days": recheck, "max_codec": max_codec, "services": services}
    with _lock:
        _write_json(SERVICES_FILE, data)
    return data


def _service_signature(services) -> str:
    raw = json.dumps(
        sorted((s["tag"], sorted(s.get("codecs", [])), sorted(s.get("hdr_codecs", []))) for s in services)
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# ─── Vérifiés / ignorés ─────────────────────────────────────────────────────

def load_skips() -> dict:
    data = _read_json(SKIPS_FILE, {})
    return data if isinstance(data, dict) else {}


def set_skip(key: str, action: str, sig: str = "", confirmed: bool = False) -> None:
    with _lock:
        skips = load_skips()
        if action == "clear":
            skips.pop(key, None)
        elif action in ("checked", "ignored"):
            skips[key] = {
                "status": action,
                "date": datetime.now(timezone.utc).isoformat(),
                "sig": sig,
                "confirmed": bool(confirmed),
            }
        else:
            raise ValueError("action invalide")
        _write_json(SKIPS_FILE, skips)


# ─── Preuves : index FTP ────────────────────────────────────────────────────

_ftp_lookup_cache = {"sig": None, "movies": {}, "series": {}}


def _ftp_lookup(ftp_ids):
    """
    {norm_title: [(year, codec, hdr, size, name, res)]} pour films,
    {norm_series: [(year, codec, hdr, size, name, res)]} pour séries.
    Seules les releases dont le codec est lisible dans le nom sont gardées.
    """
    from app.services.ftp_index_service import (
        _get_ftp_index_cached, get_ftp_index_file, normalize_series_for_matching,
    )

    sig = []
    for fid in ftp_ids:
        p = get_ftp_index_file(fid)
        try:
            sig.append((fid, os.path.getmtime(p)))
        except OSError:
            sig.append((fid, None))
    sig = tuple(sig)
    if _ftp_lookup_cache["sig"] == sig:
        return _ftp_lookup_cache["movies"], _ftp_lookup_cache["series"]

    movies, series = {}, {}
    for fid in ftp_ids:
        try:
            data, _, _, _ = _get_ftp_index_cached(fid)
        except Exception:
            continue
        for it in data.get("items", []):
            name = it.get("name") or ""
            codec = codec_from_release_name(name) or codec_from_release_name(it.get("path") or "")
            if not codec:
                continue
            entry = (it.get("year"), codec, hdr_from_name(it.get("path") or name), it.get("size"), name,
                     res_from_name(name) or res_from_name(it.get("path") or ""))
            mt = it.get("media_type")
            if mt == "episode":
                nst = it.get("normalized_series_title") or ""
                if nst:
                    series.setdefault(normalize_series_for_matching(nst) or nst, []).append(entry)
            elif mt in ("movie", "unknown") and it.get("season") is None:
                nt = it.get("normalized_title") or ""
                if nt:
                    movies.setdefault(nt, []).append(entry)

    _ftp_lookup_cache.update(sig=sig, movies=movies, series=series)
    return movies, series


def _ftp_evidence(item, movies, series):
    from app.services.ftp_index_service import normalize_title, normalize_series_for_matching

    titles = [t for t in (item.get("title"), item.get("alt_title")) if t]
    cands = []
    if item.get("type") == "show":
        for t in titles:
            cands += series.get(normalize_series_for_matching(t), [])
    else:
        year = item.get("year")
        for t in titles:
            for e in movies.get(normalize_title(t), []):
                if year and e[0] and abs(int(e[0]) - int(year)) > 1:
                    continue
                cands.append(e)
    return cands


# ─── Preuves : serveurs amis (caches du Dashboard) ──────────────────────────

_friend_cache = {"sig": None, "by_guid": {}}


def _friend_lookup():
    """{guid: [(codec, res, server_name)]} depuis les caches dashboard_*.json."""
    paths = sorted(glob.glob(os.path.join(CACHE_DIR, "dashboard_*.json")))
    sig = []
    for p in paths:
        try:
            sig.append((p, os.path.getmtime(p)))
        except OSError:
            pass
    sig = tuple(sig)
    if _friend_cache["sig"] == sig:
        return _friend_cache["by_guid"]

    by_guid = {}
    for p in paths:
        server = os.path.basename(p)[len("dashboard_"):].split("_", 1)[0]
        try:
            with open(p, "r", encoding="utf-8") as f:
                libs = (json.load(f).get("data") or {}).get("libraries") or []
        except Exception:
            continue
        for lib in libs:
            for it in lib.get("comparison") or []:
                guid, codec = it.get("guid"), normalize_codec(it.get("his_codec"))
                if guid and codec:
                    try:
                        res = int(str(it.get("his_res") or "").rstrip("pP") or 0) or None
                    except ValueError:
                        res = None
                    lst = by_guid.setdefault(guid, [])
                    if (codec, res, server) not in lst:
                        lst.append((codec, res, server))

    _friend_cache.update(sig=sig, by_guid=by_guid)
    return by_guid


# ─── Calcul ─────────────────────────────────────────────────────────────────

def _estimate_new_size(size, cur_rank, target_codec):
    if not size:
        return None
    ratio = _SIZE_RATIO.get((min(cur_rank, 3), target_codec))
    return int(size * ratio) if ratio else None


def _human(n):
    if not n:
        return "—"
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if abs(n) < 1024 or unit == "To":
            return f"{n:.1f} {unit}" if unit in ("Go", "To") else f"{n:.0f} {unit}"
        n /= 1024


def compute_upgrades(items, ftp_ids=None):
    """
    items : liste de dicts « codec_items » produits par my_dashboard.
    Retourne {"rows": [...], "summary": {...}, "hidden": int, "config": {...}}.
    """
    config = load_services_config()
    services = config["services"]
    recheck_days = int(config.get("recheck_days", 90))
    max_rank = CODEC_RANK.get(config.get("max_codec"), CODEC_RANK["av1"])
    skips = load_skips()
    now = datetime.now(timezone.utc)

    tmdb_map = {}
    for s in services:
        for n in s.get("tmdb_names") or [s.get("name")]:
            tmdb_map[_norm_service_name(n)] = s

    def service_for_tmdb(name):
        nn = _norm_service_name(name)
        for key, s in tmdb_map.items():
            if nn == key or nn.startswith(key + " "):
                return s
        return None

    try:
        movies, series = _ftp_lookup(ftp_ids or [])
    except Exception:
        movies, series = {}, {}
    try:
        friends = _friend_lookup()
    except Exception:
        friends = {}

    rows, hidden = [], 0
    summary = {lvl: {"count": 0, "gain": 0} for lvl in LEVEL_ORDER}

    for item in items:
        cur = normalize_codec(item.get("codec"))
        cur_rank = codec_rank(cur)
        if not cur_rank:
            continue
        hdr = bool(item.get("hdr"))

        # Services offrant mieux que le codec actuel (en tenant compte du HDR)
        better = {}
        for s in services:
            allowed = s.get("hdr_codecs", []) if hdr else s.get("codecs", [])
            allowed = [c for c in allowed if codec_rank(c) <= max_rank]
            best = max(allowed, key=codec_rank, default=None)
            if best and codec_rank(best) > cur_rank:
                better[s["tag"]] = (s, best)

        # Preuves
        my_res = item.get("resolution") or 0

        def res_ok(r):
            # une preuve en résolution inférieure ne compte pas (tolérance 1080 vs 960/1040…)
            return not r or not my_res or r >= my_res * 0.85

        evidence = []
        for (_, codec, e_hdr, e_size, name, e_res) in _ftp_evidence(item, movies, series):
            if cur_rank < codec_rank(codec) <= max_rank and (e_hdr or not hdr) and res_ok(e_res):
                evidence.append({"src": "ftp", "codec": codec, "label": name, "size": e_size})
        if not hdr and item.get("guid"):
            for codec, e_res, server in friends.get(item["guid"], []):
                if cur_rank < codec_rank(codec) <= max_rank and res_ok(e_res):
                    evidence.append({"src": "friend", "codec": codec, "label": server, "size": None})

        source = item.get("source_tag")
        avail = []
        for name in item.get("streams") or []:
            s = service_for_tmdb(name)
            if s and s["tag"] in better and s["tag"] not in avail:
                avail.append(s["tag"])

        ev = None
        if evidence:
            level = "confirmed"
            ev = max(evidence, key=lambda e: codec_rank(e["codec"]))
            target = ev["codec"]
            via = ev["label"]
            via_src = ev["src"]
        elif source and source in better:
            level, target, via, via_src = "same", better[source][1], source, "service"
        elif avail:
            tag = max(avail, key=lambda t: codec_rank(better[t][1]))
            level, target, via, via_src = "possible", better[tag][1], ", ".join(avail), "service"
        else:
            continue

        size = item.get("size")
        new_size = None
        if ev and ev.get("size") and item.get("type") != "show":
            new_size = ev["size"]
        if new_size is None:
            new_size = _estimate_new_size(size, cur_rank, target)
        gain = max(0, size - new_size) if (size and new_size) else 0

        sig = _service_signature([better[t][0] for t in sorted(better)]) + f"-{max_rank}"
        key = item_key(item)

        skip = skips.get(key)
        skip_state = None
        if skip:
            if skip.get("status") == "ignored":
                skip_state = "ignored"
            else:
                try:
                    age = (now - datetime.fromisoformat(skip["date"])).days
                except Exception:
                    age = recheck_days + 1
                if (
                    age <= recheck_days
                    and skip.get("sig") == sig
                    and not (level == "confirmed" and not skip.get("confirmed"))
                ):
                    skip_state = "checked"
        if skip_state:
            hidden += 1
        else:
            summary[level]["count"] += 1
            summary[level]["gain"] += gain

        rows.append({
            "key": key,
            "title": item.get("title"),
            "year": item.get("year"),
            "type": item.get("type"),
            "lib": item.get("lib"),
            "codec": cur,
            "resolution": item.get("resolution"),
            "hdr": hdr,
            "size": size,
            "size_h": _human(size),
            "estimated": not (ev and ev.get("size") and item.get("type") != "show"),
            "target": target,
            "via": via,
            "via_src": via_src,
            "source_tag": source,
            "level": level,
            "gain": gain,
            "gain_h": _human(gain),
            "sig": sig,
            "skip": skip_state,
            "skip_date": (skip or {}).get("date", "")[:10] if skip_state else "",
        })

    rows.sort(key=lambda r: (LEVEL_ORDER[r["level"]], -r["gain"]))
    for lvl in summary.values():
        lvl["gain_h"] = _human(lvl["gain"])
    return {"rows": rows, "summary": summary, "hidden": hidden, "config": config, "all_codecs": ALL_CODECS}
