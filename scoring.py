"""Deterministic ICP-Match scoring per zip — Census ACS (via keyless Census Reporter) + centroids.
No LLM here: pure data + math, so it's cheap, fast, and reproducible.

ICP-Match (0-100): income fit 25, home value 20, owner-occ 15, education 15, married 10, detached 10, age 5.
Buckets: FUND 80-100 (Broad), TEST 60-79 (Expansion), EXCLUDE <60.
High-Quality = top ~25% of FUND by income -> price -> home-age (handled in pipeline).
"""
import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
CR = "https://api.censusreporter.org/1.0/data/show/latest"
TABLES = "B25077,B25003,B25024,B19013,B19001,B15003,B11001,B01001"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def fetch_census(zip_code: str):
    """Return the estimate dict for a ZCTA, or None. Census Reporter is keyless; the
    official api.census.gov now requires a key. S-tables are rejected here, so we use B-tables."""
    geo = f"86000US{zip_code}"
    try:
        with httpx.Client(timeout=30, headers=UA) as c:
            r = c.get(CR, params={"table_ids": TABLES, "geo_ids": geo})
            if r.status_code != 200:
                return None
            est = r.json().get("data", {}).get(geo, {})
            out = {}
            for t, blob in est.items():
                out.update(blob.get("estimate", {}))
            return out or None
    except Exception:
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


def fetch_zip(zip_code: str):
    """Fetch census + centroid once (so the pipeline can pick a market-aware threshold before scoring)."""
    d = fetch_census(zip_code)
    if not d:
        return None
    lat, lng, area = fetch_centroid(zip_code)
    return {"zip": zip_code, "raw": d, "lat": lat, "lng": lng, "area": area,
            "value": d.get("B25077001") or 0}


def compute_row(fetched: dict, income_threshold: int):
    """Score a pre-fetched zip. income_threshold e.g. 175000 (premium) or 100000 (floor)."""
    d = fetched["raw"]; lat = fetched["lat"]; lng = fetched["lng"]
    zip_code = fetched["zip"]; area = fetched["area"]
    value = d.get("B25077001") or 0                                  # median home value
    pct_own = _pct(d, ["B25003002"], "B25003001")                    # owner-occupied
    pct_det = _pct(d, ["B25024002"], "B25024001")                    # 1-unit detached
    income = d.get("B19013001") or 0                                 # median HH income
    # income brackets: 016=$150-199,999, 017=$200k+  -> approx >= $175k
    pct_175 = _pct(d, ["B19001017"], "B19001001") + 0.5 * _pct(d, ["B19001016"], "B19001001")
    pct_175 = round(pct_175, 1)
    pct_bach = _pct(d, ["B15003022", "B15003023", "B15003024", "B15003025"], "B15003001")
    pct_marr = _pct(d, ["B11001003"], "B11001001")                   # married-couple households
    # ages 35-74: male 013-022, female 037-046
    male = [f"B01001{n:03d}" for n in range(13, 23)]
    female = [f"B01001{n:03d}" for n in range(37, 47)]
    age_share = _pct(d, male + female, "B01001001")

    # thresholds scale with income_threshold tier
    val_floor = 500000 if income_threshold >= 175000 else (300000 if income_threshold >= 100000 else 200000)
    inc_full = 30 if income_threshold >= 175000 else 20             # % of HH at threshold for full marks

    s_income = 25 * _clamp(pct_175 / inc_full, 0, 1) if income_threshold >= 175000 else \
        25 * _clamp(_pct(d, ["B19001014", "B19001015", "B19001016", "B19001017"], "B19001001") / 40, 0, 1)
    s_value = 20 if value >= val_floor else _clamp(20 * (value - val_floor / 2) / (val_floor / 2), 0, 20)
    s_own = 15 if pct_own >= 90 else _clamp(15 * (pct_own - 50) / 40, 0, 15)
    s_edu = 15 if pct_bach >= 60 else _clamp(15 * (pct_bach - 25) / 35, 0, 15)
    s_marr = 10 if pct_marr >= 65 else _clamp(10 * (pct_marr - 35) / 30, 0, 10)
    s_det = 10 if pct_det >= 80 else _clamp(10 * (pct_det - 40) / 40, 0, 10)
    s_age = 5 if age_share >= 50 else _clamp(5 * (age_share - 35) / 20, 0, 5)
    icp = round(s_income + s_value + s_own + s_edu + s_marr + s_det + s_age, 1)

    return {
        "zip": zip_code, "area": area,
        "pct_households_income": pct_175, "median_home_value": int(value),
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
