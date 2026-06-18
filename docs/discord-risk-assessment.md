# Discord Transport Swap — Security & Risk Assessment

> **Specialist B — Security & Risk · Hermes Phase-3 (Telegram → Discord transport swap)**
> **Status: BLUEPRINT ONLY** — no code written. This document verifies that the
> swap can preserve every approval guardrail and enumerates the NEW risk classes
> Discord introduces. Last meaningful update: 2026-06-17.

## 0. Scope & method

I read the live implementation, not the spec, to ground every claim:

- `bin/dispatch.py` — builds the nonce + inline keyboard, enqueues the awaiting item, stamps the draft. **Never writes `memory/approved/`.**
- `bin/approval_handler.py` — `handle_callback()` is the single decision path: auth → parse → nonce verify → idempotency → write-ahead decision → act. **Only the `approve` branch calls `store.write_approved()`** (handler line ~244).
- `lib/store.py` — `append_decision()` (append-only JSONL via `O_APPEND`), `write_approved()` (atomic tmp+`os.replace`), `find_pending()`, `dequeue()`.

The critical architectural fact for this swap: **the decision logic is already transport-agnostic.** `handle_callback(cb)` is a pure function over a dict. Telegram-specific code is confined to `is_authorized()` (chat-type check), `_parse_callback_data()` (the `apr:` wire format), `_ack_callback()` (Telegram API calls), and the env-var names. A Discord handler must reuse the SAME core (`store.append_decision`, `store.write_approved`, `store.dequeue`, nonce resolution) and add **no new writer** to `memory/approved/`.

---

## 1. Guardrail-preservation table

Each of the six mandated guardrails, how it maps to Discord, the code that must enforce it, and a verdict. **AT-RISK** = maps but requires a NEW control that does not exist in the Telegram path; **PASS** = maps 1:1 with the existing transport-agnostic core.

| # | Guardrail (Telegram today) | Discord mapping | Enforcing code (today → Discord) | Verdict |
|---|---|---|---|---|
| 1 | **Single human gate** — only the `approve` branch writes `memory/approved/`; core is transport-agnostic | UNCHANGED. Discord handler must call the same `store.write_approved()` only inside its approve branch, guarded by `store.approved_exists()` | `approval_handler.handle_callback()` approve branch → `store.write_approved()` (store.py:353). Discord handler reuses verbatim; adds NO second writer | **PASS** — but VERIFY in review that the Discord handler imports the existing branch and does not re-implement the write |
| 2 | **Fail-closed allowlist** — `TELEGRAM_ALLOWED_USERS`; empty ⇒ deny ALL | `DISCORD_ALLOWED_USERS` = comma-sep Discord user IDs; empty ⇒ deny ALL. Check `interaction.user.id` | `_allowed_users()` + `is_authorized()` (handler.py:49–67): `if not allowed: return False`. Discord port keeps the identical empty-set fail-closed and compares `str(interaction.user.id)` | **PASS** — fail-closed logic is trivially portable; **must keep the empty-set→deny default and never substitute Discord-server-membership for the explicit allowlist** |
| 3 | **"DM-only" → private approval channel** — chat must be `private` or match `TELEGRAM_DM_CHAT_ID` | `interaction.channel_id == DISCORD_APPROVAL_CHANNEL_ID`; channel must be locked so only Arnav + bot can view | Today: `is_authorized()` checks `chat.type == "private"`. Discord: add an explicit `channel_id` equality check **in code** AND a private-channel server-permission config **outside code** | **AT-RISK — does NOT map 1:1.** Telegram DMs are private by protocol; a Discord channel's privacy is a **server-permission setting**, not a code property. A misconfigured public/shared channel leaks every draft to anyone who can read it. Code can verify `channel_id`, but code **cannot** by itself guarantee the channel is private. See Risk D-1. |
| 4 | **Append-only `decisions.jsonl`** | UNCHANGED. `decided_by`: `telegram:<id>` → `discord:<id>` | `store.append_decision()` (store.py:320) via `append_jsonl()` `O_APPEND` (store.py:92). Only the `decided_by` string prefix changes (handler.py:218) | **PASS** — storage layer untouched; one string-format change |
| 5 | **Write-ahead decision before side effects** | UNCHANGED. Decision appended BEFORE `write_approved`/`dequeue` | `handle_callback()` calls `append_decision()` at step 4 **before** `write_approved()` / `dequeue()` (handler.py:222 vs 244) | **PASS** — ordering lives in the shared core; preserved as long as the Discord handler calls the same core in the same order. **Caveat:** the Discord 3-second ACK rule (Risk D-5) tempts a re-order — the `defer()` must wrap the FS work, not replace write-ahead |
| 6 | **Unguessable nonce in `custom_id`** | `custom_id` = `apr:<nonce>:<idx>:<action>` (same wire format), verified against the queued item | Nonce: `secrets.token_urlsafe(8)` (dispatch.py:138). Verify: `_resolve_by_nonce()` + double-check `pending.get("nonce") != nonce` (handler.py:201–216). Discord `custom_id` carries the same string | **PASS — with a hard constraint:** Discord `custom_id` max length is **100 chars**; `apr:<token_urlsafe(8)>:<idx>:<action>` is ~20 chars, well under. Do NOT lengthen the nonce past the limit. Keep nonce generation in the (unchanged) dispatch step |

