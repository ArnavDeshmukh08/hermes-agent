# Mac-side Garmin Service

This service runs on your Mac (home IP) and exposes Garmin data over HTTP,
reachable from the VPS via Tailscale.  The VPS IP is blocked by Garmin, so
all Garmin API calls originate from the Mac.

**Mac Tailscale IP:** `100.120.65.115`  
**Port:** `8765`

---

## 1. Create `~/.garmin_env`

```
GARMIN_EMAIL=your@email.com
GARMIN_PASSWORD=yourpassword
```

Secure the file so only you can read it:

```bash
chmod 600 ~/.garmin_env
```

---

## 2. Install the garminconnect Python package (if not already installed)

```bash
pip3 install garminconnect
# or, if you use pipx / a venv, activate it first
```

---

## 3. Copy the plist into your LaunchAgents folder

```bash
cp "/Users/arnav/Documents/Hermes Agent/mac_services/com.hermes.garmin.plist" \
   ~/Library/LaunchAgents/
```

---

## 4. Load the service

```bash
launchctl load ~/Library/LaunchAgents/com.hermes.garmin.plist
```

---

## 5. Verify locally

```bash
curl http://localhost:8765/health
# Expected: {"garmin_ready": true, "status": "ok"}

curl http://localhost:8765/sleep
curl http://localhost:8765/stats
curl http://localhost:8765/daily_summary
```

---

## 6. Verify from the VPS (via Tailscale)

```bash
curl http://100.120.65.115:8765/health
curl http://100.120.65.115:8765/daily_summary
```

---

## 7. Tail logs

```bash
# stdout (JSON-structured log lines)
tail -f /tmp/garmin-service.log

# stderr
tail -f /tmp/garmin-service-error.log
```

---

## 8. Reload after changes

```bash
launchctl unload ~/Library/LaunchAgents/com.hermes.garmin.plist
launchctl load  ~/Library/LaunchAgents/com.hermes.garmin.plist
```

---

## Endpoints

| Method | Path | Query | Description |
|--------|------|-------|-------------|
| GET | `/health` | — | Liveness check; `garmin_ready` shows whether session is cached |
| GET | `/sleep` | `?date=YYYY-MM-DD` (optional, default today IST) | Sleep totals and score |
| GET | `/stats` | `?date=YYYY-MM-DD` (optional, default today IST) | Steps, body battery, calories, stress |
| GET | `/daily_summary` | — | Today's sleep + stats merged |
| GET | `/workouts` | `?days=N` (optional, default 7, max 30) | Running workouts for the last N days |

### `/workouts` response format

```json
{
  "workouts": [
    {
      "activity_id": 123456789,
      "activity_name": "Morning Run",
      "start_time_local": "2026-06-29 06:30:00",
      "distance_km": 10.5,
      "duration_s": 3180,
      "avg_pace_min_km": 5.05,
      "is_run": true
    }
  ],
  "count": 1
}
```

- `avg_pace_min_km` is `null` when `averageSpeed` is 0 or missing.
- An empty `workouts` list with `count: 0` is returned when there are no runs in the window (not an error).
- Returns `{"error": "garmin_unavailable"}` with HTTP 503 on Garmin API failure.

---

## Troubleshooting

- **`garmin_ready: false` on `/health`**: first request failed to authenticate. Check `~/.garmin_env` and retry.
- **`{"error": "garmin_unavailable"}` (503)**: Garmin API is down or credentials are wrong. Check logs.
- **`{"error": "no_data"}`**: Garmin returned data but the device hasn't synced yet for that date.
- **Port already in use**: set `GARMIN_SERVICE_PORT=8766` (or any free port) in `~/.garmin_env` and reload.

---
---

# Mac-side Google + Orsa Service (`jack_google_service.py`)

Same Tailscale pattern as Garmin: the Google OAuth token (`~/.hermes/google_token.json`)
and the Orsa SQLite DB both live on the Mac, so the Mac serves Calendar/Gmail/Orsa data to
the VPS gateway. **Read + draft only — there is no send endpoint.**

