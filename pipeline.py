"""Orchestrates the full Research Intelligence run for one client, then publishes to Base44.
Mirrors the painter-market-research skill phases. Called by main.py (the /run endpoint)."""
import os, re, math
import base44, scoring, research, competition, manus, angle

# --- v2 rollout gate (Identity Analysis, competitor ad teardown, angle engine) ---
# During testing these features run ONLY for the demo client so live clients are untouched (no extra
# cost/latency, nothing written). At rollout set env V2_ALL=1 to enable for every client.
_DEMO_IDS = {"6a2c4c51ef3be74f3fb1c8f0"}   # Demo Client
_V2_ALL = os.environ.get("V2_ALL") == "1"


def _demo_feature(client_id):
    return _V2_ALL or client_id in _DEMO_IDS


def _competitor_seeds(comps, city, region):
    """Seed competitors (from research.competitors) that Manus confirms + EXPANDS on in the Ad Library.
    Just starting points — Manus discovers >=10 local advertisers in the market on top of these. The
    client is NOT included; the teardown is about competitors only."""
    return [{"name": c.get("name", ""),
             "facebook_page_url": (c.get("facebook_page_url") or "").strip(),
             "website": (c.get("website") or "").strip(), "city": city, "region": region}
            for c in (comps or [])]


def _zips(raw: str):
    # Match 5-digit zips AND 4-digit ones — New England zips (e.g. 02492) often lose their leading
    # zero when stored as numbers ("2492"), so zero-pad short matches back to 5 digits.
    seen, out = set(), []
    for m in re.findall(r"\b\d{4,5}\b", raw or ""):
        z = m.zfill(5)
        if z not in seen:
            seen.add(z); out.append(z)
    return out


def _derive_threshold(values, avg_job=None):
    """Market-driven HHI threshold (per Ricky: market-adaptive, $100K floor). $150K in premium
    markets (60th-percentile home value >= $450K), else the $100K floor. Job size does NOT force
    premium — a modest market like Baton Rouge stays at $100K even for a higher-ticket painter."""
    vals = sorted(v for v in values if v)
    p60 = vals[min(len(vals) - 1, int(len(vals) * 0.6))] if vals else 0
    return 150000 if p60 >= 450000 else 100000


def _city_region(addr: str, sample_area: str):
    addr = addr or ""
    state = (re.search(r"\b([A-Z]{2})\b", addr) or [None, ""])[1]
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    city = parts[-2] if len(parts) >= 2 else (sample_area or "the area")
    city = re.sub(r"\d", "", city).strip() or (sample_area or "the area")
    return city, (state or "US")


def _targeting(income_threshold):
    inc = f"${income_threshold:,}+"
    return (
        {"geo": "All FUND zips", "age": "35-75", "gender": "All",
         "income_approach": f"{inc} via zip selection (no Meta income overlay)",
         "interests": "None - let CAPI optimize"},
        {"geo": "Top ~25% premium zips", "age": "43-58 (core 45-54)", "gender": "All",
         "income_approach": "Meta household income top 5-10% of ZIP overlay",
         "interests": "Homeowner + home improvement / interior design / Houzz / HGTV-Fixer Upper / Home Depot-Lowe's / Zillow-Realtor.com / luxury real estate + design influencers (test vs no-interest)"},
    )


