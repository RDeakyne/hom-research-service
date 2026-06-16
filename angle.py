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
import seasonal

MODEL = os.environ.get("ANGLE_MODEL", "claude-sonnet-4-6")
_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

try:
    with open(os.path.join(os.path.dirname(__file__), "angle_index.json")) as _f:
        INDEX = json.load(_f)
except Exception:
    INDEX = {"angles": [], "built_at": None}

# Proven performers — the 80% backbone (portfolio-wide, pre-filled defaults the media buyer can accept
# as-is: primary text, headline, CTA, the DQ form spec, + top-performer reference lists). Built from
# Mongo by the proven-performers pull; refreshed alongside the index.
try:
    with open(os.path.join(os.path.dirname(__file__), "proven_performers.json")) as _f:
        PROVEN = json.load(_f)
except Exception:
    PROVEN = {}


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

=== SEASONAL OFFER CALENDAR (offers MUST match the current season + the client's climate) ===
{seasonal.context_for_prompt(services=services, region=region)}

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
     "creative_angle":"Owner / Founder Identity","offer":"the offer this ad leads with",
     "headline":"the ad headline","cta_button":"Get Quote",
     "primary_text":"the full ad primary text for THIS client (real client name/cities filled in, brand voice, ready to paste)",
     "script_variation_1":"if Video: the full video script (ready for an editor); if Image: a primary-text variation",
     "script_variation_2":"a distinct variation",
     "script_variation_3":"a distinct variation",
     "creative_direction":"shot list / visual direction (Video) or image concept + on-image text (Image)",
     "recommended_form":"All Services Volume DQ Form | 216-style Qualifying Form",
     "rationale":"one line: why this ad, for this client",
     "weight_basis":"proven-playbook|client-specific"}}
 ],
 "go_to_market_20": {{
     "video_script": "a full owner-video script tailored to THIS client (their identity + the whitespace angle), ready for an editor",
     "primary_text": "the tailored primary text for THIS client (brand voice)",
     "hook_first_150": "the FIRST 150 characters of that primary text — the scroll-stopping hook (this is what shows before 'see more')",
     "headline": "the tailored headline for THIS client",
     "cta_button": "Get Quote",
     "rationale": "why this beats the generic default for this client — cite identity + whitespace + a concern"
 }},
 "headline_recommendation": "the single highest-priority ad to build first, and why"
}}

Generate exactly 10 ad_concepts — "The Top 10 Ads to Build" — ranked by priority (1 = build first), each a COMPLETE, ready-to-build spec (a media buyer should be able to implement it as-is). Make ~7 of them proven-playbook (weight_basis "proven-playbook" — model on our winning angles/copy above, tailored to this client) and ~3 client-specific (weight_basis "client-specific" — exploit this client's whitespace/objections). Mix Video and Static. Fill the real client name and cities into primary_text/scripts (do NOT leave placeholder tokens). The go_to_market_20 is the single sharpest CLIENT-SPECIFIC recommendation. Brand voice: premium-but-approachable local painter; homeowner avatar = Gen X / Boomer / affluent; no financing language; 'curating quality', not 'surviving budget'. Scripts ready to hand to an editor."""

    data = _ask_json(prompt, max_tokens=16000) or {}
    data.setdefault("research", {})
    data.setdefault("proposed_offers", [])
    data.setdefault("proposed_angles", [])
    data.setdefault("ad_concepts", [])
    data.setdefault("headline_recommendation", "")
    data["index_built_at"] = INDEX.get("built_at")

    # Go-to-Market Strategy: 80% = proven, pre-filled defaults (portfolio-wide, accept-as-is);
    # 20% = this client's tailored recommendation (from research/identity/competition).
    data["go_to_market"] = {
        "eighty_percent": {
            "primary_text": (PROVEN.get("defaults") or {}).get("primary_text", ""),
            "hook_first_150": (PROVEN.get("defaults") or {}).get("hook_first_150", ""),
            "headline": (PROVEN.get("defaults") or {}).get("headline", ""),
            "cta_button": (PROVEN.get("defaults") or {}).get("cta_button", "Get Quote"),
            "creative_type": (PROVEN.get("defaults") or {}).get("creative_type", ""),
            "form": PROVEN.get("form", {}),
            "top_video": PROVEN.get("top_video", []),
            "top_image": PROVEN.get("top_image", []),
            "top_headlines": PROVEN.get("top_headlines", []),
            "top_primary_texts": PROVEN.get("top_primary_texts", []),
            "source": "Portfolio-proven, trailing 90 days, ranked by CPES + booked ROAS",
        },
        "twenty_percent": data.pop("go_to_market_20", {}),
    }
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
