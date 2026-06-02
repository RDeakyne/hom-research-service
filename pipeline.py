"""Orchestrates the full Research Intelligence run for one client, then publishes to Base44.
Mirrors the painter-market-research skill phases. Called by main.py (the /run endpoint)."""
import re, math
import base44, scoring, research


def _zips(raw: str):
    seen, out = set(), []
    for z in re.findall(r"\b\d{5}\b", raw or ""):
        if z not in seen:
            seen.add(z); out.append(z)
    return out


def _job_threshold(avg_job):
    try:
        v = float(re.sub(r"[^\d.]", "", str(avg_job)) or 0)
    except Exception:
        v = 0
    return 150000 if v >= 5000 else 0  # else let market + $100k floor decide


def _derive_threshold(values, avg_job):
    """Market-aware: premium ($150k) if job size is high OR the market's home values are high.
    Never below the $100k ICP floor (portfolio-wide minimum)."""
    vals = sorted(v for v in values if v)
    p60 = vals[min(len(vals) - 1, int(len(vals) * 0.6))] if vals else 0
    mkt = 150000 if p60 >= 650000 else 100000
    return max(_job_threshold(avg_job), mkt, 100000)


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
    """FUND + expansion buckets. Absolute (>=80) for premium markets; but when a modest market has
    fewer than 5 zips clearing 80, FUND becomes the client's BEST cluster (top of their own market,
    within 18 pts of the top zip and >= 55) so every client gets a non-empty Broad audience to target.
    Expansion = the next tier worth testing (>= 50, not already FUND)."""
    s = sorted(scored, key=lambda x: -x["icp_match_score"])
    fund = [z for z in s if z["icp_match_score"] >= 80]
    if len(fund) < 5 and s:
        cut = max(55, s[0]["icp_match_score"] - 18)
        fund = [z for z in s if z["icp_match_score"] >= cut]
    fz = {z["zip"] for z in fund}
    expansion = [z for z in s if z["zip"] not in fz and z["icp_match_score"] >= 50]
    return fund, expansion


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
    # High-Quality = top ~25% of FUND by income -> price
    hq_rank = sorted(fund, key=lambda x: (-x["pct_households_income"], -x["median_home_value"]))
    n_hq = max(4, math.ceil(len(fund) * 0.25)) if fund else 0
    hq = hq_rank[:n_hq]

    city, region = _city_region(cl.get("business_address"), "")
    base44.set_status(client_id, "Running", "Researching competitors, reviews, homeowner concerns...")
    comps = research.competitors(name, city, region, services, [z["zip"] for z in fund] or zips)
    comps_complaints = research.complaints(city, region)
    homeowner_concerns = research.concerns(city, region)

    broad_t, hq_t = _targeting(income_threshold)
    centroids = [(z["lat"], z["lng"]) for z in (fund or scored) if z["lat"] and z["lng"]]
    mlat = round(sum(a for a, _ in centroids) / len(centroids), 4) if centroids else None
    mlng = round(sum(b for _, b in centroids) / len(centroids), 4) if centroids else None

    payload = {
        "income_threshold": f"${income_threshold:,}",
        "metro_center_lat": mlat, "metro_center_lng": mlng,
        "broad_zips": [_strip_internal(z) for z in fund],
        "hq_zips": [_strip_internal(z) for z in hq],
        "expansion_zips": [_strip_internal(z) for z in expansion],
        "broad_targeting": broad_t, "hq_targeting": hq_t,
        "audience_rationale": _rationale(income_threshold, len(fund), len(hq), len(expansion)),
        "top_concerns": homeowner_concerns,
        "top_complaints": comps_complaints,
        "competitors": comps,
        "sources": "Demographics: Census ACS 5-yr via Census Reporter (B19001/B15003/B11001/B01001/B25077/B25003/B25024). "
                   "Reviews & USPs: competitor websites + Google/Yelp/BBB. Concerns: Reddit/forums via web search (flag where thin). "
                   "Competitor Meta ads = MANUAL CHECK. Audience counts = Census residents 35-74; pull reachable size from Meta Ads Manager.",
        "status": "Done", "status_note": f"{len(fund)} FUND / {len(expansion)} expansion / {len(scored)-len(fund)-len(expansion)} excluded",
    }
    base44.upsert_research(client_id, payload)
    log(f"published {name}: FUND={len(fund)} HQ={len(hq)} expansion={len(expansion)} competitors={len(comps)}")
    return {"ok": True, "fund": len(fund), "hq": len(hq), "expansion": len(expansion)}
