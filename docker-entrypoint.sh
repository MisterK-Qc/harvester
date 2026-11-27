#!/bin/sh
# Harvester — démarrage du conteneur.
#
# Par défaut (PUID/PGID non définis) : exécution en root, comme avant.
# Si PUID et PGID sont définis (Unraid : PUID=99, PGID=100 = nobody:users),
# l'application tourne avec cet utilisateur : un bug exploité dans l'app ne
# donne alors pas les droits root dans le conteneur.
set -e

if [ -n "${PUID:-}" ] && [ -n "${PGID:-}" ] && [ "$(id -u)" = "0" ]; then
    CONFIG_DIR="${HARVESTER_CONFIG_DIR:-/config}"
    mkdir -p "$CONFIG_DIR" /inbox/FTP

    # Les fichiers créés avant (en root) doivent rester modifiables par PUID:PGID
    if [ "$(stat -c '%u:%g' "$CONFIG_DIR")" != "${PUID}:${PGID}" ] \
       || find "$CONFIG_DIR" \( ! -uid "$PUID" -o ! -gid "$PGID" \) -print -quit | grep -q .; then
        echo "[entrypoint] chown -R ${PUID}:${PGID} ${CONFIG_DIR}"
        chown -R "${PUID}:${PGID}" "$CONFIG_DIR" /inbox
    fi

    # spotdl et autres outils écrivent dans $HOME : un dossier persistant et accessible
    export HOME="$CONFIG_DIR/.home"
    mkdir -p "$HOME"
    chown "${PUID}:${PGID}" "$HOME"

    umask "${UMASK:-0002}"
    echo "[entrypoint] Démarrage en tant que ${PUID}:${PGID}"
    exec gosu "${PUID}:${PGID}" "$@"
fi

exec "$@"
