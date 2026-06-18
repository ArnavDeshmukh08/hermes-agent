# Skill: Provider Debugger

**Responsibility:** Diagnose LLM provider/model routing, auth, fallback, and rate-limit
behavior — Groq primary, local Ollama fallback, and per-job overrides. Not context size
(`context-compressor`) and not Telegram (`telegram-debugger`).

## Routing model (verified)
- **Primary:** `model.provider: custom` → Groq `llama-3.3-70b-versatile`,
  `base_url https://api.groq.com/openai/v1`, `max_context 32768`, `max_tokens 8192`.
- **Fallback chain:** `fallback_providers` → local `llama3.1:8b` @ `localhost:11434`
  (65k ctx). Advances **only** on `rate_limit`/`billing` (`chat_completion_helpers.py`).
- **413 is `payload_too_large` → compress, NOT failover** (`agent/error_classifier.py`).
  So an oversize request never reaches the fallback — it dies in the compressor.
- **Per-job override** (cron): `provider`/`model`/`base_url` resolved by
  `_resolve_model_override` (tested). Use this to send heavy jobs to local Ollama.

## Symptom → root cause
| Log signal | Cause | Fix direction |
|---|---|---|
| `404 ... generateContent / v1main` | request routed to **Google**, not Groq — an `.env` key (e.g. `GOOGLE_API_KEY`) hijacked auto-routing | ensure explicit `model.provider`+`base_url`; remove/disable stray key |
| `400 ... max_tokens>...` | `model.max_tokens` unset/too high | set a sane `max_tokens` (e.g. 8192) |
| `413 ... Requested >12000` | request bigger than TPM budget | not a provider bug → `context-compressor` |
| `429 / rate_limit_exceeded` (true RPM/RPD) | genuine throttling | failover should fire; verify chain reachable |
| fallback never used on big jobs | by design (413≠failover) | route the job to local via per-job override or `no_agent` |
| leaked raw tool-call JSON, very slow | weak model (local 8B in hot path) | keep 8B as fallback only |

## Method (read-only)
1. From the failing turn, read `provider= base_url= model=` — confirm *where* it went.
2. Match the status code to the table; confirm with `config.yaml` (redacted) values.
3. For cron: check the job's `provider/model/no_agent` in `jobs.json`.

## Output
The actual route taken vs. intended, the classified failure, and the minimal routing fix
(backup + approval for live edits).
