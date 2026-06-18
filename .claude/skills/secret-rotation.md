# Skill: Secret Rotation

**Responsibility:** Rotate the exposed Groq key, single-source every secret (it currently
lives in **both** `~/.hermes/config.yaml` and `~/.hermes/.env`), prune the 487-line `.env`,
and optionally rotate the VPS sudo password — all without echoing a secret to logs,
transcripts, or shell history. This is the **secrets-hygiene** skill; it does not validate
deploys (`deployment-validator`) or debug routing (`provider-debugger`).

## Why this exists
The Groq key is duplicated across `config.yaml` and `.env` — two places to leak, two to
forget on rotation. `.env` has grown to 487 lines (stale keys = attack surface and the source
of stray auto-routing, e.g. a leftover `GOOGLE_API_KEY` hijacking the provider). Single-source
+ prune + rotate is the fix.

## Method  (read-only first; live edits need backup + approval + validation)
1. **Inventory (read-only, never print values):**
   - Where the Groq key appears: `grep -rln -i "groq\|gsk_" ~/.hermes/config.yaml ~/.hermes/.env`
     (`-l` = filenames only, **no values**).
   - `.env` size and stale candidates: `wc -l ~/.hermes/.env` then
     `grep -nE "^[A-Z_]+=" ~/.hermes/.env | sed -E 's/=.*/=<redacted>/'` (keys only, values stripped).
   - Cross-check which keys are actually read by the code/config before deleting any.
2. **Decide single source:** prefer **`.env`** as the only home for runtime secrets and have
   `config.yaml` reference the env var (e.g. `api_key_env: GROQ_API_KEY`) rather than the
   literal — so the key lives in exactly one file.
3. **Rotate (live — approval-gated; route via `deployment-validator`):**
   - Generate a **new** Groq key in the Groq console; revoke the old one **after** the new one
     is validated working.
   - Back up first: `cp ~/.hermes/.env{,.bak.$(date +%Y%m%d_%H%M%S)}` and same for `config.yaml`.
   - Write the new value with a method that **doesn't hit shell history** — edit the file in an
     editor, or `set +o history` first; never `echo "gsk_..." >>`.
   - Remove the duplicate literal from `config.yaml`; point it at the env var.
4. **Prune `.env`:** delete confirmed-stale keys (surgical line removals, keep a backup). Each
   removal is reversible from the `.bak`.
5. **Validate:**
   - YAML still parses; `systemctl --user restart hermes-gateway.service` → `is-active`.
   - One live turn completes against Groq with the **new** key (no `401/invalid_api_key`).
   - Only then revoke the old key in the console.
   - Confirm nothing leaked: `grep -i "gsk_" ~/.hermes/logs/*.log` should return **nothing**.

## Don't
- Don't `echo`/`cat`/`export` a secret on a line that lands in shell history, logs, or this
  transcript — use editor edits or history-disabled shells.
- Don't revoke the old key before the new one is validated (you'll lock out the brain).
- Don't blanket-delete `.env` lines without confirming each key is unused (a deleted key can
  silently change provider routing — see `provider-debugger`).
- Don't store the new key in two files again — single-source is the whole point.
- Don't leave secret-bearing backups behind: after the old key is revoked, shred/remove
  `.env.bak.*` / `config.yaml.bak.*` copies that still hold it (they're a stale-secret-at-rest
  leak), and ensure remaining backups are gitignored + `chmod 600`.
- VPS sudo-password rotation is **deferred/optional**; only do it on explicit request and never
  paste the password anywhere persistent.

## Output
Inventory of where each secret lived (filenames only), the single-source decision, confirmation
the Groq key was rotated + old one revoked, `.env` line count before/after, and the
"no secret in logs" grep result.
