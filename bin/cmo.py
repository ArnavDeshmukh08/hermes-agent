#!/usr/bin/env python3
"""Stream C — CMO Agent (Hermes Prime Phase-2 MVP).

Stage 2 of the content pipeline: research -> CMO -> approval.

Reads recent unconsumed research findings, builds ONE lean prompt (< 6k input
tokens), asks the LLM for LinkedIn variants self-scored on hook/clarity/
voice_match/cta, then deterministically re-scores and caps before writing a
single content draft to memory/content/<content-id>.json (status "pending").

Pure stdlib. Works fully and deterministically under HERMES_LLM_MOCK=1.

Guardrails honored:
- Never sends. Produces a pending draft only; sending is the approval stage.
- No fabricated data: the prompt forbids inventing stats; deterministic caps
  punish banned words / bad length / emoji spam / missing CTA.
- Honest about failure: no eligible research -> clean no-op (exit 0); any
  failure during generate/score/write -> nothing is marked consumed.
"""

import json
import sys
from pathlib import Path

# --- sys.path shim: make `lib` importable from repo root --------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lib import contracts, store, llm  # noqa: E402

# ---------------------------------------------------------------------------
# Constants (baked in so the prompt stays lean even if voice files are sparse)
# ---------------------------------------------------------------------------
PERSONA = "jack"
PLATFORM = "linkedin"

# Selection
RECENCY_DAYS = 14
PRIORITY_TAGS = {"retention", "no-show", "whatsapp", "clinic", "healthcare", "founder"}

# Voice hard rules (mirrors memory/voice/style.md, baked for determinism)
BANNED_WORDS = ("thrilled", "delve", "game-changer")
MAX_EMOJIS = 2
MIN_LEN = 200
MAX_LEN = 2200

# Prompt trimming caps
STYLE_HEAD_CHARS = 500
SAMPLE_MAX_CHARS = 600
SUMMARY_MAX_CHARS = 400
EXCERPT_MAX_CHARS = 300
SECONDARY_MAX_CHARS = 200
TOKEN_CEILING = 5000  # design ceiling under the 6k input requirement

# Composite score weights (sum to 1.0)
W_HOOK = 0.35
W_CLARITY = 0.25
W_VOICE = 0.25
W_CTA = 0.15

# CTA detection: a question mark, or one of these light call phrases.
_CTA_PHRASES = (
    "what's", "what is", "how do", "tell me", "let me know", "drop a",
    "share your", "curious", "your take", "agree", "thoughts", "comment",
    "reply", "dm me", "follow", "try it",
)

# Voice rules condensed for the system prompt (lean).
_VOICE_RULES = (
    "Authentic technical-founder voice. First person, building in public. "
    "Direct, concrete, specific, a little opinionated. Lead with a sharp hook "
    "(a number, a tension, or a contrarian take). Short lines, plain words, "
    "one idea per post. End with a light CTA or a question, never salesy. "
    f"Length {MIN_LEN}-{MAX_LEN} chars. Max {MAX_EMOJIS} emojis. "
    "Never invent stats or quotes; only use facts from the brief. "
    f"BANNED WORDS (never use): {', '.join(BANNED_WORDS)}."
)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def _tag_affinity(finding):
    """Rank score: +2 per priority-tag hit, +1 per any tag. Higher is better."""
    tags = [str(t).lower() for t in (finding.get("tags") or [])]
    score = 0
    for t in tags:
        score += 2 if t in PRIORITY_TAGS else 1
    return score


def _recency_key(finding):
    """Sort key for recency (newer first). Falls back to empty string."""
    return str(finding.get("run_date") or finding.get("created_at") or "")


def select_topics(findings):
    """Pick 1 primary (+ <=1 secondary sharing a tag) from eligible findings.

    Returns (primary, secondary_or_None). Ranking: tag affinity, then recency.
    """
    if not findings:
        return None, None

    ranked = sorted(
        findings,
        key=lambda f: (_tag_affinity(f), _recency_key(f)),
        reverse=True,
    )
    primary = ranked[0]
    primary_tags = {str(t).lower() for t in (primary.get("tags") or [])}

    secondary = None
    for cand in ranked[1:]:
        if cand.get("id") == primary.get("id"):
            continue
        cand_tags = {str(t).lower() for t in (cand.get("tags") or [])}
        if primary_tags & cand_tags:
            secondary = cand
            break

    return primary, secondary


# ---------------------------------------------------------------------------
# Voice material
# ---------------------------------------------------------------------------
def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def read_style_slice():
    """A trimmed head slice of style.md (constants already carry hard rules)."""
    raw = _read_text(store.voice_dir() / "style.md")
    return raw.strip()[:STYLE_HEAD_CHARS]