### Net verdict
**5 of 6 PASS, 1 AT-RISK.** The single guardrail that does **not** map 1:1 is **#3 (DM-only → private channel)**, because Discord channel privacy is server-permission configuration, not a code-enforced protocol property. This converts a Telegram property we got "for free" into a **configuration dependency** — a new, distinct risk class. Everything else is preserved by reusing the existing transport-agnostic core.

---

## 2. Discord-specific NEW risks

Ranked by severity, each with concrete mitigation.

### D-1 · Channel-privacy misconfiguration — **CRITICAL**
The approval channel's privacy is a Discord server-permission setting, not a code invariant. If `@everyone` (or any unintended role) has **View Channel** on the approval channel, **every dispatched draft (outreach copy, lead names, strategy) is exposed** to those members. Auth (#2) still prevents an unauthorized member from *clicking* approve — but the **content leaks regardless of who clicks**, and that content is exactly the sensitive material the approval gate exists to protect.
- **Why CRITICAL:** this is a confidentiality breach that the code-level guardrails cannot detect or prevent; it is silent (no error, drafts just post into a readable channel); and it inverts the whole point of "DM-only."
- **Mitigation:**
  1. **Channel-permission checklist** (pre-go, §4): deny `View Channel` for `@everyone`; allow only Arnav's user and the bot role.
  2. **Startup self-check:** on boot, the bot calls `channel.permissions_for(guild.default_role)` (or REST `GET /channels/{id}` overwrites) and **refuses to dispatch** (fail-closed) if `view_channel` is granted to `@everyone` or any role other than {Arnav, bot}. Log a CRITICAL and do not send.
  3. Prefer a **bot DM** to Arnav over a guild channel if Discord DM + persistent components are workable — that restores the Telegram "private by protocol" property and largely retires this risk. (Channel chosen for component persistence; if DM works, it is strictly safer.)

### D-2 · Interaction auth bypass / default-allow — **HIGH**
Discord cryptographically signs interaction payloads, so the *transport* is authentic — but Discord does NOT know Arnav's allowlist. A common mistake is to treat "the interaction is from Discord and the user is in the server" as authorization. That is a default-allow and would let **any server member** approve.
- **Mitigation (defense in depth — both checks, AND):**
  1. `str(interaction.user.id) in DISCORD_ALLOWED_USERS` (fail-closed, empty⇒deny — port of `is_authorized`).
  2. `interaction.channel_id == DISCORD_APPROVAL_CHANNEL_ID`.
  3. Then nonce verify against the queued item.
  Reject (and log) on any failure, writing nothing — mirror `handle_callback`'s "auth fails → write NOTHING" (handler.py:191). Verify signature validation is on if using the raw HTTP interactions endpoint; with discord.py gateway it is handled by the library.

### D-3 · `discord.py` dependency (supply-chain + venv) — **HIGH**
The MVP is **stdlib-only** (`dispatch.py`/`approval_handler.py` use only `urllib`, `json`, `secrets`). Adding discord.py introduces a transitive dependency tree (`aiohttp`, `multidict`, `yarl`, `frozenlist`, …) and an `asyncio` runtime — a new supply-chain and resource surface on a **3.7 GB RAM / no-swap** box.
- **Why HIGH:** new attack surface (any dep CVE now ships into the approval path), and a real OOM/stability risk on a constrained box with no swap.
- **Mitigation:**
  1. **Pin exact versions** (`discord.py==X.Y.Z` + a fully pinned `requirements.txt` / lockfile); document the pin and a review cadence.
  2. Install into a **dedicated venv**, not system Python; measure idle RSS before enabling 24/7 (gateway WebSocket holds a persistent connection + buffers).
  3. Run `pip-audit` / `safety` on the pinned set at install and on each bump.
  4. **Least-privilege intents** (see D-4) keep the library's footprint and data exposure minimal.
  5. Consider the **stdlib HTTP-interactions path** (verify Ed25519 signature manually) as a lighter alternative that avoids the gateway/asyncio stack entirely — but this needs a public HTTPS endpoint, a tradeoff to weigh.

