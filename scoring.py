"""Deterministic ICP-Match scoring per zip — Census ACS (via keyless Census Reporter) + centroids.
No LLM here: pure data + math, so it's cheap, fast, and reproducible.

ICP-Match (0-100): income fit 25, home value 20, owner-occ 15, education 15, married 10, detached 10, age 5.
Buckets: FUND 80-100 (Broad), TEST 60-79 (Expansion), EXCLUDE <60.
High-Quality = top ~25% of FUND by income -> price -> home-age (handled in pipeline).
"""
import os, json, time, httpx

# IRS SOI per-zip wealth/income lookup (preprocessed from 22zpallagi.csv via soi_preprocess.py).
# Gives DOLLAR magnitudes of investment + retirement income per return — a far stronger net-worth
# signal than ACS's binary "has investment income" flag. Falls back to ACS-only if a zip is absent.
try:
    with open(os.path.join(os.path.dirname(__file__), "soi_zip.json")) as _f:
        SOI = json.load(_f)
except Exception:
    SOI = {}

# Zillow Home Value Index (ZHVI) per zip — CURRENT market home values (preprocessed via
# zillow_preprocess.py). More current than Census's lagged self-reported values. Census B25077
# is the fallback when a zip isn't in Zillow.
try:
    with open(os.path.join(os.path.dirname(__file__), "zillow_zhvi.json")) as _f:
        ZILLOW = json.load(_f)
except Exception:
    ZILLOW = {}

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
CR = "https://api.censusreporter.org/1.0/data/show/latest"
# B19054 = households with interest/dividend/net-rental income (net-worth proxy: asset-holders, incl. retirees)
TABLES = "B25077,B25003,B25024,B19013,B19001,B19054,B15003,B11001,B01001"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def fetch_census(zip_code: str, retries: int = 4):
    """Return the estimate dict for a ZCTA, or None. Census Reporter is keyless (the official
    api.census.gov now requires a key); S-tables are rejected here, so we use B-tables.
    Retries transient failures — Census Reporter rate-limits rapid bursts, which was silently
    dropping zips during multi-zip runs."""
    geo = f"86000US{zip_code}"
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=30, headers=UA) as c:
                r = c.get(CR, params={"table_ids": TABLES, "geo_ids": geo})
            if r.status_code == 200:
                est = r.json().get("data", {}).get(geo, {})
                out = {}
                for t, blob in est.items():
                    out.update(blob.get("estimate", {}))
                if out:
                    return out
        except Exception:
            pass
        time.sleep(0.7 * (attempt + 1))   # backoff before retry
    return None


def fetch_centroid(zip_code: str):
    """Return (lat, lng, area_name). area_name from the zip's place name (city/neighborhood)."""
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(f"https://api.zippopotam.us/us/{zip_code}")
            if r.status_code != 200:
                return None, None, ""
            p = r.json()["places"][0]
            return round(float(p["latitude"]), 4), round(float(p["longitude"]), 4), p.get("place name", "")
    except Exception:
        return None, None, ""


def _pct(d, num_codes, den_code):
    den = d.get(den_code) or 0
    if not den:
        return 0.0
    num = sum(d.get(k) or 0 for k in num_codes)
    return round(100 * num / den, 1)


def pct_income_ge(d, threshold):
    """% of households at/above the income threshold (B19001 brackets)."""
    BR = {150000: ["B19001016", "B19001017"],
          100000: ["B19001014", "B19001015", "B19001016", "B19001017"],
          75000:  ["B19001013", "B19001014", "B19001015", "B19001016", "B19001017"]}
    return _pct(d, BR.get(threshold, BR[150000]), "B19001001")


def fetch_zip(zip_code: str):
    """Fetch census + centroid once (so the pipeline can pick a market-aware threshold before scoring)."""
    d = fetch_census(zip_code)
    if not d:
        return None
    lat, lng, area = fetch_centroid(zip_code)
    return {"zip": zip_code, "raw": d, "lat": lat, "lng": lng, "area": area,
            "value": ZILLOW.get(zip_code) or (d.get("B25077001") or 0)}  # Zillow current value, Census fallback


