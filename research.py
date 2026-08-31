"""Web-research + synthesis via the Claude API with the web_search tool.
Handles the parts that need reasoning + live web: competitors, review complaints, homeowner concerns.
Returns JSON shaped to the Base44 ResearchIntelligence fields. Deterministic scoring lives in scoring.py.
"""
import os, re
from anthropic import Anthropic
from jsonsalvage import salvage

MODEL = os.environ.get("RESEARCH_MODEL", "claude-sonnet-4-6")
# Identity-linked API keys must name the workspace they act in; plain workspace-scoped keys must NOT
# send the header. Sending it only when ANTHROPIC_WORKSPACE_ID is set supports both key types.
_WS = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"],
                    **({"default_headers": {"anthropic-workspace-id": _WS}} if _WS else {}))
WEB = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]
EMPTY_TEARDOWN = {"common_hooks": "", "primary_texts": "", "headlines": "", "cta_buttons": "", "creative_explanations": ""}


def _ask_json(prompt: str):
    """Ask the model for JSON and recover it best-effort. Verbatim-quote prompts routinely yield a
    stray unescaped quote/comma, and long answers truncate — so we NEVER let a parse error raise.
    On unrecoverable output we return None; callers default with `or {}` / `or []` so one bad section
    can't kill the whole run (the deterministic zip scoring must always survive). max_tokens matches
    angle.py (8000) to cut down on truncation of the verbose identity/competitor answers."""
    try:
        msg = _client.messages.create(
            model=MODEL, max_tokens=8000, tools=WEB,
            messages=[{"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON, no prose, no markdown fences."}],
        )
    except Exception:
        return None
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return salvage(text)


def competitors(client_name, city, region, services, fund_zips):
    data = _ask_json(f"""You are doing competitor research for {client_name}, a painting contractor serving {city}, {region} (zips: {', '.join(fund_zips[:8])}). Services: {services}.
Find the top 5 most relevant competing painting companies (most reviews + actively marketing) in that area. For each, use web search to get: name, website, Google rating (number), review_count (number), a one-line positioning, top_usps (3-5 from their website), common_topics (the 5 topics/themes they emphasize most on their site), and facebook_page_url (their Facebook Page URL — find the Facebook link on their website; if none is found, return an empty string).
Do NOT include ad creative — that's a separate step. Return a JSON array of objects with keys:
name, website, rating, review_count, positioning, top_usps (array), common_topics (array of 5), facebook_page_url.""") or []
    for c in data:
        c.setdefault("top_usps", []); c.setdefault("common_topics", [])
        c.setdefault("facebook_page_url", "")
        c["ad_teardown"] = dict(EMPTY_TEARDOWN)
        c["rating"] = float(c.get("rating") or 0); c["review_count"] = int(c.get("review_count") or 0)
    return data[:5]


# --- Core Messaging Framework -------------------------------------------------
# The portal's newer intake writes 60+ `cmf_*` fields (positioning, founder story, ICA, offer stack,
# proof, voice). The OLD identity fields (business_story / story_differentiator / story_benefit /
# unique_selling_points / target_customer) are blank on every client onboarded since that switch —
# which is why identity analysis had no "self" side to work with. We now pass EVERY populated cmf_
# field through, per Ricky ("take all of the fields from messaging guide"), and keep the legacy
# fields as a fallback for older records that still have them.
# Sized off real records: Porcupine's 61 populated fields are ~18k chars (~4.5k tokens) in full, so
# these are SAFETY NETS, not routine truncation — at 14k the alphabetical tail was silently dropping.
_CMF_MAX_FIELD = 1200     # per-field clip (longest real field seen: cmf_testimonials at 1038)
_CMF_MAX_TOTAL = 40000    # overall clip; only bites on a pathologically long guide


def _messaging_guide(ci: dict) -> str:
    """Render every populated cmf_* field as a labelled block for the prompt."""
    out, total = [], 0
    for k in sorted(ci or {}):
        if not k.startswith("cmf_"):
            continue
        v = ci[k]
        if isinstance(v, (list, tuple)):
            v = "\n".join(str(x) for x in v)
        v = str(v or "").strip()
        if not v:
            continue
        block = "[{}]\n{}".format(k[4:].replace("_", " ").upper(), v[:_CMF_MAX_FIELD])
        if total + len(block) > _CMF_MAX_TOTAL:
            break
        out.append(block)
        total += len(block)
    return "\n\n".join(out)