def read_samples(k=2):
    """Pick up to k voice samples from samples.md, each truncated ~600 chars.

    samples.md uses `---` separators between example posts.
    """
    raw = _read_text(store.voice_dir() / "samples.md")
    if not raw:
        return []
    # Split on horizontal-rule separators; keep substantive blocks only.
    blocks = [b.strip() for b in raw.split("\n---")]
    samples = []
    for b in blocks:
        # Drop the leading markdown heading line(s) if present.
        body = "\n".join(
            ln for ln in b.splitlines() if not ln.lstrip().startswith("#")
        ).strip()
        if len(body) >= MIN_LEN:
            samples.append(body[:SAMPLE_MAX_CHARS])
        if len(samples) >= k:
            break
    return samples


# ---------------------------------------------------------------------------
# Prompt construction + <6k token guard
# ---------------------------------------------------------------------------
def build_system_prompt():
    return (
        "You are Jack, a technical startup founder (Vytal: clinic patient "
        "retention via WhatsApp) writing LinkedIn posts.\n\n"
        f"VOICE:\n{_VOICE_RULES}\n\n"
        "TASK:\nGiven ONE research brief, write 2 distinct LinkedIn post "
        "variants, each using a DIFFERENT angle (e.g. contrarian, story).\n\n"
        "Then SELF-SCORE each variant 0-5 (integers) on:\n"
        "- hook: does line 1 stop the scroll?\n"
        "- clarity: is the single point obvious in one read?\n"
        "- voice_match: sounds like the voice + samples, not corporate?\n"
        "- cta: clear, non-salesy invitation to engage?\n\n"
        "OUTPUT: strict JSON only, no prose, matching:\n"
        '{"variants":[{"text":"...","angle":"...","hook":0,"clarity":0,'
        '"voice_match":0,"cta":0}]}'
    )


def _brief_block(primary, secondary, include_secondary, excerpt_chars):
    summary = str(primary.get("summary") or "")[:SUMMARY_MAX_CHARS]
    excerpt = str(primary.get("raw_excerpt") or "")[:excerpt_chars]
    lines = [
        "RESEARCH BRIEF (use only these facts):",
        f"Topic: {primary.get('topic') or primary.get('id') or ''}",
        f"Summary: {summary}",
    ]
    if excerpt:
        lines.append(f"Excerpt: {excerpt}")
    if include_secondary and secondary is not None:
        sec = str(secondary.get("summary") or "")[:SECONDARY_MAX_CHARS]
        if sec:
            lines.append(f"Supporting (optional): {sec}")
    return "\n".join(lines)


def _samples_block(samples):
    if not samples:
        return ""
    out = ["", "VOICE SAMPLES (match this tone, do NOT copy):"]
    for i, s in enumerate(samples, 1):
        out.append(f"--- sample {i} ---")
        out.append(s)
    return "\n".join(out)


def build_user_prompt(primary, secondary, samples, *, include_secondary,
                      include_sample2, excerpt_chars):
    use_samples = samples[: (2 if include_sample2 else 1)]
    parts = [
        _brief_block(primary, secondary, include_secondary, excerpt_chars),
        _samples_block(use_samples),
        "",
        "Write 2 variants now. JSON only.",
    ]
    return "\n".join(p for p in parts if p != "")


def build_prompt_within_budget(primary, secondary, samples):
    """Build (system, user) and progressively trim until est tokens <= ceiling.

    Drop order (per spec): sample2 -> secondary -> trim excerpt.
    """
    system = build_system_prompt()

    # Knobs, tried in increasingly aggressive order.
    plans = [
        dict(include_sample2=True, include_secondary=True,
             excerpt_chars=EXCERPT_MAX_CHARS),
        dict(include_sample2=False, include_secondary=True,
             excerpt_chars=EXCERPT_MAX_CHARS),   # drop sample 2
        dict(include_sample2=False, include_secondary=False,
             excerpt_chars=EXCERPT_MAX_CHARS),   # drop secondary
        dict(include_sample2=False, include_secondary=False,
             excerpt_chars=120),                  # trim excerpt
        dict(include_sample2=False, include_secondary=False,
             excerpt_chars=0),                    # drop excerpt entirely
    ]

    user = None
    for plan in plans:
        user = build_user_prompt(primary, secondary, samples, **plan)
        est = llm.estimate_tokens(system + user)
        if est <= TOKEN_CEILING:
            return system, user, est
    # Even the leanest plan: return it (and its estimate) rather than fail.
    est = llm.estimate_tokens(system + user)
    return system, user, est


# ---------------------------------------------------------------------------
# Scoring: normalize /5 -> 0..1, composite, deterministic downward caps
# ---------------------------------------------------------------------------
def _emoji_count(text):
    count = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x1F300 <= cp <= 0x1FAFF      # symbols & pictographs, supplemental
            or 0x2600 <= cp <= 0x27BF     # misc symbols + dingbats
            or 0x1F1E6 <= cp <= 0x1F1FF   # regional indicators (flags)
            or cp in (0x2B50, 0x2705, 0x2764, 0x203C, 0x2049)  # NB: not 0xFE0F (variation selector, not a glyph)
        ):
            count += 1
    return count


def _has_cta(text):
    if "?" in text:
        return True
    low = text.lower()
    return any(p in low for p in _CTA_PHRASES)


