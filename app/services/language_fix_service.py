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
from .config_service import load_config

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


def _resolve_path(plex_path: str) -> str:
    """
    Traduit le chemin tel que rapporté par Plex vers un chemin accessible par ce
    conteneur, via PLEX_PATH_FROM/PLEX_PATH_TO (Config > MKVToolNix). Nécessaire
    quand Plex et Harvester tournent dans des conteneurs Docker séparés avec des
    mappings de volumes différents pour la même bibliothèque (ex: Plex voit
    /data/Film.mkv, Harvester voit /mnt/user/Plex/Film.mkv).
    """
    cfg = load_config()
    prefix_from = cfg.get("PLEX_PATH_FROM", "")
    prefix_to = cfg.get("PLEX_PATH_TO", "")
    if prefix_from and plex_path.startswith(prefix_from):
        return prefix_to + plex_path[len(prefix_from):]
    return plex_path


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
            plex_path = part.file or ""
            file_path = _resolve_path(plex_path)
            exists = os.path.isfile(file_path)
            is_mkv = file_path.lower().endswith(".mkv")

            error = None
            tracks = []
            if not exists:
                error = f"Fichier introuvable sur le conteneur Harvester : {file_path}"
                if file_path != plex_path:
                    error += f" (chemin Plex original : {plex_path})"
                error += " — vérifie PLEX_PATH_FROM / PLEX_PATH_TO dans Config."
            elif is_mkv:
                tracks = mkv.extract_languages(file_path, mkvmerge_path)
                if len(tracks) == 1 and tracks[0].get("lang_code") == "erreur":
                    # Sentinelle extract_languages() : mkvmerge n'a pas pu lire le
                    # fichier (corrompu ?) — ce n'est pas une vraie piste éditable.
                    error = f"MKVToolNix n'a pas pu analyser ce fichier : {file_path}"
                    tracks = []

            parts_out.append({
                "part_index": part_idx,
                "file":       file_path,
                "filename":   os.path.basename(file_path),
                "is_mkv":     is_mkv,
                "exists":     exists,
                "error":      error,
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

    plex_path = flat_parts[part_index].file or ""
    file_path = _resolve_path(plex_path)

    if not os.path.isfile(file_path):
        msg = f"Fichier introuvable sur le conteneur Harvester : {file_path}"
        if file_path != plex_path:
            msg += f" (chemin Plex original : {plex_path})"
        msg += " — vérifie PLEX_PATH_FROM / PLEX_PATH_TO dans Config."
        raise ValueError(msg)

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
