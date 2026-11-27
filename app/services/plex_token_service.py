"""
Token Plex de la session courante, conservé côté serveur.

Le cookie de session Flask est signé mais PAS chiffré : son contenu est lisible
par quiconque l'intercepte. Le token Plex n'y est donc plus stocké. Comme seul le
compte admin peut se connecter, son token est celui enregistré au login dans
PLEX_TOKEN_SCHEDULER (config.json, déjà utilisé par les tâches planifiées).
"""
from flask import current_app, session

from .config_service import load_config


def get_plex_token():
    """Token Plex de l'admin connecté, ou None si la session n'est pas authentifiée."""
    if not session.get("logged_in"):
        return None
    token = current_app.config.get("PLEX_TOKEN_SCHEDULER")
    if not token:
        token = load_config().get("PLEX_TOKEN_SCHEDULER")
        if token:
            current_app.config["PLEX_TOKEN_SCHEDULER"] = token
    return token or None
