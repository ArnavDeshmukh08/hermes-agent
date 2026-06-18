# Skill: Hermes Doctor

**Responsibility:** A fast, read-only end-to-end health check of the whole system — is
Hermes up, routing correctly, within budget, and resourced? Triage before diagnosis;
powers `/doctor`. Deep root-cause work belongs to the subsystem skills.

## Checklist (all read-only)
1. **Service** — `systemctl --user is-active hermes-gateway.service` (expect `active`).
2. **Resources** — `free -m` (RAM headroom; no swap, so flag < ~300 MB free), `df -h`.
3. **Primary brain** — recent turns route to Groq:
   `grep "base_url=https://api.groq.com" ~/.hermes/logs/agent.log | tail -1`.
4. **Budget health** — any recent 413s?
   `grep -c "413" ~/.hermes/logs/agent.log` and the latest `Requested NNNNN`
   (NNNNN should be < 12000). **>0 recent 413s = unhealthy** → `context-compressor`.
5. **Fallback** — Mac Ollama tunnel reachable on `localhost:11434` (from VPS).
6. **Cron** — `jobs.json`: any `last_status: error`? which are paused and why?
7. **Memory** — `state.db` size + session count not runaway; curator ran.
8. **Secrets hygiene** — Groq key single-sourced; no secrets in recent logs.

## Output (status board)
```
hermes-doctor — <timestamp>
service    : active | down
resources  : RAM xxx MB free / disk xx% | OK | TIGHT
primary    : Groq reachable | misrouted
budget     : last turn NNNNN tok (<12k OK | OVER)  | recent 413s: N
fallback   : tunnel up | down
cron       : N ok / N paused / N error
memory     : N sessions, XX MB | OK | growing
verdict    : HEALTHY | DEGRADED (reason) | DOWN (reason)  → next: <skill/agent>
```
Map any non-OK line to the owning skill/agent and stop (don't fix here — that's `/bug`).
