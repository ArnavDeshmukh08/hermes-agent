# Jack ↔ Google / Orsa / Apollo — build notes

> One lesson per entry, newest at top. Read this before the next session.

**TL;DR of the build:** Gave Jack read-only Google Calendar + Gmail, read-only Orsa
CRM, Gmail *draft-only* replies (no send path in code), and Apollo enrichment for
unknown contacts. Auth is OAuth installed-app with a refresh token so it survives
restarts. One open item requires you: the one-time Google consent (`bin/jack_google_auth.py`).

---

## Open item (needs you)
- **One-time Google consent:** `python3 bin/jack_google_auth.py` on your Mac. Prereq: create a
  *Desktop app* OAuth client in Google Cloud Console (enable Calendar API + Gmail API), download the
  JSON to `~/.hermes/google_oauth_client.json`. You pick which Google account in the browser — that's
  the "which account" decision, made at consent time so nothing is hardcoded. Until then Calendar/Gmail
  return a friendly "not connected" message (verified) rather than pretending to work.
- **Apollo:** set `JACK_APOLLO_API_KEY` in `~/.hermes/.env` to enable enrichment; without it the
  reconciler still flags unknown contacts, just without the enriched title/company.

## Lessons

### The personality layer will invent structure from an ambiguous fact key
**Why it mattered:** First live Discord test, "any new leads in Orsa this week" came back as
"None from Orsa this week, but 73 new leads overall" — a flat contradiction (73 IS this week). The
compose layer (`jack_voice/compose.py`) reacts to a FACTS json; I'd passed `{"count": 73}`. "count"
told the LLM nothing about timeframe, so it invented an "overall vs this week" split. Fix: make fact
keys self-describing (`new_leads_in_last_7_days`) and add a CRM few-shot with that exact contradiction
marked WRONG. Lesson: for any data-report intent, the FACTS keys must name the timeframe/unit — the
LLM fills ambiguity with fiction, and that breaks the never-misrepresent rule.

### A read-only capability needs an explicit "no" for the write phrasing
**Why it mattered:** "Can you generate new leads in Orsa?" fell through to the chat brain, which
happily promised "I'll get those generated for you" — an action Jack cannot do (Orsa is read-only;
the scrape worker writes to a Sheet, not Orsa). A read integration isn't done until the *write*
phrasing is explicitly refused. Added `_CRM_WRITE_RE` (unambiguous create verbs + orsa/crm) →
`crm/readonly_notice` → a FIXED-STRING handler reply (no LLM, so it can't be embellished into a
promise). Checked before the scrape branch so "scrape leads into orsa" is refused, not silently
scraped elsewhere.

### Split-brain: proxy to where the data lives, don't ship credentials (Option A)
**Why it mattered:** The gateway (router/handler) runs on the VPS, but the OAuth token and the
Orsa DB live on the Mac. Copying the token to the VPS would spread a credential; and the Orsa DB
simply isn't on the VPS. Solution mirrors the existing Garmin service: a Mac-side HTTP service
(`mac_services/jack_google_service.py`, port 8770) that the VPS calls over Tailscale via thin
proxies (`integrations/mac_remote.py`). A `google_provider.py` factory returns the direct client on
the Mac and the remote proxy on the VPS (`JACK_GOOGLE_REMOTE=1`) — so the handler, the tests, and the
Mac service all share one code path with no credential ever leaving the Mac. The provider factory
imports the concrete client at call-time, which is what keeps the existing `patch("integrations.
calendar.CalendarClient")` handler tests green.

### Gmail over a bridge deserves a token even on Tailscale
**Why it mattered:** Garmin's Mac service binds 0.0.0.0 with no auth — fine for step counts. This
one can read your inbox and create drafts, so it takes an optional `JACK_MAC_SERVICE_TOKEN`; when set,
every request needs a matching `X-Jack-Token`. Verified a wrong token 401s. Defence in depth on top
of the private Tailscale network, and it costs nothing when unset.

### A row-limited query must never back a "how many" answer
**Why it mattered:** `new_leads()` is capped (LIMIT) for display, so counting `len(new_leads())`
reported 50 when the real 7-day total was 73. Jack stating "50 new leads" when it's 73 violates the
"never confirm what you didn't verify" rule. Added a dedicated unbounded `new_leads_count()`
(SELECT COUNT(*)) and used it for every count/"…and N more" figure. Caught by running the client
against the live DB, not by the unit test — always exercise the real data path.

### Service accounts can't read a personal Gmail — OAuth was the only path
**Why it mattered:** The existing `integrations/calendar.py` authenticates with a
Google *service account* (shared-calendar model). That works for a shared calendar
but a service account cannot read a personal `@gmail.com` inbox or create drafts in
it — that needs domain-wide delegation, which is Google Workspace-only. So for Gmail
(and to keep one consent for everything) I built OAuth installed-app auth with an
offline refresh token. Calendar read now piggybacks on the same OAuth creds, with the
old service-account path kept as a fallback so nothing that already worked breaks.

### There is no "draft-but-not-send" Gmail scope
**Why it mattered:** Guardrail is draft-only. Gmail's OAuth scopes are `readonly`,
`compose` (create/update/delete drafts **and** send), and `modify`. None grants
drafting without also technically permitting send. I used the narrowest that drafts
(`gmail.compose`) and enforced draft-only in *code*: there is no `send`/`users().messages().send`
call anywhere in `integrations/gmail.py`. The safety is the code surface, not the scope.

### Orsa DB lives outside the Hermes repo — opened read-only, live (not immutable)
**Why it mattered:** Found it at `/Users/arnav/Documents/leadkiln/backend/orsa.db`
(SQLModel schema: `lead`, `lead_activity`, `workspace`, …; single workspace
"Vytal Med Spa Agency"). The leadkiln backend writes it, Jack only reads. Opened via
`file:...?mode=ro` URI — read-only so Jack can never corrupt it, but NOT `immutable=1`
because we must see the backend's live writes ("live data, not stale cache"). No flock
needed since Jack never writes it.

### Tokens/keys never touch logs or git
**Why it mattered:** `google_token.json`, `google_oauth_client.json` live in `~/.hermes/`
(outside the repo) at mode 0600; added belt-and-suspenders patterns to `.gitignore`.
Every client logs metadata only (counts, ids, subjects) — never tokens, bodies, or API
keys. Apollo key is read from env at call time only.
