# Hermes Agent — Personal Assistant for Arnav

> This file defines WHAT we are building and the rules of engagement.
> For live status / what's been done, see [MEMORY.md](./MEMORY.md).
> For full standalone project context (shareable with any AI/human), see [CONTEXT.md](./CONTEXT.md).

## ⚠️ MANDATORY: keep CONTEXT.md updated
After any **notable** change, you MUST update [CONTEXT.md](./CONTEXT.md) in the same session.
- **Notable (DO update):** new feature/capability, architecture or model/provider change, new
  integration, infrastructure change, a new persona/skill/script, a locked decision, status
  changes (something built/paused/removed).
- **Not notable (skip):** bug fixes, typos, log/format tweaks, exploratory checks, trivial config.
- Also bump the "Last meaningful update" date in CONTEXT.md and keep §10 (Current status) accurate.
- `MEMORY.md` is the chronological work log (update every session); `CONTEXT.md` is the curated,
  always-current big-picture briefing. Keep them consistent.

## One-line goal
A 24/7 personal AI assistant ("Hermes") running on a VPS that acts as Arnav's
second brain, chief of staff, and dispatcher to a startup dev team — reachable
from his phone, learns his preferences over time, and runs free wherever possible.

## Who this is for
Arnav — startup founder building **Vytal** (a clinic patient-retention system),
also juggling college and personal life. Wants a Jarvis-like assistant.

## Core capabilities (priority order)
1. **Second brain + reminders (FOUNDATION — build first)**
   - Capture ideas/tasks/notes via text + voice from anywhere (Telegram).
   - Remind & nudge; maintain task lists across life + work + startup.
   - Learn preferences over time; ask follow-up questions early to learn faster
     (e.g. learns Arnav dislikes thin-crust pizza).
2. **Voice layer (Jarvis)** — natural speech in/out. Whisper (STT) + Piper (TTS), free.
3. **Dev-team dispatcher** — turn idea-dumps into specs, dispatch to Claude Code
   sub-agents, return a reviewable summary/diff. (The paid piece — "pay when it earns".)
4. **Outreach engine (semi-auto)** — overnight lead-finding + message drafting for
   Vytal; sends are approval-gated. (Compliance-aware: DPDP Act, anti-spam.)
5. **Later/maybe** — food ordering via headless browser (FRAGILE, low priority).

## Key decisions (locked with Arnav)
- **Framework**: "Hermes Agent" framework chosen for built-in memory + agentic skills.
  Model-agnostic — we use **Groq free models** as the brain (not tied to Hermes LLM).
- **Brain**: Groq (free tier) primary; OpenRouter/Hermes optional fallback later.
- **Budget**: "Pay only when it earns." Everything free now; revisit paid pieces
  (Claude Code dev-shipping) once outreach books real meetings.
- **Autonomy**: **Mixed by risk.**
  - Auto (no approval): reminders, capture, research, drafting.
  - Approval-gated (one-tap Telegram): sends, orders, spending, code merges.
- **Interface**: Telegram bot (text + voice notes), 24/7 on the VPS.
- **First build target**: Second brain + reminders + the risk-gate scaffold.

## Hard rules / guardrails
- Never auto-send outreach, spend money, place orders, or merge code without approval.
- Treat all credentials as secrets: `.env` on the box, `secrets/` locally (gitignored).
- Be honest about failures — surface broken steps, don't fake success.
- Cold outreach must respect anti-spam / DPDP; protect sender reputation.
- Prefer free + reliable over clever + fragile.

## Tech stack (working assumption — confirm against the box)
- Language: Python (best free ecosystem for Telegram, Whisper, schedulers, embeddings).
- Process mgmt: systemd or pm2 with auto-restart (24/7 resilience).
- Memory: SQLite + local embeddings for preferences/notes.
- Scheduler: APScheduler / cron for reminders.

## Project layout (local control repo)
- `CLAUDE.md` — this file (goals + rules)
- `MEMORY.md` — living status log (update every session)
- `secrets/` — credentials (gitignored)
- `skills/` — researched skill guides used to build/operate the system
- `docs/` — diagnosis notes, architecture, runbooks
