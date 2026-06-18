# Skill: Log Analyzer

**Responsibility:** Read Hermes's logs efficiently and extract the signal — which turn
failed, why, and the surrounding request path. The first stop for almost every
diagnosis; feeds `bug-fixer` and `debugger`.

## Log map (read-only, newest = most relevant)
- `~/.hermes/logs/agent.log` — **main**: turns, API calls, compression, resets.
- `~/.hermes/logs/gateway.log` — inbound/outbound Telegram, delivery.
- `~/.hermes/logs/errors.log` — error-level only.
- `~/.hermes/cron/output/<job_id>/<timestamp>.md` — per-cron-run output.
- `~/.hermes/cron/jobs.json` — each job's `last_status` / `last_error`.

## High-signal patterns
| grep | Means |
|---|---|
| `HTTP 413 ... Requested NNNNN` | turn exceeded TPM; NNNNN = request size (compare to 12000) |
| `cannot compress further` + `Auto-resetting session` | fixed-overhead overflow (not conversation) |
| `error_type=APIStatusError ... base_url=...` | which provider/model/endpoint actually got called |
| `404 ... generateContent ... v1main` | request hit Google, not Groq → provider hijack |
| `Streaming failed before delivery` | failed mid-stream; check the next lines for cause |
| `provider=custom base_url=https://api.groq.com` | confirms Groq routing |
| `provider=custom base_url=...11434` | confirms local Ollama (fallback/heavy path) |

## Method
1. Find the failing turn: `grep -nE "413|ERROR|Streaming failed|404" ~/.hermes/logs/agent.log | tail`.
2. Read ±15 lines around it for the full request path (client created → call → error →
   recovery → delivery).
3. Extract: timestamp, session id, provider/base_url/model, request size, recovery taken.
4. **Redact secrets** before quoting (`token`, `key`, `Bearer`).

## Output
A timestamped, redacted evidence excerpt + a one-line interpretation, handed to the
diagnosing skill/agent.
