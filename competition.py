"""Local competition density via the Google Places API (New) Text Search.
Counts ESTABLISHED painting competitors within a radius of the market center and returns their
map pins (name / lat / lng / rating / reviews) so the portal can plot them. No LLM — API + math.

'Established' = >=50 Google reviews (HOM's bar for a real, marketing-active competitor). The density
tier is derived from that count. This is a market-SATURATION proxy, NOT a Meta-auction read — many
strong Google competitors don't run Meta ads — so treat it as "how crowded / hard to stand out is this
market." Ad spend is set in-house (guarantee floor $4,000/mo) and is NOT published to the portal.

Keyless-free: needs GOOGLE_PLACES_API_KEY. If unset, competition_profile() returns an empty block with
a note and the rest of the pipeline still runs.
"""
import os, math, time, httpx

PLACES_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ("places.id,places.displayName,places.rating,"
              "places.userRatingCount,places.location,nextPageToken")
RADIUS_MI = 25
ESTABLISHED_MIN_REVIEWS = 50     # a real, review-backed competitor (HOM's bar)
STRONG_MIN_REVIEWS = 100         # heavyweight competitor
STRONG_MIN_RATING = 4.5
MAX_PAGES = 3                    # Places returns up to 20/page -> 60 results max


def _haversine_mi(lat1, lng1, lat2, lng2):
    R = 3958.8  # earth radius, miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _search_pages(lat, lng, radius_m):
    """Up to MAX_PAGES pages of 'painting contractor' near (lat,lng). locationBias biases toward the
    circle; we still hard-filter by distance below since bias is not a hard cap."""
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": PLACES_KEY,
               "X-Goog-FieldMask": FIELD_MASK}
    body = {"textQuery": "painting contractor",
            "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng},
                                        "radius": radius_m}},
            "pageSize": 20}
    places, token = [], None
    for _ in range(MAX_PAGES):
        b = dict(body)
        if token:
            b["pageToken"] = token
        try:
            with httpx.Client(timeout=25) as c:
                r = c.post(SEARCH_URL, headers=headers, json=b)
            if r.status_code != 200:
                break
            j = r.json()
        except Exception:
            break
        places.extend(j.get("places", []))
        token = j.get("nextPageToken")
        if not token:
            break
        time.sleep(2)   # the page token needs a moment to become valid
    return places


def nearby_painters(lat, lng, radius_mi=RADIUS_MI):
    """Return [{name, lat, lng, rating, reviews, distance_mi}] within radius_mi of the center."""
    if not PLACES_KEY or lat is None or lng is None:
        return []
    radius_m = int(radius_mi * 1609.34)
    seen, out = set(), []
    for p in _search_pages(lat, lng, radius_m):
        loc = p.get("location") or {}
        plat, plng = loc.get("latitude"), loc.get("longitude")
        pid = p.get("id")
        if plat is None or plng is None or pid in seen:
            continue
        dist = _haversine_mi(lat, lng, plat, plng)
        if dist > radius_mi:                 # locationBias only biases — enforce a hard radius
            continue
        seen.add(pid)
        out.append({
            "name": (p.get("displayName") or {}).get("text", ""),
            "lat": round(plat, 5), "lng": round(plng, 5),
            "rating": float(p.get("rating") or 0),
            "reviews": int(p.get("userRatingCount") or 0),
            "distance_mi": round(dist, 1),
        })
    return out


def _tier(n_established):
    """Density tier from the count of established (>=50-review) competitors. Heuristic — tune against
    RevenuePRO's actual CPL-by-market once enough markets are mapped."""
    if n_established <= 5:
        return "Low"
    if n_established <= 15:
        return "Medium"
    if n_established <= 30:
        return "High"
    return "Saturated"


def competition_profile(lat, lng, radius_mi=RADIUS_MI):
    """Return the `competition` block for the Research Intelligence payload."""
    if not PLACES_KEY:
        return {"radius_mi": radius_mi, "established_count": 0, "strong_count": 0,
                "total_found": 0, "density_tier": "", "competitor_pins": [],
                "note": "GOOGLE_PLACES_API_KEY not set - competition not measured."}
    pins = nearby_painters(lat, lng, radius_mi)
    established = [p for p in pins if p["reviews"] >= ESTABLISHED_MIN_REVIEWS]
    strong = [p for p in pins if p["reviews"] >= STRONG_MIN_REVIEWS and p["rating"] >= STRONG_MIN_RATING]
    capped = len(pins) >= 60
    return {
        "radius_mi": radius_mi,
        "established_count": len(established),     # >=50 reviews
        "strong_count": len(strong),              # >=100 reviews & >=4.5 stars
        "total_found": len(pins),
        "density_tier": _tier(len(established)),
        "competitor_pins": sorted(pins, key=lambda x: -x["reviews"]),   # for the map, busiest first
        "note": ("Hit Google's 60-place cap - the true count is at least this high; treat as Saturated."
                 if capped else ""),
    }