**Mac Tailscale IP:** `100.120.65.115`  ·  **Port:** `8770`

## 1. Prereqs (one-time)
- Google connected: `python3 bin/jack_google_auth.py` succeeded (token at `~/.hermes/google_token.json`).
- Deps installed in the Mac's python: `pip install -r integrations/requirements.txt`.
- (Optional) Apollo: `JACK_APOLLO_API_KEY=...` in `~/.hermes/.env`.

## 2. (Recommended) set a shared secret
Gmail is more sensitive than Garmin, so gate the service with a token. Put the SAME value in:
- the plist's `JACK_MAC_SERVICE_TOKEN` env (edit `com.hermes.jack-google.plist` before loading), and
- the VPS `~/.hermes/.env` as `JACK_MAC_SERVICE_TOKEN=...`.

With it set, every request must carry a matching `X-Jack-Token` header (the VPS proxy adds it).

## 3. Install + load
```bash
cp "/Users/arnav/Documents/Hermes Agent/mac_services/com.hermes.jack-google.plist" \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.hermes.jack-google.plist
```

## 4. Verify locally
```bash
curl http://localhost:8770/health          # {"status":"ok","google_connected":true,"orsa_connected":true}
curl http://localhost:8770/orsa/new_count?days=7
curl http://localhost:8770/gmail/unread?max=3
# (if a token is set, add:  -H "X-Jack-Token: <your-secret>" )
```

## 5. Turn on the VPS side
In the VPS `~/.hermes/.env`:
```
JACK_GOOGLE_REMOTE=1
JACK_GOOGLE_SERVICE_URL=http://100.120.65.115:8770
JACK_CALENDAR_ENABLED=1
JACK_MAC_SERVICE_TOKEN=<same secret as the Mac, if you set one>
```
Then restart the gateway. Jack now answers "what's on my calendar", "any new leads in Orsa this
week", "any unread email", and "draft a reply to …" using live Mac-side data.

## Endpoints
| Method | Path | Query / body | Description |
|--------|------|--------------|-------------|
| GET | `/health` | — | Liveness + google/orsa readiness |
| GET | `/calendar/events` | — | Today's events + a summary string |
| GET | `/gmail/unread` | `?max=N` (≤50) | Unread business mail (slim dicts) |
| GET | `/gmail/message` | `?id=...` | One message with decoded body (draft context) |
| POST | `/gmail/draft` | `{to,subject,body,thread_id?,in_reply_to?}` | Create a draft (NEVER sends) |
| GET | `/orsa/status` | — | `{connected}` |
| GET | `/orsa/count` | — | `{total}` |
| GET | `/orsa/new_count` | `?days=N` | `{count}` new leads in window (exact) |
| GET | `/orsa/summary` | `?days=N` | `{summary}` human list |
| GET | `/contacts/flags` | — | Unread senders not in Orsa (Apollo-enriched) |

Logs: `/tmp/jack-google-service.log` · `/tmp/jack-google-service-error.log`.

## Keep the Mac awake (so the bridge stays reachable)
The bridge only answers while the Mac is awake. `com.hermes.jack-caffeinate.plist` runs
`caffeinate -i -s -m`, which prevents idle/system sleep **only while on AC power** (battery
behaviour is untouched; the display may still sleep — only the system stays awake).

```bash
cp "/Users/arnav/Documents/Hermes Agent/mac_services/com.hermes.jack-caffeinate.plist" \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.hermes.jack-caffeinate.plist
# verify:
pmset -g assertions | grep PreventUserIdleSystemSleep   # → 1
# turn it off again:
launchctl unload ~/Library/LaunchAgents/com.hermes.jack-caffeinate.plist
```

**Caveats:** keep the Mac **plugged in** for guaranteed uptime. With the lid **closed on
battery** it will still sleep (macOS clamshell). Closed-lid stays awake only when on power
(and, on some setups, with an external display attached).
