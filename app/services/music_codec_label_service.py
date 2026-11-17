"""
music_codec_label_service.py — Étiquette Plex basée sur le VRAI codec audio
des pistes d'un album (Media.audioCodec), pour contourner la limitation de
Kometa qui refuse l'attribut `audio_codec` pour les bibliothèques musique
("Overlay Error: plex_search attribute 'audio_codec' does not work for
music libraries"). Kometa peut ensuite lire l'étiquette posée ici via un
simple `plex_search: label: FLAC` — même principe que les étiquettes de
service de streaming (plex_label_service.py).

Contrairement aux étiquettes de streaming, le codec d'un fichier ne change
jamais tout seul (seul un re-rip change le fichier) — donc ici on fait une
vraie synchronisation à chaque run (ajoute le label manquant, retire le
label incorrect) plutôt qu'un "pose une seule fois, ne retouche plus jamais".
On ne touche JAMAIS une étiquette qui n'est pas un nom de codec connu.
"""
import logging
import threading
import time

from plexapi.myplex import MyPlexAccount

logger = logging.getLogger(__name__)

# Codecs reconnus — le nom du codec Plex (en majuscules) devient directement
# le nom de l'étiquette Plex.
KNOWN_CODEC_LABELS = {
    "FLAC", "ALAC", "WAV", "APE", "WV", "DSD",  # lossless
    "MP3", "AAC", "OGG", "OPUS", "WMA",          # lossy
}

_job_lock = threading.Lock()
_job_status = {
    "running": False,
    "done": False,
    "total": 0,
    "processed": 0,
    "labeled": 0,
    "unchanged": 0,
    "errors": 0,
    "log": [],
}


def get_job_status():
    with _job_lock:
        s = dict(_job_status)
        s["log"] = list(_job_status["log"])
        return s


def _update(**kwargs):
    with _job_lock:
        _job_status.update(kwargs)


def _log(msg):
    with _job_lock:
        _job_status["log"].append(msg)
        if len(_job_status["log"]) > 500:
            _job_status["log"] = _job_status["log"][-500:]


def _album_codecs(album):
    """Retourne l'ensemble des codecs (en majuscules) trouvés parmi les pistes de l'album."""
    codecs = set()
    for track in album.tracks():
        for media in track.media:
            if media.audioCodec:
                codecs.add(media.audioCodec.upper())
    return codecs


def _worker(plex_token, library_name):
    _update(running=True, done=False, total=0, processed=0,
             labeled=0, unchanged=0, errors=0, log=[])
    try:
        account = MyPlexAccount(token=plex_token)
        server = next(
            (s for s in account.resources()
             if s.provides == "server" and getattr(s, "owned", False)),
            None,
        )
        if not server:
            _log("❌ Serveur Plex local introuvable")
            return

        plex = server.connect()
        section = plex.library.section(library_name)
        albums = section.albums()

        _update(total=len(albums))
        _log(f"▶ {len(albums)} album(s) dans '{library_name}'")

        labeled = unchanged = errors = 0

        for i, album in enumerate(albums):
            try:
                found = _album_codecs(album) & KNOWN_CODEC_LABELS
                current_labels = {lbl.tag for lbl in album.labels}
                current_codec_labels = current_labels & KNOWN_CODEC_LABELS

                to_add = found - current_codec_labels
                to_remove = current_codec_labels - found

                if to_add:
                    album.addLabel(sorted(to_add))
                if to_remove:
                    album.removeLabel(sorted(to_remove))

                if to_add or to_remove:
                    labeled += 1
                    changes = []
                    if to_add:
                        changes.append("+" + ",".join(sorted(to_add)))
                    if to_remove:
                        changes.append("-" + ",".join(sorted(to_remove)))
                    _log(f"✅ {album.parentTitle} - {album.title} → {' '.join(changes)}")
                else:
                    unchanged += 1

                _update(processed=i + 1, labeled=labeled, unchanged=unchanged, errors=errors)

            except Exception as e:
                errors += 1
                _log(f"❌ {album.parentTitle} - {album.title}: {e}")
                _update(processed=i + 1, errors=errors)

            time.sleep(0.05)

        _log(f"✔ Terminé — {labeled} mis à jour, {unchanged} déjà à jour, {errors} erreur(s)")

    except Exception as e:
        _log(f"❌ Erreur fatale: {e}")
        logger.error("[MusicCodecLabelService] Fatal error", exc_info=True)
    finally:
        _update(running=False, done=True)


def start_music_codec_job(plex_token, library_name):
    t = threading.Thread(
        target=_worker,
        args=(plex_token, library_name),
        daemon=True,
    )
    t.start()
