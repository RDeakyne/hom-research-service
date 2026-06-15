"""Web-research + synthesis via the Claude API with the web_search tool.
Handles the parts that need reasoning + live web: competitors, review complaints, homeowner concerns.
Returns JSON shaped to the Base44 ResearchIntelligence fields. Deterministic scoring lives in scoring.py.
"""
import os, json, re
from anthropic import Anthropic

MODEL = os.environ.get("RESEARCH_MODEL", "claude-sonnet-4-6")
_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
WEB = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]
EMPTY_TEARDOWN = {"common_hooks": "", "primary_texts": "", "headlines": "", "cta_buttons": "", "creative_explanations": ""}


def _ask_json(prompt: str):
    msg = _client.messages.create(
        model=MODEL, max_tokens=4000, tools=WEB,
        messages=[{"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON, no prose, no markdown fences."}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        return json.loads(m.group(1)) if m else None


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

    data = _ask_json(f"""You are doing a BRAND PERCEPTION & SENTIMENT analysis for {client_name}, a residential painting contractor serving {city}, {region}. Services: {services}.
Compare how the business describes ITSELF against how the MARKET actually perceives it. Use web search throughout and quote verbatim.

THE BUSINESS — search these to pull their EXACT self-description language:
- Website: {site}
- Google Business Profile: {gbp or '(search for it by business name + city)'}
- Social: {social_list}
Their own intake answers (their words, may be blank):
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
- perception_gap (string: where the claim diverges from the belief — the strategic opening)
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