def identity(client_name, city, region, website, services, company_info):
    """Brand Perception & Sentiment (Limitless Mission 1) for the CLIENT's OWN painting company.
    Compares how they describe THEMSELVES (their site/GBP/social + intake answers) against how the
    MARKET actually perceives them (reviews/forums). Claude + web search. Flags thin sourcing rather
    than fabricating. Returns a dict matching the identity_analysis Base44 field."""
    ci = company_info if isinstance(company_info, dict) else {}
    site = website or ci.get("website") or "(no website provided)"
    gbp = ci.get("google_business_profile_url") or ""
    socials = ci.get("social_media") or {}
    social_list = ", ".join(f"{k}: {v}" for k, v in socials.items() if v) or "(none provided)"
    story = ci.get("business_story") or ""
    diff = ci.get("story_differentiator") or ci.get("story_benefit") or ""
    usp = ci.get("unique_selling_points") or ""
    target = ci.get("target_customer") or ""
    offers = ci.get("current_offers") or ci.get("past_offers") or ""
    guide = _messaging_guide(ci)

    data = _ask_json(f"""You are doing a BRAND PERCEPTION & SENTIMENT analysis for {client_name}, a residential painting contractor serving {city}, {region}. Services: {services}.
Compare how the business describes ITSELF against how the MARKET actually perceives it. Use web search throughout and quote verbatim.

THE BUSINESS — search these to pull their EXACT self-description language:
- Website: {site}
- Google Business Profile: {gbp or '(search for it by business name + city)'}
- Social: {social_list}
THEIR CORE MESSAGING FRAMEWORK - the messaging guide they filled out in our portal, in their own
words. This is the AUTHORITATIVE source for the "self" side. Use ALL of it. Treat it as what they
CLAIM about themselves, not as verified fact - your job is to test those claims against the market.
{guide or "(no messaging framework on file)"}

Legacy intake fields (older records only; usually blank - ignore if empty):
- Story: {story}
- Differentiator: {diff}
- Unique selling points: {usp}
- Target customer: {target}
- Current offer: {offers}

THE MARKET — mine Google reviews, Yelp, BBB, Facebook, Nextdoor, Reddit and local forums for how CUSTOMERS and PROSPECTS describe THIS specific business. Quote verbatim. If review volume is thin or you cannot confidently identify the business, say so in sourcing_note and do NOT fabricate.

Return a JSON object with EXACTLY these keys:
- self_description (string: how they position themselves, from site/GBP/social + intake)
- claimed_differentiators (array of strings)
- market_perception (string: how customers/prospects actually describe them, with verbatim phrasing)
- perception_gap (string: where their Core Messaging Framework claims diverge from what the market actually believes - the strategic opening. Name the specific claimed pillar that reviews do NOT corroborate, and the strength customers cite that their messaging UNDER-plays)
- confirmed_strengths (array of objects with keys: strength, evidence, source)
- vulnerabilities (array of objects with keys: issue, evidence, source)
- ownable_emotional_territory (string: the space the gap + strengths point to that they can credibly own)
- sourcing_note (string: flag thin/uncertain sourcing here)""") or {}

    data.setdefault("claimed_differentiators", [])
    data.setdefault("confirmed_strengths", [])
    data.setdefault("vulnerabilities", [])
    for k in ("self_description", "market_perception", "perception_gap",
              "ownable_emotional_territory", "sourcing_note"):
        data.setdefault(k, "")
    return data


def complaints(city, region):
    return _ask_json(f"""Mine online reviews (Google, Yelp, BBB, complaint boards) for painting contractors in {city}, {region}. Identify the top 4 recurring COMPLAINT themes homeowners have about local painters (e.g. rotating subcontractor crews, quote-not-honored/cost creep, sloppy prep/cleanup, no-shows/slow). For each return: title, explanation (1-2 sentences), source. Return a JSON array of objects with keys: title, explanation, source.""") or []


def concerns(city, region):
    """Homeowner voice-of-customer. Reddit is often crawler-blocked; the model uses web search across
    Reddit + forums + Q&A and flags thin sourcing rather than fabricating."""
    return _ask_json(f"""Using web search across Reddit, homeowner forums, and Q&A sites, find the top 4 things HOMEOWNERS in {city}, {region} (and similar US markets) actually care/worry about most when hiring a house painter (e.g. 'is this price fair?', 'who did you actually use?', durability, disruption). For each return: title, explanation (1-2 sentences), quotes (array of short verbatim phrases you actually found - empty array if none), source. If sourcing is thin, say so in the source field. Do NOT fabricate quotes. Return a JSON array of objects with keys: title, explanation, quotes, source.""") or []
