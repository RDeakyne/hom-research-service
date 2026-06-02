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
Find the top 5 most relevant competing painting companies (most reviews + actively marketing) in that area. For each, use web search to get: name, website, Google rating (number), review_count (number), a one-line positioning, top_usps (3-5 from their website), and common_topics (the 5 topics/themes they emphasize most on their site).
Do NOT include ad creative — that's a manual step. Return a JSON array of objects with keys:
name, website, rating, review_count, positioning, top_usps (array), common_topics (array of 5).""") or []
    for c in data:
        c.setdefault("top_usps", []); c.setdefault("common_topics", [])
        c["ad_teardown"] = dict(EMPTY_TEARDOWN)
        c["rating"] = float(c.get("rating") or 0); c["review_count"] = int(c.get("review_count") or 0)
    return data[:5]


def complaints(city, region):
    return _ask_json(f"""Mine online reviews (Google, Yelp, BBB, complaint boards) for painting contractors in {city}, {region}. Identify the top 4 recurring COMPLAINT themes homeowners have about local painters (e.g. rotating subcontractor crews, quote-not-honored/cost creep, sloppy prep/cleanup, no-shows/slow). For each return: title, explanation (1-2 sentences), source. Return a JSON array of objects with keys: title, explanation, source.""") or []


def concerns(city, region):
    """Homeowner voice-of-customer. Reddit is often crawler-blocked; the model uses web search across
    Reddit + forums + Q&A and flags thin sourcing rather than fabricating."""
    return _ask_json(f"""Using web search across Reddit, homeowner forums, and Q&A sites, find the top 4 things HOMEOWNERS in {city}, {region} (and similar US markets) actually care/worry about most when hiring a house painter (e.g. 'is this price fair?', 'who did you actually use?', durability, disruption). For each return: title, explanation (1-2 sentences), quotes (array of short verbatim phrases you actually found - empty array if none), source. If sourcing is thin, say so in the source field. Do NOT fabricate quotes. Return a JSON array of objects with keys: title, explanation, quotes, source.""") or []
