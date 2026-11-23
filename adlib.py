"""Meta Ad Library competitor teardown via an Apify scraper + Claude (replaces Manus, Nov 2026).

Why: Manus (agentic browser) stopped working for us ~2026-08-31 (API key revoked after the Meta/Manus
split). Meta has no Ad Library API for commercial US ads. So:

  1. SCRAPE  — Apify actor `whoareyouanas/meta-ad-scraper` pulls ACTIVE ads for (a) each seed competitor
               by name and (b) local keyword searches ("<city> painting", ...) to DISCOVER who actually
               advertises. Hard caps on ads per query keep cost ~$2/client ($6 per 1,000 ads).
  2. FILTER  — Claude keeps only local residential painting contractors (keyword searches return circuses,
               pottery studios, window companies; franchise-name searches return other cities' locations).
  3. COUNT   — code (not the model) computes ads per advertiser, formats, start dates, days running.
               60+ days running = proven (Limitless method).
  4. ANALYZE — Claude reads the real ad copy and writes offers / hooks / angles / whitespace.

Output = the SAME `competitor_ad_intel` shape Manus produced, so Base44 and angle.strategy are unchanged.
Facts (who, how many ads, how long) come from the scraper; only interpretation comes from the model.
"""
import os, re, json, datetime
from concurrent.futures import ThreadPoolExecutor
import httpx
import angle_taxonomy as tax
from jsonsalvage import salvage

TOKEN = os.environ.get("APIFY_TOKEN", "")
ACTOR = os.environ.get("APIFY_AD_ACTOR", "whoareyouanas~meta-ad-scraper")
SEED_MAX_ADS = int(os.environ.get("ADLIB_SEED_MAX_ADS", "25"))
KEYWORD_MAX_ADS = int(os.environ.get("ADLIB_KEYWORD_MAX_ADS", "40"))
PROVEN_DAYS = 60

EMPTY = {"advertisers": [], "ad_longevity_table": [], "offers_in_market": [],
         "messaging_patterns": [], "whitespace": {"angles_nobody_runs": [], "objections_nobody_addresses": [],
                                                  "obvious_differentiator_invisible_in_all_ads": ""},
         "sourcing_note": ""}


# ---------------------------------------------------------------- 1. scrape
def _scrape(query, max_ads):
    """One actor run, synchronous. Returns a list of ad dicts ([] on any failure — one bad query
    must not sink the teardown)."""
    try:
        r = httpx.post(
            f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items",
            params={"timeout": 280}, timeout=300,
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"country": "US", "searchQuery": query, "activeStatus": "active", "adType": "all",
                  "mediaType": "all", "sortMode": "relevance", "maxAds": max_ads,
                  "maxConcurrency": 1, "requestHandlerTimeoutSecs": 240})
        data = r.json()
        if not isinstance(data, list):
            print(f"[adlib] query {query!r}: HTTP {r.status_code} {str(data)[:200]}", flush=True)
            return []
        for a in data:
            a["_query"] = query
        return data
    except Exception as e:
        print(f"[adlib] query {query!r} failed: {e!r}"[:300], flush=True)
        return []


def _days(start, today):
    try:
        m, d, y = (int(x) for x in str(start).split("/"))
        return max(0, (today - datetime.date(y, m, d)).days)
    except Exception:
        return None


def _iso(start):
    try:
        m, d, y = (int(x) for x in str(start).split("/"))
        return datetime.date(y, m, d).isoformat()
    except Exception:
        return ""


def _domain(url):
    m = re.search(r"https?://(?:www\.)?([^/?#]+)", url or "")
    return m.group(1).lower() if m else ""


