"""
playlist_defaults_service.py — Playlists d'univers maison (remplacement Trakt).

Fichiers : app/playlist_defaults/<slug>.json
Format :
  {
    "name":    "Middle-earth Universe",
    "summary": "...",
    "items":   [{"title": ..., "year": ..., "type": "movie", "tmdb_id": ..., ...}, ...]
  }

Les clés d'item reprennent la forme normalisée de trakt_service.normalize_items()
(title, year, type, tmdb_id, imdb_id, tvdb_id, et pour épisodes/saisons :
show_title, season, episode, show_tmdb_id, show_tvdb_id, show_imdb_id) — aucune
adaptation nécessaire côté matching (playlist_service.match_trakt_items).
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "playlist_defaults")


def list_default_playlists() -> list:
    """Scanne app/playlist_defaults/*.json et retourne les résumés disponibles."""
    result = []
    if not os.path.isdir(DEFAULTS_DIR):
        return result

    for fname in sorted(os.listdir(DEFAULTS_DIR)):
        if not fname.endswith(".json"):
            continue
        slug = fname[:-5]
        try:
            with open(os.path.join(DEFAULTS_DIR, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            result.append({
                "slug":       slug,
                "name":       data.get("name", slug),
                "summary":    data.get("summary", ""),
                "item_count": len(data.get("items", [])),
            })
        except Exception:
            logger.warning("[PLAYLIST DEFAULTS] Fichier illisible ignoré: %s", fname, exc_info=True)

    return result


def _normalize_item(raw: dict) -> dict:
    """Garantit les clés attendues par match_trakt_items/_build_preview_response,
    au cas où un fichier d'univers écrit à la main en omettrait certaines."""
    item = {
        "type":     raw.get("type", "movie"),
        "title":    raw.get("title", ""),
        "year":     raw.get("year"),
        "tmdb_id":  raw.get("tmdb_id"),
        "imdb_id":  raw.get("imdb_id"),
        "tvdb_id":  raw.get("tvdb_id"),
    }
    if item["type"] in ("episode", "season"):
        item["show_title"]   = raw.get("show_title", "")
        item["season"]       = raw.get("season")
        item["show_tmdb_id"] = raw.get("show_tmdb_id")
        item["show_tvdb_id"] = raw.get("show_tvdb_id")
        item["show_imdb_id"] = raw.get("show_imdb_id")
        if item["type"] == "episode":
            item["episode"] = raw.get("episode")
    return item


def load_default_playlist(slug: str) -> tuple:
    """
    Charge app/playlist_defaults/<slug>.json.
    Retourne (name, items). Lève ValueError si le slug est inconnu.
    """
    safe_slug = os.path.basename(slug)
    path = os.path.join(DEFAULTS_DIR, f"{safe_slug}.json")
    if not os.path.isfile(path):
        raise ValueError(f"Playlist d'univers inconnue : '{slug}'")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    name  = data.get("name", safe_slug)
    items = [_normalize_item(raw) for raw in data.get("items", [])]
    logger.info("[PLAYLIST DEFAULTS] '%s' chargée (%d items)", name, len(items))
    return name, items
