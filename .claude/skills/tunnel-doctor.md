# Skill: Tunnel Doctor

**Responsibility:** Diagnose and repair the Mac↔VPS Ollama **SSH reverse tunnel** and its
launchd persistence so the fallback brain is reachable at `localhost:11434` on the VPS, then
prove it with a test completion. This is the **transport/persistence** skill for the fallback
link; `provider-debugger` decides *whether* a request should fail over, `cron-router` *routes*
jobs to it — both assume this tunnel is up.

## How the link works
Ollama runs on the **Mac** (`llama3.1:8b`). A reverse SSH tunnel from the Mac exposes it on
the **VPS** at `localhost:11434`, so Hermes' fallback config (`base_url
http://localhost:11434/v1`) reaches the Mac. A **launchd** agent on the Mac keeps the tunnel
alive across reboots/disconnects. If either the tunnel or launchd dies, every local-routed
turn/cron job fails.

## Method  (read-only first; live edits need backup + approval + validation)
1. **Is it reachable from the VPS? (read-only, the decisive check):**
   - `ssh -i ~/.ssh/hermes_vps hermes@167.233.108.213 'curl -sS -m 5 http://localhost:11434/api/tags'`
     — a JSON model list = tunnel up. Connection refused / timeout = tunnel down.
2. **Locate the failure layer:**
   - On the VPS: `ss -tlnp | grep 11434` (is anything listening?).
   - On the Mac: is Ollama itself up? `curl -sS -m 5 http://localhost:11434/api/tags`.
   - On the Mac: is the tunnel process alive? `pgrep -fl "ssh.*11434"` /
     `launchctl list | grep -i ollama` (or the actual launchd label).
3. **Inspect launchd persistence (read-only):** read the plist under
   `~/Library/LaunchAgents/` (the Ollama-tunnel label) — confirm `KeepAlive`/`RunAtLoad`,
   the `ssh -N -R 11434:localhost:11434 hermes@167.233.108.213` command, and `-i ~/.ssh/hermes_vps`.
4. **Repair (live — backup the plist + approval first):**
   - If Ollama down on Mac: start it / `ollama serve`.
   - If tunnel process dead but launchd ok: `launchctl kickstart -k gui/$(id -u)/<label>`.
   - If launchd misconfigured: back up the plist (`cp <plist>{,.bak.$(date +%Y%m%d_%H%M%S)}`),
     fix the command/KeepAlive, `launchctl unload` then `load` it.
   - SSH is key-only (`~/.ssh/hermes_vps`) — never add a password; fix the key path, not auth mode.
5. **Validate (must pass before declaring fixed):**
   - Re-run step 1 → model list returns.
   - End-to-end completion through the tunnel:
     `ssh -i ~/.ssh/hermes_vps hermes@167.233.108.213 'curl -sS -m 30 http://localhost:11434/api/generate -d "{\"model\":\"llama3.1:8b\",\"prompt\":\"ping\",\"stream\":false}"'`
     → a real `response` field = fallback brain live.
   - Optionally confirm a Hermes job set to the local override now succeeds (`cron-router`).

## Don't
- Don't declare it healthy off `pgrep` alone — the process can be alive while the port isn't
  forwarding; the **VPS-side `curl`** is the only authoritative check.
- Don't switch SSH to password auth to "fix" it — the link is key-only by design.
- Don't restart `hermes-gateway` to fix the tunnel — they're independent; restarting the
  gateway won't bring up a dead launchd tunnel.
- Don't decide failover policy here — that's `provider-debugger`.

## Output
The failing layer (Ollama / tunnel process / launchd / VPS port), the repair applied, the
plist backup path if touched, and the end-to-end test-completion `response` proving
`localhost:11434` is live on the VPS.