def _rationale(income_threshold, n_broad, n_hq, n_exp):
    inc = f"${income_threshold:,}+"
    return (
        f"Why these zips: every zip in the serviceable area was scored 0-100 on how well its households match our Ideal Client Profile - not chosen by gut. "
        f"The biggest factor is financial capacity - the GREATER of household income ({inc}) OR net worth (home equity + IRS investment & retirement income per return). A household qualifies whether they have a high paycheck OR accumulated wealth, so neither a high-earning young family nor a wealthy retiree on a fixed income is penalized. "
        f"It also weighs homeownership, college education, married-couple families, detached single-family homes, and ages 35-75.\n\n"
        f"BROAD - the {n_broad} best-matched zips (your core audience): the strongest fit for the ideal client in YOUR market - affluent-for-the-area, high-owner-occupancy, detached-home neighborhoods. (In a premium metro these all clear an absolute bar; in a more modest market they're the top of your own market.)\n\n"
        f"HIGH-QUALITY - the top {n_hq} of those by income, home price, and ideal home age (built ~1985-2010: old enough to need repainting, new enough to skip lead paint). Tighter age band (43-58) + intent layering for the lowest cost per estimate.\n\n"
        f"EXPANSION - the next-best {n_exp} zips, ranked. Add these in score order when you want more reach - broaden by adding the best remaining zips first, not by loosening targeting (which protects cost per estimate).\n\n"
        f"The lowest-scoring zips (renter-heavy, apartment-dense, or lower income/net-worth areas) were excluded - they'd attract the wrong homeowner and waste spend."
    )


def _strip_internal(z):
    return {k: v for k, v in z.items() if not k.startswith("_")}


def _bucket(scored):
    """Percentile tiers + quality guardrails (per Ricky):
      Broad     = top 50% of the client's zips, PLUS any zip scoring >=80 (never drop a clearly-great
                  zip), MINUS any zip <50 (never fund a genuinely weak one).
      Expansion = the 50-75% band, >=50, not already in Broad.
      (Excluded = everything else — computed by the caller and stored as excluded_zips.)"""
    s = sorted(scored, key=lambda x: -x["icp_match_score"])
    n = len(s)
    if not n:
        return [], []
    half = max(1, round(n * 0.50))
    three_q = max(half, round(n * 0.75))
    broad = list(s[:half])
    bset = {z["zip"] for z in broad}
    for z in s:                                                # guardrail: always keep any zip >= 80
        if z["icp_match_score"] >= 80 and z["zip"] not in bset:
            broad.append(z); bset.add(z["zip"])
    broad = [z for z in broad if z["icp_match_score"] >= 50]   # guardrail: never fund a zip < 50
    bset = {z["zip"] for z in broad}
    expansion = [z for z in s[:three_q] if z["zip"] not in bset and z["icp_match_score"] >= 50]
    return broad, expansion


