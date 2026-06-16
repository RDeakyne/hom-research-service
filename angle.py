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


def _ask_json(prompt, max_tokens=8000):
    msg = _client.messages.create(
        model=MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON, no prose, no fences."}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        return json.loads(m.group(1)) if m else None


def strategy(client_name, city, region, services, concerns, complaints, competitor_ad_intel, identity):
    """Full Meta Ad Strategy — prioritized, build-ready ad concepts that map to the portal's Content
    Asset fields. 80% grounded in what converts across our portfolio (angle_index winners by CPES/ROAS),
    20% in this client's research/identity/competition. Produces the 3-tab payload (research / proposed
    offers & angles / ad concepts with 3 script variations each)."""
    angles = INDEX.get("angles", [])
    top_ads = INDEX.get("top_ads", [])[:15]
    angles_str = "\n".join(
        f"- {r['name']}: CPES {('$' + str(int(r['cpes']))) if r.get('cpes') else 'n/a'}, "
        f"ROAS {r.get('roas')}, set_rate {r.get('set_rate')}, {r['n_ads']} ads"
        for r in angles) or "(index unavailable)"
    winners_str = "\n".join(
        f"- [{w.get('angle_name')}] CPES ${int(w['cpes'])} ROAS {w.get('roas')} | "
        f"HEADLINE: {w.get('headline') or '(none)'} | COPY: {(w.get('primary_text') or '')[:240]}"
        for w in top_ads) or "(no winning ads in index)"
    comp = competitor_ad_intel or {}
    white = comp.get("whitespace", {}) or {}
    coverage = {}
    for a in comp.get("advertisers", []):
        for ang in (a.get("dominant_angles") or []):
            coverage[ang] = coverage.get(ang, 0) + 1
    comp_str = ", ".join(f"{k} ({v})" for k, v in sorted(coverage.items(), key=lambda x: -x[1])) \
        or "(no competitor ad data)"
    concerns_str = "; ".join(c.get("title", "") for c in (concerns or [])) or "(none)"
    complaints_str = "; ".join(c.get("title", "") for c in (complaints or [])) or "(none)"
    ident = identity or {}
    strengths = "; ".join(s.get("strength", "") for s in (ident.get("confirmed_strengths") or [])) or "(none)"
    territory = ident.get("ownable_emotional_territory", "") or "(not assessed)"

    prompt = f"""You are the Meta Ad Strategist for {client_name}, a residential painting contractor in {city}, {region}. Services: {services}.
Produce a PRIORITIZED Meta ad plan that tells the media team EXACTLY what ads to build. Weighting:
- 80%: what has PROVEN to work across our client portfolio (lowest cost per estimate set, best ROAS, best set rate). Model new ads on our winning copy below.
- 20%: this client's specific edge (identity, competitive whitespace, local homeowner concerns).

=== OUR PROVEN PERFORMANCE (the 80% — portfolio-wide) ===
ANGLES ranked (lower CPES + higher ROAS = better):
{angles_str}

OUR WINNING ADS (real copy that produced estimate sets — model new concepts on these patterns):
{winners_str}

=== THIS CLIENT'S EDGE (the 20%) ===
Identity strengths: {strengths}
Ownable territory: {territory}
Competitor angles already saturated (avoid copying): {comp_str}
Competitor WHITESPACE (angles nobody runs): {white.get('angles_nobody_runs')}
Objections nobody addresses: {white.get('objections_nobody_addresses')}
Homeowner concerns: {concerns_str}
Complaints about local painters: {complaints_str}

ANGLE TAXONOMY (use these names for creative_angle):
{tax.list_for_prompt()}

Return a JSON object with EXACTLY these keys:
{{
 "research": {{
   "what_works_for_us": "2-3 sentences: the proven angles/offers/copy patterns that win portfolio-wide, with CPES/ROAS proof",
   "client_edge": "2-3 sentences: this client's whitespace + identity advantage (the 20%)",
   "proven_angles": [{{"angle":"key","name":"...","cpes":0,"roas":0,"set_rate":0,"n_ads":0}}],
   "top_concerns": ["..."],
   "competitor_whitespace": ["..."]
 }},
 "proposed_offers": [{{"offer":"...","rationale":"...","basis":"historical|research"}}],
 "proposed_angles": [{{"angle":"key","name":"...","verdict":"OWN|TEST|AVOID","priority":1,"rationale":"...","basis":"80% historical|20% research","cpes_proof":"$X across N ads"}}],
 "ad_concepts": [
   {{"priority":1,"asset_name":"short punchy name","video_or_image":"Video|Image",
     "awareness_stage":"Top of Funnel|Middle of Funnel|Bottom of Funnel","service":["Exterior|Interior|Cabinet"],
     "creative_angle":"Owner / Founder Identity","offer":"Free estimate",
     "script_variation_1":"full script if Video, or primary text if Image — brand voice, ready for an editor",
     "script_variation_2":"a distinct variation",
     "script_variation_3":"a distinct variation",
     "creative_direction":"shot list / visual direction (Video) or image concept (Image)",
     "rationale":"why this ad will work for THIS client",
     "proof":"the 80/20 basis — cite the CPES/ROAS proof + the client edge",
     "weight":"mostly-historical|balanced|mostly-research"}}
 ],
 "headline_recommendation": "the single highest-priority ad to build first, and why"
}}

Generate 6 ad_concepts, ranked by priority (1 = build first). Lead with mostly-historical proven concepts; include 1-2 that exploit this client's whitespace. Brand voice: premium-but-approachable local painter; homeowner avatar = Gen X / Boomer / affluent; no financing language; 'curating quality', not 'surviving budget'. Scripts ready to hand to an editor."""

    data = _ask_json(prompt, max_tokens=16000) or {}
    data.setdefault("research", {})
    data.setdefault("proposed_offers", [])
    data.setdefault("proposed_angles", [])
    data.setdefault("ad_concepts", [])
    data.setdefault("headline_recommendation", "")
    data["index_built_at"] = INDEX.get("built_at")
    return data


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
