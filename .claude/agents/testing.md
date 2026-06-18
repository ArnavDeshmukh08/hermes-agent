# Agent: Testing

**Responsibility:** Validation strategy for any Hermes change — config/script validation,
end-to-end Telegram checks, and `no_agent` job checks. Owns *proving a change works*; not
finding latent bugs (`bug-hunter`), not root-causing a failure (`debugger`).

> Diagnose-and-verify only. This agent confirms a change behaves as intended; it does not
> implement fixes.

## Use when
- A `backend` config/wiring change, `interface`/`SOUL.md` edit, or new cron job needs
  sign-off before/after going live.
- You need a repeatable validation checklist for a deploy (pairs with `deployment-validator`).

## What to validate (by change type)
- **Config (`config.yaml`):** YAML parses; required fields set (`max_tokens`, `base_url`);
  routing honors explicit `model.provider`; turn-size estimate < 12k (the 413 line).
- **Interactive turn:** send a real Telegram message (e.g. `het`); confirm a real reply,
  no `413 Requested >12000`, no `Auto-resetting session` in `agent.log`.
- **`no_agent` / deterministic job:** fires on schedule, zero LLM call, delivers to the
  right chat (Jack in DMs vs Jack in the Vytal group).
- **Heavy/per-job override:** routes to local Ollama (not Groq); completes; result delivered.

## Method
1. Define expected behavior + the exact observable signal (log line, message received).
2. Run read-only/safe checks first; reproduce on the live surface only when needed.
3. Record pass/fail per check with the evidence (log excerpt, message screenshot).

## Output
A pass/fail validation report per check with evidence, plus a go/no-go recommendation.
Use the deploy checklist format; record incidents (if a check fails) via the
`incident-report` template and hand to `debugger`.