def _has_banned_word(text):
    low = text.lower()
    return any(w in low for w in BANNED_WORDS)


def _clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _sub(raw):
    """Normalize an LLM sub-score (0-5 int) to 0..1, defensively."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.0
    return _clamp01(v / 5.0)


def score_variant(raw_variant):
    """Normalize sub-scores, apply deterministic caps, flag links.

    Returns a draft-shaped variant dict WITHOUT idx (caller reindexes):
        {text, angle, score, score_breakdown:{...}, flagged_links:[...]}
    """
    text = str(raw_variant.get("text") or "")
    angle = str(raw_variant.get("angle") or "general")

    hook = _sub(raw_variant.get("hook"))
    clarity = _sub(raw_variant.get("clarity"))
    voice = _sub(raw_variant.get("voice_match"))
    cta = _sub(raw_variant.get("cta"))

    # --- deterministic guards (cap DOWNWARD only; never raise) -------------
    if _has_banned_word(text):
        voice = 0.0
    length = len(text)
    if length < MIN_LEN or length > MAX_LEN:
        clarity = min(clarity, 0.3)
    if _emoji_count(text) > MAX_EMOJIS:
        voice = min(voice, 0.4)
    if not _has_cta(text):
        cta = min(cta, 0.3)

    composite = round(
        W_HOOK * hook + W_CLARITY * clarity + W_VOICE * voice + W_CTA * cta, 4
    )

    variant = {
        "text": text,
        "angle": angle,
        "score": composite,
        "score_breakdown": {
            "hook": round(hook, 4),
            "clarity": round(clarity, 4),
            "voice_match": round(voice, 4),
            "cta": round(cta, 4),
        },
    }

    # Security guard: surface (do not strip) non-allowlisted URLs.
    flagged = contracts.flag_unallowlisted_urls(text)
    if flagged:
        variant["flagged_links"] = list(flagged)

    return variant


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # 1. Select findings.
    findings = store.load_findings(unconsumed_only=True, days=RECENCY_DAYS)
    if not findings:
        print("cmo: no eligible research, skipping (no-op)")
        return 0

    primary, secondary = select_topics(findings)
    if primary is None:
        print("cmo: no eligible research, skipping (no-op)")
        return 0

    consumed_ids = {primary.get("id")}
    source_ids = [primary.get("id")]
    if secondary is not None:
        consumed_ids.add(secondary.get("id"))
        source_ids.append(secondary.get("id"))
    consumed_ids = {i for i in consumed_ids if i}
    source_ids = [i for i in source_ids if i]

    # 2-3. Voice + lean prompt under the 6k input ceiling.
    read_style_slice()  # read for completeness; hard rules already baked above
    samples = read_samples(k=2)
    system, user, est_tokens = build_prompt_within_budget(
        primary, secondary, samples
    )

    # 4. Generate + score. On ANY failure: mark nothing consumed, no draft.
    try:
        raw = llm.complete(system, user, json_only=True)
        # llm.complete(json_only=True) returns a JSON *string* (the mock and both
        # real providers do); parse it before reading "variants".
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = None
        raw_variants = raw.get("variants") if isinstance(raw, dict) else None
        if not raw_variants:
            print("cmo: llm returned no variants, skipping (nothing consumed)")
            return 0

        scored = [score_variant(v) for v in raw_variants]

        # 5. Sort best-first, reindex idx.
        scored.sort(key=lambda v: v["score"], reverse=True)
        for i, v in enumerate(scored):
            v["idx"] = i

        draft = contracts.new_draft(
            contracts.make_content_id(_slug_for(primary)),
            source_ids,
            scored,
            persona=PERSONA,
            platform=PLATFORM,
        )
        draft_errs = contracts.validate_draft(draft)  # returns list[str]; [] == valid
        if draft_errs:
            raise ValueError("draft failed contract validation: " + "; ".join(draft_errs))
        path = store.save_draft(draft)

        # Only after a clean write do we mark findings consumed.
        store.mark_consumed(consumed_ids)
    except Exception as exc:  # honest-about-failure: clean no-op, nothing consumed
        print(f"cmo: generation/write failed ({exc.__class__.__name__}: {exc}); "
              "nothing consumed, no draft written")
        return 1

    # 6. One-line summary.
    top = scored[0]["score"] if scored else 0.0
    flagged_total = sum(len(v.get("flagged_links", [])) for v in scored)
    flag_note = f" flagged_links={flagged_total}" if flagged_total else ""
    print(
        f"cmo: wrote {draft['id']} | variants={len(scored)} | "
        f"top_score={top:.3f} | est_in_tokens={est_tokens} | "
        f"sources={','.join(source_ids)}{flag_note} | {path}"
    )
    return 0


def _slug_for(finding):
    """Derive a slug for the content-id from the finding's topic/id/tags."""
    src = (
        finding.get("topic")
        or finding.get("summary")
        or finding.get("id")
        or "post"
    )
    return contracts.slugify(str(src))


if __name__ == "__main__":
    sys.exit(main())
