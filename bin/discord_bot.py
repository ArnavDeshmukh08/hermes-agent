#!/usr/bin/env python3
"""Discord approval bot (Phase-3 transport) — replaces the Telegram dispatch+handler.

One long-running process that:
  1. scans memory/content/ for pending+undispatched drafts and POSTS each to a
     private Discord approval channel with Approve / Reject buttons, and
  2. handles button interactions: authorize (allowlisted user AND correct
     channel, fail-closed) -> lib.approval_core.decide -> edit the message.

Design notes:
  * The pure functions (auth, parse, build, post_pending_once, handle_interaction)
    have NO ``discord`` import, so tests drive them offline. ``discord`` is
    imported lazily inside ``run()`` only.
  * Buttons are sent as a RAW components payload and clicks are handled via raw
    ``on_interaction`` parsing ``custom_id`` — so no View registration is needed
    and interactions survive a bot restart (the queue is the source of truth).
  * Guardrails preserved: fail-closed auth + channel-lock, write-ahead decision,
    only approve writes approved/, unguessable nonce, idempotent on replay.
  * Channel-privacy self-check (the one guardrail with no Telegram 1:1) runs at
    startup and REFUSES to post if anyone but {Arnav, bot} can view the channel.
"""

import sys
import os
import json
import secrets
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import contracts, store, approval_core  # noqa: E402

ACTION_SHORT = {"approve": "a", "reject": "r", "revise": "v"}
ACTION_MAP = {"a": "approve", "r": "reject", "v": "revise"}

# Discord component constants (raw API shape — no discord.py needed to build).
_COMPONENT_ACTION_ROW = 1
_COMPONENT_BUTTON = 2
_BTN_SUCCESS = 3   # green (Approve)
_BTN_DANGER = 4    # red (Reject)


# --------------------------------------------------------------------------
# env / dry-run
# --------------------------------------------------------------------------

def _env_truthy(name):
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


def is_dry_run():
    if _env_truthy("HERMES_DISCORD_DRYRUN"):
        return True
    return not os.environ.get("DISCORD_BOT_TOKEN", "").strip()