def compute_row(fetched: dict, income_threshold: int):
    """Score a pre-fetched zip. income_threshold e.g. 175000 (premium) or 100000 (floor)."""
    d = fetched["raw"]; lat = fetched["lat"]; lng = fetched["lng"]
    zip_code = fetched["zip"]; area = fetched["area"]
    value = ZILLOW.get(zip_code) or d.get("B25077001") or 0          # Zillow current home value (Census fallback)
    pct_own = _pct(d, ["B25003002"], "B25003001")                    # owner-occupied
    pct_det = _pct(d, ["B25024002"], "B25024001")                    # 1-unit detached
    income = d.get("B19013001") or 0                                 # median HH income
    pct_inc = pct_income_ge(d, income_threshold)                     # % HH at/above income threshold
    pct_invest = _pct(d, ["B19054002"], "B19054001")                 # % HH w/ investment income (net-worth proxy)
    pct_bach = _pct(d, ["B15003022", "B15003023", "B15003024", "B15003025"], "B15003001")
    pct_marr = _pct(d, ["B11001003"], "B11001001")                   # married-couple households
    # ages 35-74: male 013-022, female 037-046
    male = [f"B01001{n:03d}" for n in range(13, 23)]
    female = [f"B01001{n:03d}" for n in range(37, 47)]
    age_share = _pct(d, male + female, "B01001001")

    val_target = 600000 if income_threshold >= 150000 else 400000
    soi = SOI.get(zip_code)
    if soi:
        # Income capacity: ACS %>=threshold blended with IRS %returns>=$200k AGI (sharper on high earners).
        income_fit = _clamp(0.5 * _clamp(pct_inc / 30, 0, 1) + 0.5 * _clamp(soi["p200k"] / 25, 0, 1), 0, 1)
        # Net-worth capacity: home equity + IRS investment-income $ + retirement-distribution $ per return.
        networth_fit = (0.4 * _clamp(value / val_target, 0, 1)
                        + 0.4 * _clamp(soi["inv"] / 25000, 0, 1)
                        + 0.2 * _clamp(soi["ret"] / 25000, 0, 1))
    else:  # ACS-only fallback (zip absent from SOI)
        income_fit = _clamp(pct_inc / 30, 0, 1)
        networth_fit = 0.6 * _clamp(value / val_target, 0, 1) + 0.4 * _clamp(pct_invest / 50, 0, 1)
    # Financial capacity (45): the GREATER of income OR net worth — a household qualifies whether they
    # have a high paycheck OR accumulated wealth. Neither the young high-earner nor the wealthy retiree
    # is penalized for being strong in only one.
    s_capacity = 45 * max(income_fit, networth_fit)
    s_own = 15 if pct_own >= 90 else _clamp(15 * (pct_own - 50) / 40, 0, 15)
    s_edu = 15 if pct_bach >= 60 else _clamp(15 * (pct_bach - 25) / 35, 0, 15)
    s_marr = 10 if pct_marr >= 65 else _clamp(10 * (pct_marr - 35) / 30, 0, 10)
    s_det = 10 if pct_det >= 80 else _clamp(10 * (pct_det - 40) / 40, 0, 10)
    s_age = 5 if age_share >= 50 else _clamp(5 * (age_share - 35) / 20, 0, 5)
    icp = round(s_capacity + s_own + s_edu + s_marr + s_det + s_age, 1)

    return {
        "zip": zip_code, "area": area,
        "pct_households_income": round(pct_inc, 1), "median_home_value": int(value),
        "pct_owner_occupied": pct_own, "pct_bachelors": pct_bach, "pct_married": pct_marr,
        "pct_detached": pct_det, "age_35_75_share": age_share, "icp_match_score": icp,
        "lat": lat, "lng": lng,
        # internal-only (not written to Base44): for HQ ranking + housing-age note
        "_median_income": int(income),
    }


def score_zip(zip_code: str, area: str, income_threshold: int):
    """Convenience: fetch + score one zip (used by standalone tests)."""
    f = fetch_zip(zip_code)
    return compute_row(f, income_threshold) if f else None
