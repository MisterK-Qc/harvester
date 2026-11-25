from flask import Blueprint, current_app, render_template, request, redirect, url_for, session, jsonify
from plexapi.myplex import MyPlexAccount
from plexapi.exceptions import Unauthorized, BadRequest
from requests.exceptions import HTTPError
from ..services.plex_service import connect_to_server
from ..services.config_service import load_config, update_config_key
import logging
import time

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

# Petite durée de cache en session pour éviter de recontacter Plex.tv inutilement
SERVER_CACHE_TTL = 300  # 5 minutes


def _extract_error_message(exc):
    msg = str(exc)

    if "429" in msg or "too_many_requests" in msg or "rate limit exceeded" in msg.lower():
        return (
            "Connexion Plex temporairement bloquée : trop de requêtes ont été envoyées à Plex. "
            "Attends un moment avant de réessayer."
        )

    if "401" in msg or "unauthorized" in msg.lower():
        return "Nom d’utilisateur ou mot de passe Plex invalide."

    return f"Connexion Plex impossible : {msg}"


def _serialize_servers(resources):
    servers = []
    for r in resources:
        if getattr(r, "provides", None) == "server":
            servers.append({
                "name": getattr(r, "name", ""),
                "clientIdentifier": getattr(r, "clientIdentifier", ""),
                "owned": bool(getattr(r, "owned", False)),
                "provides": getattr(r, "provides", ""),
            })
    return servers


def _get_cached_servers():
    cached = session.get("plex_servers_cache")
    cached_at = session.get("plex_servers_cache_ts", 0)

    if cached and (time.time() - cached_at) < SERVER_CACHE_TTL:
        return cached
    return None


def _set_cached_servers(servers):
    session["plex_servers_cache"] = servers
    session["plex_servers_cache_ts"] = time.time()


def _fetch_servers_from_token(plex_token):
    account = MyPlexAccount(token=plex_token)
    resources = account.resources()
    servers = _serialize_servers(resources)
    _set_cached_servers(servers)
    return servers


class NotAdminError(Exception):
    pass


def _resolve_admin_account_id(account, resources):
    """
    Retourne l'ID du compte Plex administrateur de cette instance.

    Un seul compte Plex (le propriétaire) a le droit d'utiliser Harvester. Si aucun
    admin n'est encore enregistré (installation neuve ou mise à jour) :
      1. migration : le compte du PLEX_TOKEN_SCHEDULER déjà enregistré devient l'admin ;
      2. sinon : le premier compte qui se connecte ET possède un serveur Plex.
    Pour changer d'admin : vider PLEX_ADMIN_ACCOUNT_ID dans config.json.
    """
    cfg = load_config()
    admin_id = str(cfg.get("PLEX_ADMIN_ACCOUNT_ID") or "").strip()
    if admin_id:
        return admin_id

    sched_token = str(cfg.get("PLEX_TOKEN_SCHEDULER") or "").strip()
    if sched_token:
        try:
            admin_id = str(MyPlexAccount(token=sched_token).id)
            logger.warning("[AUTH] Admin initialisé depuis PLEX_TOKEN_SCHEDULER (id=%s)", admin_id)
        except Exception:
            admin_id = ""

    if not admin_id:
        owns_server = any(
            getattr(r, "provides", None) == "server" and getattr(r, "owned", False)
            for r in resources
        )
        if not owns_server:
            raise NotAdminError("Ce compte Plex ne possède aucun serveur Plex.")
        admin_id = str(account.id)
        logger.warning("[AUTH] Admin initialisé au premier login (id=%s)", admin_id)

    update_config_key("PLEX_ADMIN_ACCOUNT_ID", admin_id)
    current_app.config["PLEX_ADMIN_ACCOUNT_ID"] = admin_id
    return admin_id


def _find_selected_server_from_live_resources(plex_token, server_name):
    account = MyPlexAccount(token=plex_token)
    resources = account.resources()
    return next((res for res in resources if res.name == server_name), None)


