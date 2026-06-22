#!/usr/bin/env python3
"""
Mac-side Garmin fetch service.

Runs on the Mac (home IP), exposes HTTP endpoints reachable from the VPS via
Tailscale.  The VPS IP is blocked by Garmin, so all Garmin API calls originate
here.

Credentials are loaded from ~/.garmin_env (KEY=VALUE lines), with a fallback
to plain environment variables.

Endpoints:
  GET /health
  GET /sleep?date=YYYY-MM-DD
  GET /stats?date=YYYY-MM-DD
  GET /daily_summary
"""

import datetime
import json
import os
import sys

# ---------------------------------------------------------------------------
# Constants / configuration
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("GARMIN_SERVICE_PORT", 8765))
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

def _load_garmin_env_file():
    """Parse ~/.garmin_env (KEY=VALUE, one per line) into a dict."""
    path = os.path.expanduser("~/.garmin_env")
    result = {}
    if not os.path.exists(path):
        return result
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def load_credentials():
    """Return (email, password) from ~/.garmin_env or environment variables."""
    file_creds = _load_garmin_env_file()
    email = file_creds.get("GARMIN_EMAIL") or os.environ.get("GARMIN_EMAIL", "")
    password = file_creds.get("GARMIN_PASSWORD") or os.environ.get("GARMIN_PASSWORD", "")
    return email, password


# ---------------------------------------------------------------------------
# Garmin session management (lazy import)
# ---------------------------------------------------------------------------

_garmin_client = None


def _garmin_login():
    """Create and authenticate a garminconnect.Garmin client."""
    # Lazy import so the module can be imported without garminconnect installed.
    try:
        import garminconnect  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("garminconnect package is not installed") from exc

    email, password = load_credentials()
    if not email or not password:
        raise RuntimeError("GARMIN_EMAIL and GARMIN_PASSWORD must be set")

    client = garminconnect.Garmin(email, password)
    client.login()
    print(json.dumps({"level": "info", "msg": "Garmin login successful", "user": email}))
    return client


def get_garmin_client():
    """Return a logged-in client, logging in if necessary."""
    global _garmin_client
    if _garmin_client is None:
        _garmin_client = _garmin_login()
    return _garmin_client


def _refresh_client():
    """Force a fresh login and update the cached client."""
    global _garmin_client
    _garmin_client = None
    _garmin_client = _garmin_login()
    return _garmin_client


def _call_garmin(fn, *args, **kwargs):
    """
    Call fn(*args, **kwargs) with the cached client, retrying once on any
    error that looks like an auth / availability problem.

    Returns the API result, or raises RuntimeError with a "garmin_unavailable"
    message so callers can return a 503.
    """
    try:
        import garminconnect  # noqa: PLC0415
        auth_errors = (
            garminconnect.GarminConnectAuthenticationError,
            garminconnect.GarminConnectTooManyRequestsError,
        )
    except ImportError:
        auth_errors = ()

    try:
        client = get_garmin_client()
        return fn(client, *args, **kwargs)
    except auth_errors as exc:
        print(json.dumps({"level": "warning", "msg": "Garmin auth error, re-logging", "error": str(exc)}))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"level": "warning", "msg": "Garmin API error, re-logging", "error": str(exc)}))

    # Retry once after a fresh login.
    try:
        client = _refresh_client()
        return fn(client, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"level": "error", "msg": "Garmin unavailable after retry", "error": str(exc)}))
        raise RuntimeError("garmin_unavailable") from exc


# ---------------------------------------------------------------------------
# Data parsers
# ---------------------------------------------------------------------------

def _today_ist() -> str:
    return datetime.datetime.now(IST).strftime("%Y-%m-%d")


def _parse_sleep(raw: dict) -> dict:
    dto = raw.get("dailySleepDTO") or {}
    total_s = dto.get("sleepTimeSeconds") or 0
    deep_s = dto.get("deepSleepSeconds") or 0
    rem_s = dto.get("remSleepSeconds") or 0

    total_h = round(total_s / 3600, 1)
    if total_h == 0:
        return {"error": "no_data"}

    result = {
        "total_sleep_h": total_h,
        "deep_h": round(deep_s / 3600, 1),
        "rem_h": round(rem_s / 3600, 1),
    }

    scores = raw.get("sleepScores") or []
    if scores and isinstance(scores[0], dict):
        score = scores[0].get("value")
        if score is not None:
            result["score"] = score

    return result


def _parse_stats(raw: dict) -> dict:
    if not raw:
        return {"error": "no_data"}

    result = {}

    steps = raw.get("totalSteps")
    if steps is not None:
        result["steps"] = steps

    # Body battery — prefer highestValue, fall back to mostRecent
    bb = raw.get("bodyBatteryHighestValue") or raw.get("bodyBatteryMostRecentValue")
    if bb is not None:
        result["body_battery_high"] = bb

    calories = raw.get("totalKilocalories")
    if calories is not None:
        result["calories"] = int(calories)

    stress = raw.get("averageStressLevel")
    if stress is not None:
        result["stress_avg"] = stress

    if not result:
        return {"error": "no_data"}

    return result


# ---------------------------------------------------------------------------
# Attempt to use Flask; fall back to stdlib http.server
# ---------------------------------------------------------------------------

