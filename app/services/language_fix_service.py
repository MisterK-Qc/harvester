"""
Language Fix — corrige l'étiquette de langue d'une piste audio sur un fichier
déjà présent dans la bibliothèque Plex (contrairement à mkvtoolnix_service, qui
traite les fichiers encore dans l'inbox). Plex affiche uniquement la langue
stockée dans le conteneur MKV ; il n'y a aucun moyen de la corriger depuis l'UI
Plex, d'où cet outil.
"""

import logging
import os

from . import mkvtoolnix_service as mkv

logger = logging.getLogger(__name__)


def search_media(plex, query: str, libtype: str = "") -> list:
    kwargs = {"title": query}
    if libtype in ("movie", "show"):
        kwargs["libtype"] = libtype
    results = plex.library.search(**kwargs)[:20]
    return [
        {
            "key":   str(r.ratingKey),
            "title": r.title,
            "year":  getattr(r, "year", None),
            "type":  r.type,
        }
        for r in results
    ]


def list_episodes(plex, show_key: int) -> list:
    show = plex.fetchItem(show_key)
    return [
        {
            "key":     str(ep.ratingKey),
            "season":  ep.seasonNumber,
            "episode": ep.index,
            "title":   ep.title,
        }
        for ep in show.episodes()
    ]


def _display_title(item) -> str:
    if item.type == "episode":
        season  = getattr(item, "parentIndex", None) or 0
        episode = getattr(item, "index", None) or 0
        return f"{item.grandparentTitle} — S{season:02d}E{episode:02d} — {item.title}"
    year = getattr(item, "year", None)
    return f"{item.title} ({year})" if year else item.title


def probe_item(plex, rating_key: int, mkvmerge_path: str):
    """
    Retourne (titre_affiché, parts) où parts est la liste des fichiers de l'item
    (généralement un seul) avec leurs pistes audio actuelles.
    """
    item = plex.fetchItem(rating_key)
    parts_out = []
    part_idx = 0
    for media in item.media:
        for part in media.parts:
            file_path = part.file or ""
            is_mkv = file_path.lower().endswith(".mkv")
            tracks = mkv.extract_languages(file_path, mkvmerge_path) if is_mkv else []
            parts_out.append({
                "part_index": part_idx,
                "file":       file_path,
                "filename":   os.path.basename(file_path),
                "is_mkv":     is_mkv,
                "tracks":     tracks,
            })
            part_idx += 1
    return _display_title(item), parts_out


def apply_fix(plex, rating_key: int, part_index: int, edits: list,
              mkvmerge_path: str, mkvpropedit_path: str) -> list:
    """
    edits : [{"track_index": int, "lang": str}, ...] — track_index 1-based (piste audio).
    Le chemin du fichier est résolu depuis Plex (jamais depuis le client) pour éviter
    qu'un appel malveillant ne cible un fichier arbitraire sur le serveur.
    Déclenche item.analyze() ensuite pour que Plex relise les pistes immédiatement.
    """
    item = plex.fetchItem(rating_key)
    flat_parts = [part for media in item.media for part in media.parts]
    if part_index < 0 or part_index >= len(flat_parts):
        raise ValueError("Fichier introuvable pour cet item")

    file_path = flat_parts[part_index].file or ""
    if not file_path.lower().endswith(".mkv"):
        raise ValueError("La correction de langue n'est possible que sur des fichiers MKV")

    for edit in edits:
        mkv.set_audio_track_language(
            file_path, mkvpropedit_path,
            track_index=int(edit["track_index"]),
            new_lang=edit["lang"],
        )

    try:
        item.analyze()
    except Exception as e:
        logger.warning("[LanguageFix] item.analyze() a échoué pour %s: %s", rating_key, e)

    return mkv.extract_languages(file_path, mkvmerge_path)
