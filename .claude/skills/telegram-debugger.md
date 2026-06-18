# Skill: Telegram Debugger

**Responsibility:** Diagnose the Telegram surface — message in/out, persona routing,
allowed chats/users, delivery failures. Not the LLM call itself (`provider-debugger`) and
not overflow (`context-compressor`).

## Key facts
- Transport: Telegram **polling** via `hermes-gateway.service`.
- `telegram.allowed_chats` includes the Vytal group `-1003797274797` and Arnav's DM
  IDs ✅.
- Persona: a single **Jack** across both surfaces — DMs and the Vytal group — from `SOUL.md`.
  Note: SOUL.md text references group `-5439847434` while the live group is `-1003797274797` —
  a surface routing mismatch to confirm with Arnav (ROADMAP Phase 0).
- Streaming enabled for Telegram (`display.platforms.telegram.streaming: true`).

## Symptom → check
| Symptom | Check (read-only) |
|---|---|
| No reply at all | `systemctl --user is-active hermes-gateway.service`; `grep "inbound message" gateway.log` (did it arrive?) |
| Receives but no answer | `grep -A3 "inbound message" agent.log` → usually a 413 (→ `context-compressor`) |
| Wrong/!persona behavior | compare chat id in log to `SOUL.md` persona bindings + `telegram.allowed_chats` |
| "Unauthorized"/ignored | chat/user not in `telegram.allowed_chats`/`allowed_users` |
| Reply but 218-char error | 413→compress→reset (overflow); not a Telegram bug |
| Media not sent | `gateway.media_delivery_allow_dirs`, `trust_recent_files` |

## Method
1. Confirm the message arrived (`gateway.log` inbound line with chat id).
2. Follow that session id into `agent.log`.
3. Decide: Telegram-layer issue (auth/delivery/persona) vs. downstream (provider/overflow)
   and route accordingly.

## Output
Whether the fault is in the Telegram layer; if so, the exact config/SOUL line to change
(backup + approval required). Otherwise, hand off to the downstream skill with the
session id.
