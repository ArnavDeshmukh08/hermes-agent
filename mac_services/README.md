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

---

## Troubleshooting

- **`garmin_ready: false` on `/health`**: first request failed to authenticate. Check `~/.garmin_env` and retry.
- **`{"error": "garmin_unavailable"}` (503)**: Garmin API is down or credentials are wrong. Check logs.
- **`{"error": "no_data"}`**: Garmin returned data but the device hasn't synced yet for that date.
- **Port already in use**: set `GARMIN_SERVICE_PORT=8766` (or any free port) in `~/.garmin_env` and reload.
