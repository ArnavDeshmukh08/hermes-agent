# Skill: Cron Router

**Responsibility:** Keep scheduled jobs off the Groq free hot path — route heavy/agentic
cron jobs to the local Ollama tunnel via per-job `provider/model/base_url`, or convert them to
`no_agent` script jobs (the `remind.py` pattern), and safely edit `~/.hermes/cron/jobs.json`.
This is the **cron-side** application of routing; `provider-debugger` diagnoses *why* a route
went wrong, this **decides and sets** the route for scheduled work.

## When to use
- A cron job is large/agentic and would burn the 12k Groq TPM budget (or 413s) at its run time.
- A paused job (`Learning Engine`, `Daily AI…`) needs re-enabling without re-introducing the
  413 → reset instability on the shared brain.
- A job does deterministic work (reminders, digests) that needs no LLM at all.

## Routing decision (pick the cheapest that works)
1. **`no_agent` script job** — zero LLM. Best for deterministic jobs (reminders, fetch+post).
   Model on `remind.py`: the job runs a script, no agent loop, no token cost.
2. **Per-job local override** — keep the agent loop but point it at local Ollama:
   `provider: custom`, `model: llama3.1:8b`, `base_url: http://localhost:11434/v1`. Uses the
   Mac↔VPS reverse tunnel (verify with `tunnel-doctor` first — if the tunnel is down the job
   fails). `_resolve_model_override` is tested and honors these per-job.
3. **Leave on Groq** — only for small, infrequent jobs that comfortably fit under 12k TPM.

## Method  (read-only first; live edits need backup + approval + validation)
1. **Inspect (read-only):** `python -c "import json;print(json.dumps(json.load(open('/home/hermes/.hermes/cron/jobs.json')),indent=2))"`
   — note each job's `schedule`, whether it sets `no_agent`/`provider`, and `enabled` state.
2. **Confirm the fallback brain is up** before choosing option 2: `tunnel-doctor` (a job
   routed to a dead `localhost:11434` just fails on schedule).
3. **Edit jobs.json safely (live — backup + approval; route via `deployment-validator`):**
   - Back up: `cp ~/.hermes/cron/jobs.json{,.bak.$(date +%Y%m%d_%H%M%S)}`.
   - Make **surgical** per-job edits — add `no_agent: true` (+ a `script`/command) for option 1,
     or `provider`/`model`/`base_url` for option 2; flip `enabled: true` to re-enable a paused job.
   - Never full-rewrite the file; validate JSON: `python -c "import json;json.load(open('jobs.json'))"`.
4. **Validate:**
   - `systemctl --user restart hermes-gateway.service` → `is-active`.
   - Trigger or wait for the next run; confirm in `agent.log` the job used the intended route
     (`no_agent` → no LLM call; override → the local `base_url`/`model`, no 413/429) and ended
     `last_status: ok`.

## Don't
- Don't re-enable `Learning Engine` / `Daily AI…` on Groq unmodified — that re-creates the 413
  loop on the shared brain; route them local or `no_agent` first.
- Don't point a job at `localhost:11434` without verifying the tunnel (`tunnel-doctor`).
- Don't hand-author large multi-key rewrites of `jobs.json`; surgical edits + JSON validation.
- Don't use this to debug a route that's already misbehaving at runtime — that's `provider-debugger`.

## Output
Per-job route decisions (job → no_agent | local-override | groq + reason), the backup path, the
edited `jobs.json` keys, and the post-run validation log line showing the intended route and
`last_status: ok`.