### D-4 · Bot token leakage — **HIGH**
A leaked bot token = full impersonation of the bot (read the approval channel, post fake approvals UI, act with the bot's permissions).
- **Mitigation:**
  1. `DISCORD_BOT_TOKEN` in `.env` only; already covered by `.gitignore` (`*.env`, `.env`, `secrets/`).
  2. **Never logged** — mirror the existing discipline: `_tg_call` never echoes the token and swallows transport errors *after* the decision is persisted (handler.py:97–100). The Discord client must never log the token, headers, or full interaction payloads at INFO.
  3. **Least-privilege bot permissions:** NO Administrator. Required scopes only: View Channel + Send Messages + (Use/Read) on the approval channel. **Disable the `MESSAGE_CONTENT` privileged intent** — button interactions arrive as interaction events and do NOT require reading message content; leaving it on needlessly widens both data exposure and the verification burden.
  4. Rotate the token immediately if exposed (per `security.md` response protocol).

### D-5 · Interaction replay / stale buttons after restart — **HIGH**
An old approval message's button can be clicked **after a bot restart** (or days later). The bot must (a) still hold the nonce↔queue mapping, and (b) handle interactions for messages it did **not** post *in this process*.
- **Two sub-risks:**
  - **Idempotency / replay:** re-clicking an already-resolved item. The existing core already defends this: `_resolve_by_nonce` returns the item only while pending; once `dequeue()`d, `find_pending` returns `None` ⇒ `already_resolved` no-op (handler.py:203–214). This is **preserved as-is** because the queue/decision state is on disk, not in Discord. Nonce mismatch ⇒ `bad_nonce`.
  - **Cross-process buttons:** discord.py drops component interactions for views it doesn't recognize after a restart. **Mitigation:** use a **persistent View** (`timeout=None`, registered in `setup_hook`) **or** a raw `on_interaction` listener keyed off the `custom_id` prefix `apr:` — so the bot processes any historical button by parsing `custom_id` and consulting the on-disk queue, exactly as Telegram does. Do NOT rely on in-memory message references.
  - **Defense:** after resolving, **disable the buttons** on the original message (edit components to disabled) — the analog of `editMessageReplyMarkup` clearing the keyboard (handler.py:117). Belt-and-suspenders with the nonce/idempotency check.

### D-6 · 3-second ACK failure — **MEDIUM**
Discord requires an interaction response within **3 seconds** or it shows "This interaction failed" to the user, even if the backend later succeeds. The approval path does filesystem work (`append_decision` + `write_approved` + atomic replaces + `fsync`).
- **Mitigation:** call `interaction.response.defer()` (ephemeral) **immediately** on receipt, *then* run the FS work, *then* `interaction.followup`/edit. **Critical ordering note:** `defer()` is a UI acknowledgement, NOT the decision — the **write-ahead decision (#5) must still be appended before any side effect**. Sequence: `defer()` → `append_decision()` → `write_approved()`/`dequeue()` → followup. Do not let the 3-second pressure collapse write-ahead.

### D-7 · Multi-clicker / content visibility in a shared channel — **MEDIUM**
If `DISCORD_ALLOWED_USERS` ever holds >1 user, another allowlisted person can approve. More importantly, the **DM→channel model change** means draft content is visible to **everyone who can view the channel**, not just the decider — a structural difference from Telegram's 1:1 DM.
- **Mitigation:** keep `DISCORD_ALLOWED_USERS` to **exactly Arnav** for MVP (matches single-human-gate intent). Combined with D-1's private-channel lock + startup self-check, this bounds both who can click and who can see. Treat any additional allowlisted user as an explicit, logged decision.

---

## 3. Comparison to the Telegram threat model

| Property | Telegram (today) | Discord (proposed) | Direction |
|---|---|---|---|
| Channel privacy | Private **by protocol** (1:1 DM) | Private **by configuration** (server permissions) | **RISKIER** — new misconfig class (D-1) |
| Content audience | Only Arnav (DM) | Everyone with View Channel | **RISKIER** — content can leak even if approve-auth holds |
| Dependency surface | **stdlib-only** (`urllib`) | discord.py + asyncio + transitive deps | **RISKIER** — supply-chain + RAM on no-swap box (D-3) |
| Transport authenticity | TLS to Telegram API | **Cryptographically signed interactions** | **SAFER** — Discord signs payloads; harder to forge a click |
| Stale-button handling | Inline keyboard cleared after decision | Needs persistent View / raw `on_interaction` | **EQUIVALENT** once persistent components are wired; on-disk idempotency already covers replay |
| Auth model | Allowlist + DM check, fail-closed | Allowlist + channel check, fail-closed | **EQUIVALENT** (defense in depth must keep BOTH checks — D-2) |
| Single-writer gate (#1) | Only approve branch writes `approved/` | Same core, no new writer | **EQUIVALENT** (unchanged core) |
| Append-only audit (#4) | `decisions.jsonl` O_APPEND | Same; `decided_by` prefix only | **EQUIVALENT** |
| Write-ahead (#5) | Append before side effects | Same, but `defer()` ordering trap | **EQUIVALENT** if defer wraps (not replaces) write-ahead (D-6) |
| Nonce (#6) | `token_urlsafe(8)` in callback_data | Same in `custom_id` (100-char limit OK) | **EQUIVALENT** |
| ACK latency | No hard deadline | **3-second** hard deadline | **RISKIER (UX/integrity)** — mishandled defer can break ordering (D-6) |

**Summary:** The swap is **net SAFER on transport authenticity** (signed interactions) and **otherwise equivalent on the core file-based guardrails** — because the decision/audit/write-ahead logic is transport-agnostic and untouched. It is **RISKIER on three new axes that did not exist with Telegram DMs**: (1) channel-privacy configuration, (2) the discord.py dependency/runtime footprint, and (3) the 3-second ACK deadline interacting with write-ahead. The first is the dominant new risk.

---

## 4. Pre-go security checklist

Verify ALL before enabling the Discord path in production.

**Channel & permissions (addresses D-1, D-7 — the dominant risk)**
- [ ] Approval channel has `View Channel` **denied** for `@everyone`.
- [ ] Only Arnav's user and the bot role have `View Channel` + `Send Messages` on it.
- [ ] **Startup self-check implemented:** bot inspects the channel's permission overwrites on boot and **refuses to dispatch (fail-closed)** if any non-{Arnav, bot} principal can view it; logs CRITICAL.
- [ ] `DISCORD_APPROVAL_CHANNEL_ID` is set and the handler rejects interactions from any other channel.

**Auth (addresses D-2, #2, #3)**
- [ ] `DISCORD_ALLOWED_USERS` set to exactly Arnav's Discord user ID; empty ⇒ deny-ALL verified by test.
- [ ] Handler enforces **both** `user.id ∈ allowlist` **AND** `channel_id == approval channel` (defense in depth), then nonce.
- [ ] Auth failure path writes **nothing** (no decision, no `approved/`) — covered by test mirroring `handle_callback` unauthorized.

**Core guardrail preservation (#1, #4, #5, #6)**
- [ ] Discord approve branch calls the **existing** `store.write_approved()` guarded by `store.approved_exists()`; **no second writer** to `memory/approved/` anywhere in the Discord module (grep-verified).
- [ ] Decision appended via `store.append_decision()` **before** `write_approved`/`dequeue`; `decided_by` = `discord:<id>`.
- [ ] Nonce generated by the (unchanged) dispatch step with `secrets.token_urlsafe`; verified against the queued item; `custom_id` ≤ 100 chars.
- [ ] Idempotent double-click ⇒ `already_resolved` no-op (test it after a simulated restart).

**Token & dependency (addresses D-3, D-4)**
- [ ] `DISCORD_BOT_TOKEN` in `.env` only; confirmed `.gitignore` covers it (it does: `*.env`/`.env`/`secrets/`).
- [ ] Token never logged; no full interaction payloads logged at INFO.
- [ ] Bot has **no Administrator**; least-privilege scopes only; **`MESSAGE_CONTENT` intent disabled**.
- [ ] discord.py pinned to an exact version + lockfile; `pip-audit`/`safety` clean; installed in a dedicated venv.
- [ ] Idle RSS measured on the box; confirmed safe headroom (3.7 GB, no swap).

**Lifecycle (addresses D-5, D-6)**
- [ ] Persistent View (`timeout=None`, registered in `setup_hook`) OR raw `on_interaction` keyed on `apr:` prefix — handles buttons after restart.
- [ ] Buttons disabled on the message after a decision (analog of clearing the keyboard).
- [ ] `interaction.response.defer()` called immediately; FS work then followup; **write-ahead ordering preserved** (defer ≠ decision).

---

## 5. Single most important pre-go check

> **Lock the approval channel private AND ship a fail-closed startup self-check that refuses to dispatch if anyone other than Arnav + the bot can view it.**

This is the one control with no Telegram equivalent. Every code-level guardrail (auth, nonce, write-ahead, single-writer) is preserved by reusing the existing transport-agnostic core — but none of them stops a misconfigured channel from leaking every draft to readers. Channel privacy is the only guardrail that does not map 1:1 from Telegram, so it is the only one that must be re-established by explicit configuration **and** enforced in code at startup. If that self-check is not in place, the Discord path should not be enabled.
