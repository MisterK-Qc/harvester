import json
import logging
import os
import re
from datetime import datetime

from plexapi.myplex import MyPlexAccount

from app.config_paths import CACHE_DIR
from app.services.plex_service import connect_to_server

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = os.path.join(CACHE_DIR, "snapshots")


def _ensure_snapshot_dir():
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)


def _snapshot_path(filename):
    return os.path.join(SNAPSHOT_DIR, filename)


def create_snapshot(plex_token, label=""):
    """
    Connecte au serveur Plex local (owned=True), sérialise toutes les
    bibliothèques (films + séries + épisodes) et sauvegarde le snapshot.
    Retourne le nom du fichier créé.
    """
    account = MyPlexAccount(token=plex_token)
    my_server_res = next(
        (s for s in account.resources() if s.provides == "server" and getattr(s, "owned", False)),
        None,
    )
    if not my_server_res:
        raise RuntimeError("Serveur Plex local introuvable.")

    server = connect_to_server(my_server_res, plex_token, prefer_local=True)
    if not server:
        raise RuntimeError("Impossible de se connecter au serveur Plex local.")

    libraries = []
    for section in server.library.sections():
        items = []
        if section.type == "movie":
            for item in section.all():
                items.append({
                    "title": getattr(item, "title", "") or "",
                    "originalTitle": getattr(item, "originalTitle", "") or "",
                    "year": getattr(item, "year", None),
                    "guid": getattr(item, "guid", "") or "",
                    "type": "movie",
                })
        elif section.type == "show":
            for item in section.all():
                episodes = []
                try:
                    for ep in item.episodes():
                        episodes.append({
                            "season": getattr(ep, "seasonNumber", None),
                            "episode": getattr(ep, "index", None),
                            "title": getattr(ep, "title", "") or "",
                        })
                except Exception:
                    pass
                items.append({
                    "title": getattr(item, "title", "") or "",
                    "originalTitle": getattr(item, "originalTitle", "") or "",
                    "year": getattr(item, "year", None),
                    "guid": getattr(item, "guid", "") or "",
                    "type": "show",
                    "episodes": episodes,
                })
        else:
            # music / photo — titres seulement
            for item in section.all():
                items.append({
                    "title": getattr(item, "title", "") or "",
                    "year": getattr(item, "year", None),
                    "guid": getattr(item, "guid", "") or "",
                    "type": section.type,
                })

        libraries.append({
            "title": section.title,
            "type": section.type,
            "items": items,
        })
        logger.info("[SNAPSHOT] section '%s' — %d items", section.title, len(items))

    _ensure_snapshot_dir()
    now = datetime.now()
    filename = f"snapshot_{now.strftime('%Y-%m-%d_%H-%M-%S')}.json"
    payload = {
        "created_at": now.isoformat(),
        "label": (label or "").strip(),
        "server_name": getattr(my_server_res, "name", ""),
        "libraries": libraries,
    }
    path = _snapshot_path(filename)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    logger.info("[SNAPSHOT] Créé : %s (%d bibliothèques)", filename, len(libraries))
    return filename