# ---------------------------------------------------------------- Claude helpers
def _claude(prompt, max_tokens=6000):
    import research   # reuse the configured Anthropic client + model (workspace header etc.)
    try:
        msg = research._client.messages.create(
            model=research.MODEL, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON, no prose, no markdown fences."}])
    except Exception as e:
        print(f"[adlib] Claude call failed: {e!r}"[:400], flush=True)
        return None
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    return salvage(text)


# ---------------------------------------------------------------- main entry
def teardown(client_name, city, region, seeds, areas=None, log=print):
    """Returns competitor_ad_intel (EMPTY-shaped with a sourcing_note on failure), or None if Apify
    isn't configured (caller then skips the section)."""
    if not TOKEN:
        return None
    today = datetime.date.today()
    areas = [a for a in (areas or []) if a][:3] or [city]
    market = f"{', '.join(areas)} ({region})"

    # Discovery queries: seed names + local keywords across the client's top areas.
    queries = [(s["name"], SEED_MAX_ADS) for s in (seeds or []) if s.get("name")][:5]
    kw = []
    for a in areas:
        kw += [f"{a} painting", f"{a} painters"]
    kw += [f"house painting {areas[0]}", f"exterior painting {areas[0]}"]
    seen_q = set()
    for q in kw:
        if q.lower() not in seen_q:
            seen_q.add(q.lower()); queries.append((q, KEYWORD_MAX_ADS))
    queries = queries[:11]
    log(f"[adlib] {len(queries)} Ad Library searches for {market}")
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda q: _scrape(*q), queries))

    ads, ids = [], set()
    for batch in results:
        for a in batch:
            key = a.get("libraryID") or (a.get("brand"), a.get("body"), a.get("startDate"))
            if key in ids:
                continue
            ids.add(key); ads.append(a)
    log(f"[adlib] {len(ads)} unique active ads scraped")
    if not ads:
        return {**EMPTY, "sourcing_note": f"Ad Library scrape returned no active ads for {market} "
                                          f"({len(queries)} searches). Scraper may be blocked — retry later."}

    # Group by advertiser (brand). Facts computed here, in code.
    by_brand = {}
    for a in ads:
        b = (a.get("brand") or "").strip()
        if b:
            by_brand.setdefault(b, []).append(a)

    # 2. FILTER — which brands are local residential painting contractors?
    listing = []
    for b, lst in by_brand.items():
        sample = (lst[0].get("body") or "")[:220].replace("\n", " ")
        doms = sorted({_domain(x.get("linkUrl") or x.get("ctaUrl")) for x in lst} - {""})[:2]
        listing.append({"brand": b, "ads": len(lst), "sites": doms, "sample_ad": sample})
    verdict = _claude(f"""These Meta advertisers came back from Ad Library searches for painting contractors in {market}.
Keep ONLY advertisers that are residential/commercial PAINTING contractors that plausibly serve {market}.
Exclude: non-painters (windows, roofing, crafts, events, retail, lending, lead-gen aggregators), painters clearly
in another city/state (franchise locations elsewhere, e.g. "CertaPro of Staten Island"), national brand pages with
no local tie, and the client itself ({client_name}).
Advertisers: {json.dumps(listing)}
Return JSON: {{"keep": ["exact brand", ...]}}""", max_tokens=2000) or {}
    keep = [b for b in (verdict.get("keep") or []) if b in by_brand and client_name.lower() not in b.lower()]
    log(f"[adlib] {len(keep)} local painting advertisers kept of {len(by_brand)} brands")
    if not keep:
        return {**EMPTY, "sourcing_note": f"Scraped {len(ads)} active ads across {len(by_brand)} advertisers for "
                                          f"{market}, but none were local painting contractors running Meta ads."}

    # 3. COUNT — per-advertiser inventory + longevity (deterministic).
    facts, long_rows = [], []
    for b in keep:
        lst = by_brand[b]
        for a in lst:
            a["_days"] = _days(a.get("startDate"), today)
        oldest = max(lst, key=lambda x: x["_days"] or -1)
        facts.append({
            "name": b,
            "facebook_page_url": f"https://www.facebook.com/{lst[0]['pageId']}" if lst[0].get("pageId") else "",
            "page_unresolved": not lst[0].get("pageId"),
            "ads_active": len(lst),
            "formats": sorted({(x.get("format") or "").lower() for x in lst} - {""}),
            "oldest_active_ad_started": _iso(oldest.get("startDate")),
            "oldest_active_ad_days": oldest["_days"] or 0,
        })
        seen_body = set()
        for a in sorted(lst, key=lambda x: -(x["_days"] or 0)):
            body = re.sub(r"\s+", " ", a.get("body") or "").strip()
            if not body or body[:80] in seen_body:
                continue
            seen_body.add(body[:80])
            long_rows.append({"advertiser": b, "start_date": _iso(a.get("startDate")),
                              "duration_days": a["_days"] or 0, "body": body[:600],
                              "headline": a.get("linkTitle") or "", "cta": a.get("ctaText") or "",
                              "format": a.get("format") or ""})
    long_rows.sort(key=lambda r: -r["duration_days"])
    long_rows = long_rows[:30]

    # 4. ANALYZE — Claude interprets the REAL copy; it may not add advertisers or ads.
    analysis = _claude(f"""You are tearing down competitor Meta ads for {client_name}, a painting contractor in {market}.
Below is REAL, currently-active ad data scraped from the Meta Ad Library. Days running is exact. Ads running
{PROVEN_DAYS}+ days are PROVEN (advertisers don't keep paying for losers). Use ONLY this data — never invent ads,
advertisers or offers. Quote hooks verbatim (the opening line of the ad copy).

ADVERTISERS (facts): {json.dumps(facts)}
DISTINCT ADS, longest-running first (index = position): {json.dumps(long_rows)}

Angle names to use where they fit:
{tax.list_for_prompt()}

Return JSON:
{{"advertisers": {{"<exact advertiser name>": {{"lead_offer": "...", "offer_framing": "discount / urgency / risk-reversal / free estimate etc.",
   "top_hooks": ["verbatim opening line", ...up to 3], "dominant_angles": ["angle key", ...1-3]}}}},
 "ads": [{{"index": 0, "offer": "the offer in this ad", "angle": "angle key", "hook": "verbatim opening line"}}, ...one per ad above],
 "offers_in_market": ["distinct offers competitors lead with"],
 "messaging_patterns": ["language/claims repeated across competitors"],
 "whitespace": {{"angles_nobody_runs": ["angle keys none of them use"],
                 "objections_nobody_addresses": ["homeowner objections absent from every ad"],
                 "obvious_differentiator_invisible_in_all_ads": "one sentence"}}}}""", max_tokens=8000) or {}

    per = analysis.get("advertisers") or {}
    advertisers = []
    for f in facts:
        p = per.get(f["name"]) or {}
        advertisers.append({**f, "lead_offer": p.get("lead_offer", ""), "offer_framing": p.get("offer_framing", ""),
                            "top_hooks": p.get("top_hooks") or [], "dominant_angles": p.get("dominant_angles") or []})
    advertisers.sort(key=lambda x: (-x["oldest_active_ad_days"], -x["ads_active"]))
    tags = {t.get("index"): t for t in (analysis.get("ads") or []) if isinstance(t, dict)}
    longevity = []
    for i, r in enumerate(long_rows):
        t = tags.get(i) or {}
        longevity.append({"advertiser": r["advertiser"], "start_date": r["start_date"],
                          "duration_days": r["duration_days"], "offer": t.get("offer", ""),
                          "hook": t.get("hook") or r["body"].split(". ")[0][:200], "angle": t.get("angle", "")})
    ws = analysis.get("whitespace") or {}
    proven = sum(1 for r in long_rows if r["duration_days"] >= PROVEN_DAYS)
    return {
        "advertisers": advertisers,
        "ad_longevity_table": longevity,
        "offers_in_market": analysis.get("offers_in_market") or [],
        "messaging_patterns": analysis.get("messaging_patterns") or [],
        "whitespace": {"angles_nobody_runs": ws.get("angles_nobody_runs") or [],
                       "objections_nobody_addresses": ws.get("objections_nobody_addresses") or [],
                       "obvious_differentiator_invisible_in_all_ads": ws.get("obvious_differentiator_invisible_in_all_ads") or ""},
        "sourcing_note": (f"Meta Ad Library, active US ads as of {today.isoformat()}: {len(queries)} searches "
                          f"({', '.join(q for q, _ in queries)}). {len(ads)} ads scraped; {len(keep)} local painting "
                          f"advertisers kept; {proven} distinct ads running {PROVEN_DAYS}+ days (proven). "
                          f"Ad counts and run lengths are exact from the Library; offers/angles/whitespace are AI analysis "
                          f"of the real copy." + ("" if analysis else " (AI analysis step failed — facts only.)")),
    }