def run(client_id: str, log=print):
    base44.set_status(client_id, "Running", "Scoring zips...")
    cl = base44.get_client_record(client_id)
    name = cl.get("name", "Client")
    services = cl.get("services_offered") or "Interior, Exterior, Cabinet painting"
    # Union of broad + high-quality zip fields — clients sometimes fill only one of them.
    zips = _zips(f"{cl.get('service_areas') or ''} {cl.get('highest_quality_service_areas') or ''}")
    if not zips:
        base44.set_status(client_id, "Error", "No service-area zips found on Company Info (checked both the broad and high-quality fields).")
        return {"error": "no zips"}

    # Phase 2: fetch all zips once, pick a market-aware income threshold, then score (deterministic)
    fetched = []
    for z in zips:
        f = scoring.fetch_zip(z)
        if f:
            fetched.append(f)
        log(f"fetched {z}: {'ok' if f else 'no data'}")
    income_threshold = _derive_threshold([f["value"] for f in fetched], cl.get("average_job_size"))
    log(f"income threshold -> ${income_threshold:,}")
    scored = [scoring.compute_row(f, income_threshold) for f in fetched]
    for s in scored:
        log(f"scored {s['zip']} ({s['area']}): {s['icp_match_score']}")

    fund, expansion = _bucket(scored)
    # Everything else that was scored (below the expansion floor) — kept VISIBLE so the media buyer
    # can include any zip the client knows performs, even if its ICP fit is lower.
    _picked = {z["zip"] for z in fund} | {z["zip"] for z in expansion}
    excluded = [z for z in sorted(scored, key=lambda x: -x["icp_match_score"]) if z["zip"] not in _picked]
    # High-Quality = top ~25% of the client's zips by ICP score (a subset of Broad)
    n_hq = max(4, round(len(scored) * 0.25)) if fund else 0
    hq = sorted(fund, key=lambda x: -x["icp_match_score"])[:n_hq]

    city, region = _city_region(cl.get("business_address"), "")
    base44.set_status(client_id, "Running", "Researching competitors, reviews, homeowner concerns...")
    # Each web-research section is best-effort: a model/parse/network hiccup in any ONE of them must
    # never sink the run — the deterministic zip scoring above is the core deliverable. _ask_json
    # already returns None on bad JSON; this guard also absorbs any unforeseen raise (a section just
    # comes back empty and the report still ships).
    def _safe(fn, default, label):
        try:
            return fn()
        except Exception as e:
            log(f"{label} failed (non-fatal): {str(e)[:200]}")
            return default
    comps = _safe(lambda: research.competitors(name, city, region, services, [z["zip"] for z in fund] or zips), [], "competitors")
    comps_complaints = _safe(lambda: research.complaints(city, region), [], "complaints")
    homeowner_concerns = _safe(lambda: research.concerns(city, region), [], "concerns")

    # Identity Analysis (Mission 1): how the client's own company describes itself vs. how the market
    # perceives it. Reads the client's Company Info (website/GBP/social + intake) for the self side and
    # web-searches reviews/forums for the market side. Flags thin input rather than fabricating.
    # Gated to the demo client during testing (see _demo_feature).
    identity_analysis = None
    if _demo_feature(client_id):
        base44.set_status(client_id, "Running", "Analyzing brand identity & market perception...")
        identity_analysis = _safe(lambda: research.identity(name, city, region, cl.get("website"), services, cl), None, "identity")

    broad_t, hq_t = _targeting(income_threshold)
    centroids = [(z["lat"], z["lng"]) for z in (fund or scored) if z["lat"] and z["lng"]]
    mlat = round(sum(a for a, _ in centroids) / len(centroids), 4) if centroids else None
    mlng = round(sum(b for _, b in centroids) / len(centroids), 4) if centroids else None

    # Local competition density: established painting competitors (>=50 Google reviews) within 25 mi
    # of the market center, with map pins. A market-saturation proxy (not a Meta-auction read). Skipped
    # gracefully if GOOGLE_PLACES_API_KEY is unset. Ad spend stays in-house — only the count/map ship.
    base44.set_status(client_id, "Running", "Mapping local competition...")
    comp = competition.competition_profile(mlat, mlng)
    log(f"competition: {comp['established_count']} established / {comp['total_found']} found ({comp['density_tier'] or 'n/a'})")

    # Addressable homeowner households (ages 35-75) per tier — real Census counts summed across the
    # tier's zips (one paint job per household, not per person).
    _hh = lambda zs: sum(z.get("households_35_75", 0) for z in zs)

    payload = {
        "income_threshold": f"${income_threshold:,}",
        "metro_center_lat": mlat, "metro_center_lng": mlng,
        "broad_zips": [_strip_internal(z) for z in fund],
        "hq_zips": [_strip_internal(z) for z in hq],
        "expansion_zips": [_strip_internal(z) for z in expansion],
        "excluded_zips": [_strip_internal(z) for z in excluded],
        "broad_households": _hh(fund), "hq_households": _hh(hq),
        "expansion_households": _hh(expansion), "excluded_households": _hh(excluded),
        "broad_targeting": broad_t, "hq_targeting": hq_t,
        "audience_rationale": _rationale(income_threshold, len(fund), len(hq), len(expansion)),
        "top_concerns": homeowner_concerns,
        "top_complaints": comps_complaints,
        "competitors": comps,
        "competition": comp,
        "sources": "Demographics: Census ACS 5-yr via Census Reporter (B19001/B15003/B11001/B01001/B25077/B25003/B25024). "
                   "Homeowner households (ages 35-75): Census B25007 (owner-occupied by age of householder). "
                   "Reviews & USPs: competitor websites + Google/Yelp/BBB. Concerns: Reddit/forums via web search (flag where thin). "
                   "Competitor Meta ads = MANUAL CHECK. Household counts = Census owner-occupied, ages 35-75; pull reachable size from Meta Ads Manager. "
                   "Local competition: Google Places API (painting contractors within 25 mi of the market center; 'established' = >=50 Google reviews).",
        "status": "Done", "status_note": f"{len(fund)} FUND / {len(expansion)} expansion / {len(scored)-len(fund)-len(expansion)} excluded",
    }
    # Only write identity_analysis when it was computed (demo gate) — never sends the key for live
    # clients, so it can't fail an upsert even before the Base44 field exists globally.
    if identity_analysis is not None:
        payload["identity_analysis"] = identity_analysis

    # Two-stage publish: the Manus ad teardown takes minutes, so publish the core report NOW (status
    # stays Running with a note), then run Phase 2/3 and patch them in. Live clients skip all of this.
    v2 = _demo_feature(client_id)
    if v2:
        payload["status"] = "Running"
        payload["status_note"] = "Core report ready - tearing down competitor ads + building angle strategy (~5 min)..."
    base44.upsert_research(client_id, payload)
    log(f"published core {name}: FUND={len(fund)} HQ={len(hq)} expansion={len(expansion)} competitors={len(comps)}")

    if v2:
        # Phase 2: competitor ad teardown via Manus (Meta Ad Library) — hands Manus the resolved pages.
        base44.set_status(client_id, "Running", "Tearing down competitor ads in the Meta Ad Library (Manus)...")
        competitor_ad_intel = manus.teardown(name, city, region, _competitor_seeds(comps, city, region), log=log)
        # PERSIST the teardown immediately — a later step's failure must not throw away this 15-min result.
        if competitor_ad_intel is not None:
            try:
                base44.update_research_fields(client_id, {"competitor_ad_intel": competitor_ad_intel})
            except Exception as e:
                log(f"competitor_ad_intel write failed: {e}")

        # Phase 3: Meta Ad Strategy — prioritized, build-ready ad concepts. Wrapped so a generation/parse
        # hiccup is NON-FATAL: identity + competitor_ad_intel already persisted; the run still finishes Done.
        base44.set_status(client_id, "Running", "Building Meta Ad Strategy from our conversion data...")
        meta_ad_strategy = gtm = None
        try:
            meta_ad_strategy = angle.strategy(name, city, region, services, homeowner_concerns,
                                              comps_complaints, competitor_ad_intel, identity_analysis)
            # go_to_market is written as its OWN top-level field (Base44 drops sub-keys not in a defined schema).
            gtm = meta_ad_strategy.pop("go_to_market", None) if isinstance(meta_ad_strategy, dict) else None
        except Exception as e:
            log(f"meta_ad_strategy step failed (non-fatal): {str(e)[:200]}")

        base44.set_status(client_id, "Running", "Finalizing...")
        note = f"{len(fund)} FUND / {len(expansion)} expansion / {len(scored)-len(fund)-len(expansion)} excluded"
        if not meta_ad_strategy:
            note += " — ad strategy step skipped (re-run to retry)"
        updates = {"status": "Done", "status_note": note}
        if meta_ad_strategy is not None:
            updates["meta_ad_strategy"] = meta_ad_strategy
        if gtm is not None:
            updates["go_to_market"] = gtm
        base44.update_research_fields(client_id, updates)
        log(f"published v2 {name}: competitor_ad_intel={'yes' if competitor_ad_intel else 'no'} "
            f"meta_ad_strategy concepts={len((meta_ad_strategy or {}).get('ad_concepts', []))} "
            f"go_to_market={'yes' if gtm else 'no'}")

    return {"ok": True, "fund": len(fund), "hq": len(hq), "expansion": len(expansion)}
