"""Ad Angle Intelligence Engine (Phase 3) — the differentiator.

Combines four signals and recommends which ad angles a client should run to STAND OUT (not copy):
  1. Concern Resonance — homeowner concerns/complaints (what they actually care about)
  2. Our Proof        — angle_index.json: what converts for us, by cost per estimate set (our moat)
  3. Competitor Whitespace — from the Manus Ad Library teardown (what nobody runs)
  4. Client Credibility — the client's ownable territory/strengths (from Identity Analysis)

Output: an Angle-Match Score per candidate angle, bucketed OWN / TEST / AVOID, with ready hooks.
Reads the precomputed index at import; no Mongo at runtime.
"""
import os, json, re
from anthropic import Anthropic
import angle_taxonomy as tax

MODEL = os.environ.get("ANGLE_MODEL", "claude-sonnet-4-6")
_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

try:
    with open(os.path.join(os.path.dirname(__file__), "angle_index.json")) as _f:
        INDEX = json.load(_f)
except Exception:
    INDEX = {"angles": [], "built_at": None}


def _ask_json(prompt):
    msg = _client.messages.create(
        model=MODEL, max_tokens=8000,
        messages=[{"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON, no prose, no fences."}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        return json.loads(m.group(1)) if m else None


def recommend(client_name, city, region, concerns, complaints, competitor_ad_intel, identity):
    our = INDEX.get("angles", [])
    our_str = "\n".join(
        f"- {r['name']}: CPES {('$' + str(int(r['cpes']))) if r.get('cpes') else 'n/a'}, "
        f"set_rate {r.get('set_rate')}, {r['n_ads']} ads, ${int(r['spend'])} spend"
        for r in our) or "(angle index unavailable)"

    comp = competitor_ad_intel or {}
    coverage = {}
    for a in comp.get("advertisers", []):
        for ang in (a.get("dominant_angles") or []):
            coverage[ang] = coverage.get(ang, 0) + 1
    comp_str = ", ".join(f"{k} ({v})" for k, v in sorted(coverage.items(), key=lambda x: -x[1])) \
        or "(no competitor ad-library data yet)"
    white = comp.get("whitespace", {}) or {}

    concerns_str = "; ".join(c.get("title", "") for c in (concerns or [])) or "(none)"
    complaints_str = "; ".join(c.get("title", "") for c in (complaints or [])) or "(none)"
    ident = identity or {}
    strengths = "; ".join(s.get("strength", "") for s in (ident.get("confirmed_strengths") or [])) or "(none)"
    territory = ident.get("ownable_emotional_territory", "") or "(not assessed)"

    prompt = f"""You are the Ad Angle Strategist for {client_name}, a residential painting contractor in {city}, {region}.
Recommend which ad ANGLES this client should run to STAND OUT — do NOT just copy competitors. The SWEET SPOT is an
angle that (1) hits a real homeowner concern, (2) competitors are NOT running (whitespace), (3) has PROOF it converts
in our portfolio (low cost per estimate set), and (4) this client can credibly own.

ANGLE TAXONOMY (use these keys/names):
{tax.list_for_prompt()}

WHAT CONVERTS FOR US — portfolio-wide, ranked by cost per estimate set (LOWER $ = better; this is first-party proof):
{our_str}

WHAT COMPETITORS RUN — dominant angles (count of competitors using each):
{comp_str}
WHITESPACE - angles nobody runs: {white.get('angles_nobody_runs') or 'n/a'}
WHITESPACE - objections nobody addresses: {white.get('objections_nobody_addresses') or 'n/a'}

HOMEOWNER CONCERNS: {concerns_str}
COMPLAINTS about local painters: {complaints_str}

THIS CLIENT can credibly own — strengths: {strengths} | ownable territory: {territory}

For each candidate angle, assign an Angle-Match Score 0-100 weighing concern resonance, our proof (CPES),
competitor whitespace, and client credibility. Recommend 4-6 angles bucketed OWN / TEST / AVOID, highest score first.
Lead with angles that are BOTH proven-for-us AND whitespace. For each recommended angle give 2 ready-to-run hooks
written in the brand voice of a premium-but-approachable local painter (homeowner avatar = Gen X / Boomer / affluent;
no financing language; 'curating quality', not 'surviving budget').

Return ONLY this JSON:
{{
 "our_winning_angles": [{{"angle": "key", "name": "...", "cpes": 0, "set_rate": 0, "n_ads": 0, "proof_tier": "Proven|Emerging|Thin"}}],
 "competitor_angle_coverage": [{{"angle": "...", "competitors_running": 0, "saturation": "Low|Medium|High"}}],
 "recommended_angles": [{{"angle": "key", "name": "...", "verdict": "OWN|TEST|AVOID", "angle_match_score": 0,
    "concern_resonance": "...", "our_proof": "...", "whitespace": "...", "client_credibility": "...",
    "why": "the sweet-spot rationale across all four axes", "example_hooks": ["...", "..."], "creative_direction": "..."}}],
 "headline_recommendation": "the single angle to lead with, and why"
}}"""

    data = _ask_json(prompt) or {}
    if not data.get("our_winning_angles"):
        data["our_winning_angles"] = [
            {"angle": r["angle"], "name": r["name"], "cpes": r.get("cpes"), "set_rate": r.get("set_rate"),
             "n_ads": r.get("n_ads"), "proof_tier": "Proven" if r.get("cpes") else "Thin"} for r in our[:5]]
    data.setdefault("competitor_angle_coverage", [])
    data.setdefault("recommended_angles", [])
    data.setdefault("headline_recommendation", "")
    data["index_built_at"] = INDEX.get("built_at")
    return data
