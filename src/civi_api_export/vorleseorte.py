"""
Flask-Blueprint: GET /api/v1/vorleseorte  ->  GeoJSON {ort, anzahl} pro Stadt.

Einbinden in das bestehende Backend (2 Zeilen):
    from vorleseorte import bp as vorleseorte_bp
    app.register_blueprint(vorleseorte_bp)

Benötigte Umgebungsvariablen (die ersten zwei hat das Backend schon):
    CIVICRM_BASE_URL, CIVICRM_API_KEY
Optional:
    VORLESEORTE_ACTIVITY_TYPE   (Standard: "Anmeldung Vorlesetag 2026")
    VORLESEORTE_FIELD           (Standard: "Benutzerdefinierte_Felder_Bundesweiter_Vorlesetag_2026.Vorleseort")
    VORLESEORTE_CACHE_SECONDS   (Standard: 600)
    VORLESEORTE_CORS_ORIGINS    (Standard: https://omasforfuture.de,https://www.omasforfuture.de)
    VORLESEORTE_GEOJSON         (Pfad zu germany-cities.geojson; Standard: Datei neben geo_lookup.py)
    VORLESEORTE_OVERRIDES       (Pfad zu overrides.csv; Standard: Datei neben geo_lookup.py)
    VORLESEORTE_IGNORE          (Pfad zu ignore.txt; Standard: Datei neben geo_lookup.py)
    Alle drei Dateien MÜSSEN existieren (overrides/ignore dürfen leer sein), sonst startet das Backend nicht.
    VORLESEORTE_ADMIN_TOKEN     (schaltet /api/v1/vorleseorte/review frei; ohne Token gibt es den Endpoint nicht)
"""
import hmac
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Blueprint, jsonify, request

from civi_api_export.geo_lookup import Orte, to_geojson

bp = Blueprint("vorleseorte", __name__)

_orte = None
_cache = {"geo": None, "review": None, "ts": 0.0}
_lock = threading.Lock()


def _cfg(name, default):
    return os.environ.get(name, default)


@bp.record_once
def _load_at_startup(state):
    """Beim Registrieren laden: fehlt eine Datei, startet das Backend gar nicht erst (statt erst beim 1. Aufruf zu scheitern)."""
    global _orte
    _orte = Orte()


def fetch_texts():
    """Ein Freitext pro Anmeldung (Aktivität) aus CiviCRM APIv4."""
    field = _cfg("VORLESEORTE_FIELD", "Benutzerdefinierte_Felder_Bundesweiter_Vorlesetag_2026.Vorleseort")
    params = {
        "select": [field],
        "where": [["activity_type_id:name", "=", _cfg("VORLESEORTE_ACTIVITY_TYPE", "Anmeldung Vorlesetag 2026")],
                  ["is_test", "=", False], ["is_deleted", "=", False]],
        "limit": 0,
    }
    resp = requests.post(
        f"{os.environ['CIVICRM_BASE_URL']}/civicrm/ajax/api4/Activity/get",
        params={"params": json.dumps(params)},
        headers={"X-Civi-Auth": f"Bearer {os.environ['CIVICRM_API_KEY']}", "User-Agent": "Vorleseorte Exporter"},
        timeout=30,
    )
    resp.raise_for_status()
    return [r[field] for r in resp.json()["values"] if (r.get(field) or "").strip()]


def _refresh():
    global _orte
    if _orte is None:
        _orte = Orte()                  # einmalig laden (ca. 2 s)
    _orte.reload_overrides()            # Änderungen an overrides.csv greifen ohne Neustart
    texts = fetch_texts()
    geo, review = to_geojson(texts, _orte)
    geo["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    geo["total"] = sum(f["properties"]["anzahl"] for f in geo["features"])
    _cache.update(geo=geo, review=review, ts=time.time())
    logging.info("Vorleseorte: %d Anmeldungen, %d Orte, %d zu prüfen, %d nicht gefunden, %d ignoriert",
                 len(texts), len(geo["features"]), len(review["zu_pruefen"]), len(review["nicht_gefunden"]),
                 review["ignoriert"])


def get_cached():
    ttl = int(_cfg("VORLESEORTE_CACHE_SECONDS", "600"))
    if _cache["geo"] is not None and time.time() - _cache["ts"] < ttl:
        return _cache
    with _lock:                                          # nur ein Thread fragt Civi, die anderen warten
        if _cache["geo"] is not None and time.time() - _cache["ts"] < ttl:
            return _cache
        try:
            _refresh()
        except Exception as e:
            logging.error("Vorleseorte: Aktualisierung fehlgeschlagen: %s", e)
            if _cache["geo"] is None:
                raise
            _cache["ts"] = time.time() - ttl + 60        # alte Daten weiter ausliefern, in 60 s nochmal versuchen
    return _cache


@bp.route("/api/v1/vorleseorte")
def vorleseorte():
    try:
        c = get_cached()
    except Exception:
        return {"error": "api error"}, 502
    resp = jsonify(c["geo"])
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@bp.route("/api/v1/vorleseorte/review")
def review():
    """Nur für dich: unsichere und nicht gefundene Freitexte. Enthält Rohtexte -> nicht öffentlich!"""
    token = _cfg("VORLESEORTE_ADMIN_TOKEN", "")
    if not token or not hmac.compare_digest(request.headers.get("X-Admin-Token", ""), token):
        return {"error": "not found"}, 404
    try:
        return jsonify(get_cached()["review"])
    except Exception:
        return {"error": "api error"}, 502


@bp.after_request
def cors(resp):
    if request.endpoint == "vorleseorte.vorleseorte":    # nur der öffentliche Endpoint, nie /review
        allowed = [o.strip() for o in _cfg("VORLESEORTE_CORS_ORIGINS",
                   "https://omasforfuture.de,https://www.omasforfuture.de").split(",") if o.strip()]
        origin = request.headers.get("Origin")
        if origin in allowed:
            resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers.add("Vary", "Origin")
    return resp