def list_snapshots():
    """Retourne la liste des snapshots triée par date décroissante."""
    _ensure_snapshot_dir()
    result = []
    for fname in os.listdir(SNAPSHOT_DIR):
        if not fname.startswith("snapshot_") or not fname.endswith(".json"):
            continue
        path = _snapshot_path(fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            total_items = sum(len(lib.get("items", [])) for lib in data.get("libraries", []))
            result.append({
                "filename": fname,
                "created_at": data.get("created_at", ""),
                "label": data.get("label", ""),
                "server_name": data.get("server_name", ""),
                "total_items": total_items,
                "size_kb": round(os.path.getsize(path) / 1024),
            })
        except Exception:
            continue
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return result


def load_snapshot(filename):
    """Charge un snapshot depuis son nom de fichier."""
    path = _snapshot_path(_safe_filename(filename))
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def delete_snapshot(filename):
    path = _snapshot_path(_safe_filename(filename))
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def _safe_filename(name):
    """Valide le nom de fichier pour éviter la traversée de répertoire."""
    name = os.path.basename(name)
    if not re.match(r'^snapshot_[\d_-]+\.json$', name):
        raise ValueError(f"Nom de snapshot invalide : {name}")
    return name


def _normalize(title):
    return "".join(ch.lower() for ch in (title or "") if ch.isalnum())


def _items_by_type(snap, media_type):
    """Tous les items d'un type, toutes bibliothèques confondues (un déplacement de bibliothèque n'est pas une perte)."""
    return [
        item
        for lib in snap.get("libraries", [])
        if lib.get("type") == media_type
        for item in lib.get("items", [])
    ]


def _guid(item):
    return (item.get("guid") or "").strip()


def _titles(item):
    return {t for t in (_normalize(item.get("title")), _normalize(item.get("originalTitle"))) if t}


def _years_compatible(a, b):
    """Année inconnue d'un côté, ou écart d'un an au plus (corrections de métadonnées Plex)."""
    if not a or not b:
        return True
    try:
        return abs(int(a) - int(b)) <= 1
    except (TypeError, ValueError):
        return a == b


def _match_items(ref_items, cur_items):
    """
    Associe chaque item de référence à un item actuel, du critère le plus sûr au plus souple :
      1. GUID identique (local:// compris : même ID local = même item)
      2. titre ou titre original + année identique
      3. titre seul, si les années sont compatibles (une inconnue, ou ±1 an)
    Le palier 3 couvre les items non identifiés par Plex (local://, année vide) puis rematchés
    ou réimportés sous un nouvel ID. Logique d'ensemble : un item en double dans la référence
    (ex. présent dans 2 bibliothèques) n'est pas « manquant » s'il existe encore une fois ;
    on préfère seulement un item actuel pas encore associé, pour bien apparier les épisodes.
    Retourne ([(ref, cur_ou_None), ...], {index des items actuels associés}).
    """
    by_guid, by_title_year, by_title = {}, {}, {}
    for i, cur in enumerate(cur_items):
        guid = _guid(cur)
        if guid:
            by_guid.setdefault(guid, []).append(i)
        for t in _titles(cur):
            by_title.setdefault(t, []).append(i)
            if cur.get("year"):
                by_title_year.setdefault((t, cur.get("year")), []).append(i)

    used = set()
    pairs = [None] * len(ref_items)

    def _take(ref_idx, candidates):
        if not candidates:
            return False
        i = next((c for c in candidates if c not in used), candidates[0])
        used.add(i)
        pairs[ref_idx] = (ref_items[ref_idx], cur_items[i])
        return True

    # Palier par palier sur tous les items, pour qu'un match sûr ne soit pas volé par un match souple
    for idx, ref in enumerate(ref_items):
        guid = _guid(ref)
        if guid:
            _take(idx, by_guid.get(guid, []))

    for idx, ref in enumerate(ref_items):
        if pairs[idx] is None and ref.get("year"):
            for t in _titles(ref):
                if _take(idx, by_title_year.get((t, ref.get("year")), [])):
                    break

    for idx, ref in enumerate(ref_items):
        if pairs[idx] is not None:
            continue
        for t in _titles(ref):
            candidates = [
                i for i in by_title.get(t, [])
                if _years_compatible(ref.get("year"), cur_items[i].get("year"))
            ]
            if _take(idx, candidates):
                break

    pairs = [p if p is not None else (ref_items[i], None) for i, p in enumerate(pairs)]
    return pairs, used


def diff_snapshots(snap_ref, snap_current):
    """
    Compare snap_ref (référence, plus ancien) vs snap_current (état actuel).
    Retourne un dict :
      {
        "missing_movies": [...],   # films présents dans ref mais absents dans current
        "missing_shows": [...],    # séries entièrement absentes
        "partial_shows": [...],    # séries présentes mais avec des épisodes manquants
        "new_movies": [...],       # films nouveaux dans current (pas dans ref)
        "new_shows": [...],        # séries nouvelles dans current
      }
    """
    ref_movies = _items_by_type(snap_ref, "movie")
    cur_movies = _items_by_type(snap_current, "movie")
    ref_shows = _items_by_type(snap_ref, "show")
    cur_shows = _items_by_type(snap_current, "show")

    # Films manquants / nouveaux
    movie_pairs, movie_matched_cur = _match_items(ref_movies, cur_movies)
    missing_movies = [ref for ref, cur in movie_pairs if cur is None]
    new_movies = [c for i, c in enumerate(cur_movies) if i not in movie_matched_cur]

    # Séries : manquantes entièrement ou partiellement
    show_pairs, show_matched_cur = _match_items(ref_shows, cur_shows)
    missing_shows = []
    partial_shows = []
    new_shows = [c for i, c in enumerate(cur_shows) if i not in show_matched_cur]

    for ref_show, cur_show in show_pairs:
        if cur_show is None:
            missing_shows.append(ref_show)
            continue

        ref_eps = {(ep.get("season"), ep.get("episode")) for ep in ref_show.get("episodes", [])}
        cur_eps = {(ep.get("season"), ep.get("episode")) for ep in cur_show.get("episodes", [])}

        missing_eps = sorted(ref_eps - cur_eps, key=lambda x: (x[0] or 0, x[1] or 0))
        if missing_eps:
            partial_shows.append({
                "title": ref_show.get("title"),
                "year": ref_show.get("year"),
                "guid": ref_show.get("guid"),
                "missing_episodes": [
                    {"season": s, "episode": e} for s, e in missing_eps
                ],
            })

    # Trier par titre
    def _sort_title(x):
        return _normalize(x.get("title") or "")

    # Un même titre en double (ex. 2 bibliothèques) n'est listé qu'une fois
    def _dedupe(items):
        seen, out = set(), []
        for x in items:
            key = _guid(x) or f"{_normalize(x.get('title'))}:{x.get('year') or ''}"
            if key not in seen:
                seen.add(key)
                out.append(x)
        return out

    missing_movies = _dedupe(missing_movies)
    new_movies = _dedupe(new_movies)
    missing_shows = _dedupe(missing_shows)
    partial_shows = _dedupe(partial_shows)
    new_shows = _dedupe(new_shows)

    missing_movies.sort(key=_sort_title)
    new_movies.sort(key=_sort_title)
    missing_shows.sort(key=_sort_title)
    partial_shows.sort(key=_sort_title)
    new_shows.sort(key=_sort_title)

    return {
        "missing_movies": missing_movies,
        "missing_shows": missing_shows,
        "partial_shows": partial_shows,
        "new_movies": new_movies,
        "new_shows": new_shows,
    }
