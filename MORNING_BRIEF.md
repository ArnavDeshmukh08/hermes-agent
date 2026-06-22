# Morning Brief — Jack Goal System

**Date:** 2026-06-22  
**Branch:** `goal-system` (5 commits ahead of main)  
**Tests:** 835 passed, 3 skipped (was 683 before)

---

## What was built

Jack is now goal-driven, not just skill-driven. Arnav creates goals of any kind
by talking to Jack, and the proactive loop holds him accountable against his own
plan. Goals are first-class durable objects — not memories.

**New files:**
- `jack_goals/__init__.py` — module re-exports
- `jack_goals/models.py` — `Goal` dataclass (id, title, type, target, plan, metric, deadline, status, progress_notes, last_checked)
- `jack_goals/store.py` — CRUD over `~/.hermes/goals.json` with `fcntl.flock`
- `jack_goals/intents.py` — LLM-powered goal parsing (create/query/update from free text)
- `jack_tools/web_fetch.py` — GET-only, SSRF-guarded web fetch for goal lookups
- `tests/test_jack_goals.py` — 13 goal store tests
- `tests/test_goal_intents.py` — 10 intent routing/parsing tests
- `tests/test_goal_reasoning.py` — 7 goal-aware reasoning tests
- `tests/test_web_fetch.py` — 11 web fetch security tests

**Modified files:**
- `proactive/reasoner.py` — `gather_context()` now loads active goals + Garmin data; reasoning prompt gains `GOALS_TRACKING` domain with the framing rule baked in
- `hooks/jack_router/jack_intent_router.py` — new `_GOAL_CREATE_RE`, `_GOAL_QUERY_RE`, `_GOAL_UPDATE_RE` patterns
- `hooks/jack_router/handler.py` — `_run_goal()` async handler + dispatch routing

---

## Done Criteria

- [x] Goals are first-class objects in `~/.hermes/goals.json` (flock)
- [x] Create/query/update goals by natural language, Jack confirms in-voice
- [x] Goals are general-purpose (fitness/financial/project/habit/other)
- [x] Proactive loop reads active goals + actual Garmin data, tracks plan vs actual
- [x] Reasoning reflects his plan, never prescribes medical/training/diet (FRAMING RULE in prompt)
- [x] Read-only web fetch (GET-only, SSRF-guarded: blocks localhost/10.x/192.168.x/169.254.x/link-local IPv6)
- [x] 835 tests green (threshold: 680+)
- [x] All work on branch `goal-system`, NOT main
- [x] Deploy GATED for morning approval
- [x] This MORNING_BRIEF.md written

---

## Review the branch (run on Mac)

```bash
# See what changed
git diff main..goal-system --stat

# Review the key reasoning change (framing rule + goals context)
git diff main..goal-system -- proactive/reasoner.py

# Review the new goal store
git diff main..goal-system -- jack_goals/store.py

# Review the intent routing additions
git diff main..goal-system -- hooks/jack_router/jack_intent_router.py

# Full commit list
git log --oneline main..goal-system
```

### Key things to verify in the diff:

1. **Framing rule** — `proactive/reasoner.py` should contain:
   `CRITICAL FRAMING RULE: You are a tracking mirror, not a coach. Reflect his plan back to him — NEVER invent training schedules, dietary advice, medical prescriptions, pacing targets, or recovery protocols.`

2. **SSRF guard** — `jack_tools/web_fetch.py` should block: localhost, 10.x, 172.16-31.x, 192.168.x, 169.254.x, ::1, fc00::/7, fe80::/10

3. **Goal store path** — `jack_goals/store.py` should use `Path("~/.hermes/goals.json").expanduser()`

4. **Intent patterns** — `_GOAL_CREATE_RE` should match `"new goal:"`, `"my goal is"`, `"i want to track"`, etc.

---

## Test a goal via chat (after VPS deploy)

In Discord, say to **@Jack**:

```
new goal: run a marathon in 10 weeks, my plan is to run 3 times a week building to 30km long runs
```
→ Jack should reply: *"Got it — I'm tracking: run a marathon in 10 weeks. Target: [target]. Metric: Garmin runs. Your plan saved. I'll keep you honest."*

```
what are my goals
```
→ Jack should list all active goals with metric and deadline.

```
how am I tracking on my marathon goal
```
→ Jack should query progress.

```
mark my marathon goal done
```
→ Jack should confirm: *"Done — marked 'run a marathon...' as complete. Nice work."*

---

## GATED deploy steps (await morning approval)

**Step 1 — Merge to main (on Mac):**
```bash
git checkout main
git merge --no-ff goal-system -m "feat: jack goal system — first-class goals, goal-aware proactive loop"
git log --oneline -6
```

**Step 2 — Push to VPS:**
```bash
git push origin main
```

**Step 3 — On VPS (ssh hermes@vps):**
```bash
cd ~/.hermes
git pull origin main
python3 -c "from jack_goals import list_active_goals; print('jack_goals OK')"
sudo systemctl restart jack-proactive
sudo systemctl status jack-proactive
```

**Step 4 — Monitor first goal-aware cycle:**
```bash
tail -f ~/.hermes/logs/proactive_shadow.log
# Look for: "Active Goals" in the context, and goal-nudge decisions
```

**Step 5 — Create a test goal via VPS Python (optional smoke test):**
```bash
cd ~/.hermes && python3 -c "
from jack_goals import create_goal, list_active_goals
g = create_goal('Marathon 10 weeks', 'fitness', 'Finish a marathon', 'Run 3x/week', 'garmin_runs', '2026-09-01')
print(f'Created: {g.id} — {g.title}')
print(f'Active goals: {[x.title for x in list_active_goals()]}')
"
```

---

## Shadow mode note

The proactive loop remains in shadow mode (`JACK_PROACTIVE_MODE=shadow`).
Goal-aware reasoning runs automatically on the next 15-min cycle — nothing is sent to Discord.

After merge + restart, watch:
```bash
tail -f ~/.hermes/logs/proactive_shadow.log
```

The shadow log will show whether active goals are being surfaced correctly.
To go live with goal nudges, no additional change is needed — it's the same
`JACK_PROACTIVE_MODE=live` switch already gating the whole proactive system.

---

## Security summary (all checks passed)

| Check | Result |
|-------|--------|
| SSRF guard (localhost/10.x/192.168.x/169.254.x) | PASS |
| GET-only (no POST/forms) | PASS |
| No shell=True | PASS |
| goals.json at ~/.hermes/ | PASS |
| fcntl.flock / LOCK_EX | PASS |
| No hardcoded model strings | PASS |
| Framing rule in reasoner prompt | PASS |
| SSRF attempts logged | PASS |
