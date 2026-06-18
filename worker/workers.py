"""The five worker types (mission §3) — each an independent async unit.

    A. DiscoveryWorker  — find candidate leads from one source (scrape + extract)
    B. ResearchWorker   — enrich a lead (email from the clinic's own site)
    C. SocialWorker     — social presence (e.g. instagram handle)
    D. OutreachWorker   — draft a personalized pitch (draft-only)
    E. ValidationWorker — provenance + sanity gate (no fake data)

Workers are stateless: each `run()` takes a payload + the spec and returns a
result dict. Hermes Prime composes them concurrently (B∥C → D → E per lead, and
every lead in parallel). Nothing here sends anything.
"""

from __future__ import annotations

from . import leadgen
from .specs import LeadSpec, role_map

MAX_URLS = 36
MAX_DRY = 3


def urls_for_source(spec: LeadSpec, source: str) -> list[str]:
    builder = leadgen.SOURCE_BUILDERS.get(source, leadgen.SOURCE_BUILDERS["generic_web"])
    return list(dict.fromkeys(builder(spec.target, spec.location, spec.count)))


class DiscoveryWorker:
    kind = "discover"

    async def run(self, payload: dict, spec: LeadSpec) -> dict:
        source = payload["source"]
        urls = urls_for_source(spec, source)[:MAX_URLS]
        leads: list[dict] = []
        dry = 0
        for url in urls:
            if len(leads) >= spec.count:
                break
            text = await leadgen.scrape(url)
            if not text:
                continue
            got = await leadgen.extract_leads(text, list(spec.columns), spec.count - len(leads))
            for lead in got:  # provenance captured at the scrape boundary
                lead["source_url"] = url
                lead["fetched_at"] = leadgen.now_iso()
            if not got:
                dry += 1
                if dry >= MAX_DRY:
                    break  # stop burning calls on zero-yield pages
                continue
            dry = 0
            leads += got
        return {"source": source, "leads": leads}


class ResearchWorker:
    kind = "research"

    async def run(self, lead: dict, spec: LeadSpec) -> dict:
        """Fill email from the clinic's own website when requested and missing.
        Role-aware: works regardless of the column's human label."""
        roles = role_map(spec.columns)
        email_label = roles.get("email")
        if not email_label or lead.get(email_label):
            return {}
        website_label = roles.get("website")
        website = lead.get(website_label) if website_label else None
        email = await leadgen.find_email_on_site(website)
        if email:
            return {email_label: email, "_research_source_url": website}
        return {}


class SocialWorker:
    kind = "social"

    async def run(self, lead: dict, spec: LeadSpec) -> dict:
        """Fill the instagram-role column (any label) from the clinic's social presence."""
        roles = role_map(spec.columns)
        ig_label = roles.get("instagram")
        if not ig_label:
            return {}
        clinic = lead.get(roles.get("clinic", "")) or lead.get(roles.get("name", ""))
        social = await leadgen.find_social(clinic, spec.location)
        if social.get("instagram"):
            return {ig_label: social["instagram"], "_social_source_url": social.get("_social_source_url")}
        return {}


class OutreachWorker:
    kind = "outreach"

    async def run(self, lead: dict, spec: LeadSpec) -> dict:
        if not spec.outreach:
            return {}
        return {"pitch": await leadgen.draft_pitch(lead)}


class ValidationWorker:
    kind = "validate"

    async def run(self, lead: dict, spec: LeadSpec) -> dict:
        """Mandatory gate: every kept lead must trace to a real source and carry
        at least one real requested field. No provenance -> rejected."""
        reasons = []
        if not lead.get("source_url"):
            reasons.append("missing provenance (source_url)")
        if not lead.get("fetched_at"):
            reasons.append("missing fetched_at")
        if not any(lead.get(c) for c in spec.columns):
            reasons.append("no requested field populated")
        if spec.outreach:
            pitch_label = role_map(spec.columns).get("pitch", "pitch")
            if pitch_label in lead and not lead.get(pitch_label):
                reasons.append("outreach requested but pitch empty")
        return {"valid": not reasons, "reasons": reasons}


# Registry used by the queue dispatcher.
WORKERS = {
    DiscoveryWorker.kind: DiscoveryWorker(),
    ResearchWorker.kind: ResearchWorker(),
    SocialWorker.kind: SocialWorker(),
    OutreachWorker.kind: OutreachWorker(),
    ValidationWorker.kind: ValidationWorker(),
}
