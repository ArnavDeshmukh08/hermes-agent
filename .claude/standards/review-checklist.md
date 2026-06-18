# Review Board Checklist

> The three checklists the Review Board runs before any standard, workflow, agent, skill,
> or live change is integrated. Tied to `standards/engineering-standards.md` and
> `standards/agent-authoring.md`. Block on any unchecked CRITICAL item.

## 1. Technical (correctness · completeness · maintainability)
- [ ] **Correct:** the change does what it claims; verified against cited evidence, not assumed.
- [ ] **Root cause, not symptom:** addresses the underlying issue (`engineering-standards.md` §1).
- [ ] **Routing correct:** work runs on the right path (deterministic / heavy / interactive);
      heavy work is OFF Groq.
- [ ] **Context budget respected:** no growth of the always-on prompt without justification;
      interactive turns stay < 12k tokens (`/context`).
- [ ] **Complete:** edge cases, failure paths, and rollback considered — not just the happy path.
- [ ] **Validation included:** concrete steps to prove it works (and were run for live changes).
- [ ] **Maintainable:** small, scannable, single-responsibility; no dead/duplicated logic.

## 2. Security (secrets · data handling · auth/approval gates)
- [ ] **No secrets in files:** credentials live in `~/.hermes/.env` (box) / `secrets/` (local,
      gitignored) — never hardcoded or committed.
- [ ] **Data handling:** memory/profile/log data stays within the box; no leaking user data to
      third parties; outreach respects DPDP / anti-spam.
- [ ] **Approval gates intact:** sends / spend / deploys / merges remain approval-gated; nothing
      auto-executes a gated action (`engineering-standards.md` §6).
- [ ] **No new attack surface:** no unreviewed external calls, no shell injection from user text.
- [ ] **Backups for live edits:** timestamped `.bak` exists before any config/SOUL/.env/cron edit.
- [ ] **File permissions:** `.env` / SSH key are `600`, `secrets/` not group/world-readable;
      secret-bearing `.bak` files are gitignored, `600`, and pruned after rotation.

## 3. Integration (compatibility · dependency correctness · cross-file consistency)
- [ ] **Compatible:** consistent with `.claude/CLAUDE.md`, the project `CLAUDE.md`, and the
      locked Option-B architecture (`docs/ARCHITECTURE-DECISION.md`).
- [ ] **Dependencies correct:** every referenced agent/skill/command/path actually exists and is
      spelled right (relative paths resolve).
- [ ] **No overlap:** the new file doesn't duplicate a sibling's responsibility
      (`agent-authoring.md` single-responsibility rule).
- [ ] **Cross-file consistent:** facts (token numbers, model names, paths, version) match the
      rest of the repo and `docs/`.
- [ ] **Docs synced:** `CONTEXT.md` + `MEMORY.md` updated if notable (`workflows/doc-sync.md`).

## Verdict
- **Approve** — no CRITICAL/HIGH issues.
- **Approve with notes** — only MEDIUM/LOW; record them.
- **Block** — any CRITICAL (secret leak, broken approval gate, budget regression, wrong routing,
  config-corruption risk).