try:
    import flask as _flask_module
    _USE_FLASK = True
except ImportError:
    _USE_FLASK = False

# ---------------------------------------------------------------------------
# Flask implementation
# ---------------------------------------------------------------------------

if _USE_FLASK:
    from flask import Flask, jsonify, request  # noqa: PLC0415

    app = Flask(__name__)

    @app.route("/health")
    def health():
        ready = _garmin_client is not None
        return jsonify({"status": "ok", "garmin_ready": ready})

    @app.route("/sleep")
    def sleep_endpoint():
        date = request.args.get("date") or _today_ist()
        try:
            raw = _call_garmin(lambda c, d: c.get_sleep_data(d), date)
            return jsonify(_parse_sleep(raw))
        except RuntimeError:
            return jsonify({"error": "garmin_unavailable"}), 503
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"level": "error", "msg": "sleep handler error", "error": str(exc)}))
            return jsonify({"error": "garmin_unavailable"}), 503

    @app.route("/stats")
    def stats_endpoint():
        date = request.args.get("date") or _today_ist()
        try:
            raw = _call_garmin(lambda c, d: c.get_stats(d), date)
            return jsonify(_parse_stats(raw))
        except RuntimeError:
            return jsonify({"error": "garmin_unavailable"}), 503
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"level": "error", "msg": "stats handler error", "error": str(exc)}))
            return jsonify({"error": "garmin_unavailable"}), 503

    @app.route("/daily_summary")
    def daily_summary_endpoint():
        date = _today_ist()
        try:
            sleep_raw = _call_garmin(lambda c, d: c.get_sleep_data(d), date)
            sleep_data = _parse_sleep(sleep_raw)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"level": "error", "msg": "daily_summary sleep error", "error": str(exc)}))
            sleep_data = None

        try:
            stats_raw = _call_garmin(lambda c, d: c.get_stats(d), date)
            stats_data = _parse_stats(stats_raw)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"level": "error", "msg": "daily_summary stats error", "error": str(exc)}))
            stats_data = None

        return jsonify({"date": date, "sleep": sleep_data, "stats": stats_data})

    def run_server():
        print(json.dumps({"level": "info", "msg": f"Starting Flask server on 0.0.0.0:{PORT}"}))
        app.run(host="0.0.0.0", port=PORT)


# ---------------------------------------------------------------------------
# stdlib http.server fallback implementation
# ---------------------------------------------------------------------------

else:
    import urllib.parse as _urlparse
    from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: PLC0415

    def _json_response(handler, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    class GarminHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # suppress default access log noise
            print(json.dumps({"level": "info", "msg": fmt % args}))

        def do_GET(self):  # noqa: N802
            parsed = _urlparse.urlparse(self.path)
            params = dict(_urlparse.parse_qsl(parsed.query))
            path = parsed.path

            if path == "/health":
                ready = _garmin_client is not None
                _json_response(self, {"status": "ok", "garmin_ready": ready})

            elif path == "/sleep":
                date = params.get("date") or _today_ist()
                try:
                    raw = _call_garmin(lambda c, d: c.get_sleep_data(d), date)
                    _json_response(self, _parse_sleep(raw))
                except Exception as exc:  # noqa: BLE001
                    print(json.dumps({"level": "error", "msg": "sleep error", "error": str(exc)}))
                    _json_response(self, {"error": "garmin_unavailable"}, 503)

            elif path == "/stats":
                date = params.get("date") or _today_ist()
                try:
                    raw = _call_garmin(lambda c, d: c.get_stats(d), date)
                    _json_response(self, _parse_stats(raw))
                except Exception as exc:  # noqa: BLE001
                    print(json.dumps({"level": "error", "msg": "stats error", "error": str(exc)}))
                    _json_response(self, {"error": "garmin_unavailable"}, 503)

            elif path == "/daily_summary":
                date = _today_ist()
                try:
                    sleep_raw = _call_garmin(lambda c, d: c.get_sleep_data(d), date)
                    sleep_data = _parse_sleep(sleep_raw)
                except Exception as exc:  # noqa: BLE001
                    print(json.dumps({"level": "error", "msg": "daily_summary sleep error", "error": str(exc)}))
                    sleep_data = None

                try:
                    stats_raw = _call_garmin(lambda c, d: c.get_stats(d), date)
                    stats_data = _parse_stats(stats_raw)
                except Exception as exc:  # noqa: BLE001
                    print(json.dumps({"level": "error", "msg": "daily_summary stats error", "error": str(exc)}))
                    stats_data = None

                _json_response(self, {"date": date, "sleep": sleep_data, "stats": stats_data})

            else:
                _json_response(self, {"error": "not_found"}, 404)

    app = None  # not applicable for stdlib path

    def run_server():
        print(json.dumps({"level": "info", "msg": f"Starting stdlib HTTP server on 0.0.0.0:{PORT}"}))
        server = HTTPServer(("0.0.0.0", PORT), GarminHandler)
        server.serve_forever()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    print(json.dumps({"level": "info", "msg": "Garmin service starting", "port": PORT}))

    try:
        _garmin_login()
        get_garmin_client()  # caches the logged-in client
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "level": "warning",
            "msg": "Initial Garmin login failed — will retry on first request",
            "error": str(exc),
        }))

    run_server()


if __name__ == "__main__":
    main()