@auth_bp.route("/", methods=["GET", "POST"])
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None

    # Si déjà connecté, on évite de repasser par le login
    if request.method == "GET" and session.get("logged_in") and session.get("plex_token"):
        return redirect(url_for("auth.select_server"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            error = "Nom d’utilisateur ou mot de passe invalide."
            return render_template("login.html", error=error)

        try:
            # IMPORTANT : c'est ici que le signin Plex se fait réellement
            account = MyPlexAccount(username, password)
            plex_token = account.authenticationToken
            resources = account.resources()

            # Seul le compte propriétaire de l'instance est autorisé
            admin_id = _resolve_admin_account_id(account, resources)
            if str(account.id) != admin_id:
                logger.warning("[AUTH] Login refusé pour un compte non-admin : %s", username)
                raise NotAdminError("Ce compte Plex n'est pas autorisé sur cette instance.")

            # Nom affiché sur la page Config — rafraîchi à chaque login (le nom Plex peut changer)
            admin_name = str(getattr(account, "username", "") or username).strip()
            if admin_name and current_app.config.get("PLEX_ADMIN_USERNAME") != admin_name:
                try:
                    update_config_key("PLEX_ADMIN_USERNAME", admin_name)
                    current_app.config["PLEX_ADMIN_USERNAME"] = admin_name
                except Exception:
                    logger.exception("[AUTH] Erreur sauvegarde PLEX_ADMIN_USERNAME")

            session.clear()
            session["plex_token"] = plex_token
            session["logged_in"] = True
            session["plex_username"] = username
            session["auth_admin_id"] = admin_id

            # On profite du login réussi pour mettre les serveurs en cache
            _set_cached_servers(_serialize_servers(resources))

            # Sauvegarder le token pour le scheduler de playlists (pas de session disponible en background)
            try:
                update_config_key("PLEX_TOKEN_SCHEDULER", plex_token)
            except Exception:
                pass

            return redirect(url_for("auth.select_server"))

        except NotAdminError as e:
            error = str(e)
        except Exception as e:
            error = _extract_error_message(e)

    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/select_server", methods=["GET", "POST"])
def select_server():
    if not session.get("logged_in"):
        return redirect(url_for("auth.login"))

    plex_token = session.get("plex_token")
    if not plex_token:
        return redirect(url_for("auth.login"))

    try:
        servers = _get_cached_servers()
        if not servers:
            servers = _fetch_servers_from_token(plex_token)
    except Exception as e:
        return render_template(
            "select_server.html",
            servers=[],
            selected_server="",
            selected_libraries=[],
            error=f"Impossible de charger les serveurs Plex : {_extract_error_message(e)}"
        )

    selected_server = session.get("selected_server", "")
    selected_libraries = session.get("selected_libraries", [])

    if request.method == "POST":
        selected_server = request.form.get("server")
        selected_libraries = request.form.getlist("libraries")

        if not selected_server or not selected_libraries:
            return render_template(
                "select_server.html",
                servers=servers,
                selected_server=selected_server,
                selected_libraries=selected_libraries,
                error="Vous devez choisir un serveur et au moins une bibliothèque."
            )

        selected_res = next((s for s in servers if s["name"] == selected_server), None)
        my_server = next((s for s in servers if s.get("owned")), None)
        my_client_id = my_server.get("clientIdentifier") if my_server else None

        if selected_res and selected_res.get("clientIdentifier") == my_client_id:
            # Serveur local
            session["local_selected_libraries"] = selected_libraries

            # Optionnel : éviter toute confusion visuelle / logique
            session.pop("selected_server", None)
            session.pop("selected_libraries", None)

            return redirect(url_for("my_dashboard.my_dashboard"))

        # Serveur distant
        session["selected_server"] = selected_server
        session["selected_libraries"] = selected_libraries

        return redirect(url_for("dashboard.dashboard"))

    return render_template(
        "select_server.html",
        servers=servers,
        selected_server=selected_server,
        selected_libraries=selected_libraries,
        error=None
    )


@auth_bp.route("/get_libraries")
def get_libraries():
    if not session.get("logged_in"):
        return jsonify([])

    server_name = request.args.get("server")
    plex_token = session.get("plex_token")
    if not plex_token or not server_name:
        return jsonify([])

    try:
        # Ici on doit aller chercher la ressource live pour pouvoir s’y connecter
        target_res = _find_selected_server_from_live_resources(plex_token, server_name)
        if not target_res:
            return jsonify([])

        prefer_local = bool(getattr(target_res, "owned", False))
        server = connect_to_server(target_res, plex_token, prefer_local=prefer_local)
        if not server:
            return jsonify([])

        libraries = [{"title": lib.title, "type": lib.type} for lib in server.library.sections()]
        return jsonify(libraries)

    except Exception:
        return jsonify([])