def _allowed_users():
    """DISCORD_ALLOWED_USERS comma list. Empty ⇒ deny-all (fail-closed)."""
    raw = os.environ.get("DISCORD_ALLOWED_USERS", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def _approval_channel_id():
    return os.environ.get("DISCORD_APPROVAL_CHANNEL_ID", "").strip()


# --------------------------------------------------------------------------
# auth — fail-closed, user allowlist AND channel lock (defense in depth)
# --------------------------------------------------------------------------

def is_authorized(user_id, channel_id):
    """True only if the user is allow-listed AND the click is in the approval
    channel. Empty allowlist or unset approval channel ⇒ deny (fail-closed)."""
    allowed = _allowed_users()
    if not allowed:
        return False
    if str(user_id) not in allowed:
        return False
    chan = _approval_channel_id()
    if not chan:
        return False
    return str(channel_id) == chan


# --------------------------------------------------------------------------
# parsing + rendering
# --------------------------------------------------------------------------

def parse_custom_id(data):
    """apr:<nonce>:<variant_idx>:<action> -> (nonce, idx, action) or None."""
    if not data or not isinstance(data, str):
        return None
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "apr":
        return None
    _, nonce, idx_raw, short = parts
    action = ACTION_MAP.get(short)
    if action is None or not nonce:
        return None
    try:
        idx = int(idx_raw)
    except (TypeError, ValueError):
        return None
    return nonce, idx, action


def build_components(nonce, variant_idx):
    """Raw Discord message components: one action row with Approve + Reject."""
    def _btn(label, style, action):
        return {
            "type": _COMPONENT_BUTTON,
            "style": style,
            "label": label,
            "custom_id": "apr:{0}:{1}:{2}".format(nonce, variant_idx, ACTION_SHORT[action]),
        }
    return [{
        "type": _COMPONENT_ACTION_ROW,
        "components": [
            _btn("Approve", _BTN_SUCCESS, "approve"),
            _btn("Reject", _BTN_DANGER, "reject"),
        ],
    }]


def build_message_text(draft, variant_idx):
    variants = (draft or {}).get("variants") or []
    if not (0 <= variant_idx < len(variants)):
        return "(no variant)"
    v = variants[variant_idx]
    text = (v.get("text") or "").strip()
    score = v.get("score")
    lines = [text, "", "score: {0}".format(score)]
    flagged = v.get("flagged_links") or []
    if flagged:
        lines.append("⚠ unverified links: {0}".format(", ".join(flagged)))
    lines.append("draft: {0}".format(draft.get("id")))
    return "\n".join(lines)


def _deterministic_message_id(content_id):
    """Stable fake message id for dry-run reproducibility."""
    return int.from_bytes(content_id.encode("utf-8")[:6].ljust(6, b"0"), "big") % 1_000_000_000


# --------------------------------------------------------------------------
# posting (testable: send_fn injected; dry-run prints)
# --------------------------------------------------------------------------

def _already_dispatched(draft):
    return bool((draft.get("approval") or {}).get("dispatched_at"))


def post_pending_once(*, dry_run=None, send_fn=None):
    """Post every pending+undispatched draft to the approval channel.

    ``send_fn(text, components) -> message_id`` is injected by the live bot; in
    dry-run it is None and a deterministic id is used. Returns a list of
    {content_id, nonce, message_id}. Never writes memory/approved/.
    """
    if dry_run is None:
        dry_run = is_dry_run()
    channel_id = _approval_channel_id()
    posted = []
    for draft in store.iter_drafts(status="pending"):
        content_id = draft.get("id") or draft.get("content_id")
        if _already_dispatched(draft) or store.find_pending(content_id) is not None:
            continue
        variant_idx = 0
        nonce = secrets.token_urlsafe(8)
        text = build_message_text(draft, variant_idx)
        components = build_components(nonce, variant_idx)

        if dry_run or send_fn is None:
            message_id = _deterministic_message_id(content_id)
            print("=== DRY-RUN discord post ===")
            print(json.dumps({"channel_id": channel_id, "content": text,
                              "components": components, "message_id": message_id},
                             indent=2, ensure_ascii=False))
        else:
            message_id = send_fn(text, components)

        sent_at = contracts.now_iso()
        store.enqueue({
            "content_id": content_id,
            "variant_idx": variant_idx,
            "nonce": nonce,
            "state": "sent",
            "channel_id": channel_id,
            "discord_message_id": message_id,
            "sent_at": sent_at,
        })

        def _stamp(d, _n=nonce, _m=message_id, _at=sent_at, _ch=channel_id):
            approval = dict(d.get("approval") or {})
            approval.update({"transport": "discord", "nonce": _n, "channel_id": _ch,
                             "message_id": _m, "dispatched_at": _at})
            new_d = dict(d)
            new_d["approval"] = approval
            return new_d

        try:
            store.update_draft(content_id, _stamp)
        except Exception as exc:  # noqa: BLE001 — queue is source of truth; stay approvable
            print("discord_bot: warning — could not stamp draft {0} ({1}); still queued"
                  .format(content_id, exc.__class__.__name__))
        posted.append({"content_id": content_id, "nonce": nonce, "message_id": message_id})
    return posted


# --------------------------------------------------------------------------
# interaction handling (transport → auth → core)
# --------------------------------------------------------------------------

def _interaction_user_id(interaction):
    user = interaction.get("user")
    if user is None:
        user = (interaction.get("member") or {}).get("user") or {}
    return user.get("id")


def handle_interaction(interaction, *, ack_fn=None, edit_fn=None):
    """Process a Discord button interaction dict. Returns a result dict.

    Order: AUTH (fail-closed, write nothing) -> parse -> approval_core.decide.
    ``ack_fn``/``edit_fn`` are optional callbacks the live bot uses to defer the
    3s ack and disable the buttons; tests omit them.
    """
    interaction = interaction or {}
    user_id = _interaction_user_id(interaction)
    channel_id = interaction.get("channel_id")

    if not is_authorized(user_id, channel_id):
        return {"ok": False, "reason": "unauthorized"}

    data = interaction.get("data") or {}
    parsed = parse_custom_id(data.get("custom_id"))
    if parsed is None:
        return {"ok": False, "reason": "bad_data"}
    nonce, variant_idx, action = parsed

    decided_by = "discord:{0}".format(user_id)
    result = approval_core.decide(nonce, variant_idx, action, decided_by=decided_by)

    if ack_fn is not None and not is_dry_run():
        try:
            ack_fn(result)
        except Exception:  # noqa: BLE001 — decision already durable; UI ack is best-effort
            pass
    if edit_fn is not None and not is_dry_run() and result.get("ok"):
        try:
            edit_fn(result)
        except Exception:  # noqa: BLE001
            pass
    return result


def handle_modal_revise(interaction, note):
    """Capture a revise note from a Discord modal. Same auth rules; records via core."""
    user_id = _interaction_user_id(interaction)
    channel_id = interaction.get("channel_id")
    if not is_authorized(user_id, channel_id):
        return {"ok": False, "reason": "unauthorized"}
    # The modal is opened from a specific message; resolve its content_id via the
    # custom_id carried on the modal (apr:<nonce>:<idx>:v) or a passed content_id.
    data = interaction.get("data") or {}
    parsed = parse_custom_id(data.get("custom_id"))
    if parsed is None:
        return {"ok": False, "reason": "bad_data"}
    nonce, variant_idx, _ = parsed
    item = approval_core._resolve_by_nonce(nonce)
    if item is None:
        return {"ok": True, "reason": "already_resolved"}
    return approval_core.record_revise(item.get("content_id"), note,
                                       decided_by="discord:{0}".format(user_id),
                                       variant_idx=variant_idx)


# --------------------------------------------------------------------------
# channel-privacy self-check (pure helper + live check in run())
# --------------------------------------------------------------------------

def channel_is_private(viewer_ids, *, allowed_ids):
    """True only if everyone who can view the channel is in allowed_ids
    (the bot + the allow-listed approver). Fail-closed: empty allowed ⇒ False."""
    allowed = {str(a) for a in allowed_ids if str(a).strip()}
    if not allowed:
        return False
    return all(str(v) in allowed for v in viewer_ids)


# --------------------------------------------------------------------------
# live runtime (discord.py imported lazily; not exercised by tests)
# --------------------------------------------------------------------------

def run():  # pragma: no cover — requires a live Discord connection + discord.py
    import asyncio
    import discord
    from discord.ext import tasks

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not set; cannot run the live bot.", file=sys.stderr)
        return 2
    chan_id = _approval_channel_id()
    if not chan_id or not _allowed_users():
        print("ERROR: DISCORD_APPROVAL_CHANNEL_ID and DISCORD_ALLOWED_USERS are required.",
              file=sys.stderr)
        return 2

    braindump_id = os.environ.get("DISCORD_BRAINDUMP_CHANNEL_ID", "").strip()
    intents = discord.Intents.none()
    intents.guilds = True
    if braindump_id:
        intents.guild_messages = True
        intents.message_content = True   # required to read raw #brain-dump text
    client = discord.Client(intents=intents)
    state = {"dispatch_ok": False}

    async def _real_send(text, components):
        channel = client.get_channel(int(chan_id)) or await client.fetch_channel(int(chan_id))
        msg = await channel.send(content=text, components=components)
        return msg.id

    def _send_sync(text, components):
        fut = asyncio.run_coroutine_threadsafe(_real_send(text, components), client.loop)
        return fut.result(timeout=15)

    @tasks.loop(seconds=60)
    async def _poster():
        if state["dispatch_ok"]:
            post_pending_once(dry_run=False, send_fn=_send_sync)

    @client.event
    async def on_ready():
        # Channel-privacy self-check — refuse to dispatch if the channel is not private.
        try:
            channel = client.get_channel(int(chan_id)) or await client.fetch_channel(int(chan_id))
            guild = channel.guild
            viewers = [m.id for m in channel.members]  # who can actually see it
            bot_id = client.user.id
            allowed = set(_allowed_users()) | {str(bot_id)}
            if channel_is_private(viewers, allowed_ids=allowed):
                state["dispatch_ok"] = True
            else:
                print("CRITICAL: approval channel is viewable by non-allowlisted members; "
                      "refusing to dispatch drafts.", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print("CRITICAL: channel-privacy self-check failed ({0}); refusing to dispatch."
                  .format(exc.__class__.__name__), file=sys.stderr)
        if not _poster.is_running():
            _poster.start()

    @client.event
    async def on_message(message):
        # Capture every #brain-dump message raw (zero schema for the founder).
        if not braindump_id or str(message.channel.id) != braindump_id:
            return
        if message.author.bot:
            return
        from lib import knowledge
        knowledge.record_braindump(
            message.id, message.content,
            author=str(message.author.id),
            ts=message.created_at.isoformat() if message.created_at else None)

    @client.event
    async def on_interaction(interaction):
        if interaction.type != discord.InteractionType.component:
            return

        async def _ack(_result):
            await interaction.response.defer()

        async def _edit(_result):
            try:
                await interaction.message.edit(components=[])
            except Exception:
                pass
        # defer first (3s ack), then run the (sync) decision core off the loop.
        await interaction.response.defer()
        as_dict = {
            "data": {"custom_id": interaction.data.get("custom_id")},
            "user": {"id": interaction.user.id},
            "channel_id": interaction.channel_id,
        }
        result = await asyncio.to_thread(handle_interaction, as_dict)
        try:
            await interaction.message.edit(components=[])
            await interaction.followup.send(
                content="{0}: {1}".format(result.get("action") or result.get("reason"),
                                          result.get("content_id", "")),
                ephemeral=True)
        except Exception:
            pass

    client.run(token)
    return 0


# --------------------------------------------------------------------------
# CLI — drives the pure functions with no live Discord (tests + e2e)
# --------------------------------------------------------------------------

def _arg_value(argv, flag):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--post-once" in argv:
        posted = post_pending_once(dry_run=True)
        print(json.dumps({"posted": posted}, ensure_ascii=False))
        return 0

    raw = _arg_value(argv, "--interaction")
    if raw is None and not sys.stdin.isatty():
        stdin = sys.stdin.read().strip()
        raw = stdin or None
    if raw is not None:
        try:
            interaction = json.loads(raw)
        except (ValueError, TypeError):
            print(json.dumps({"ok": False, "reason": "bad_json"}))
            return 1
        print(json.dumps(handle_interaction(interaction), ensure_ascii=False))
        return 0

    if "--run" in argv:
        return run()

    print(json.dumps({"ok": False, "reason": "no_input",
                      "usage": "--post-once | --interaction '<json>' | --run"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
