"""Build the Angle Performance Index — portfolio-wide ad performance by ANGLE, from Revenue Pro Mongo.

This is the "our proof" half of the Ad Angle Intelligence Engine: what actually converts for us,
measured by cost per estimate set (CPES) and estimate-set rate, grouped by our canonical ad angle.

Method (per CLAUDE.md: judge by cost per estimate set, not CPL):
  1. Aggregate spend + Meta leads per (companyId, adName) from `fbweeklyanalytics` over a trailing window.
  2. Count estimate sets per (companyId, adName) from `leads` (status reached estimate_set or beyond).
  3. Keep ads with meaningful spend, classify each ad's copy into the angle taxonomy (cheap LLM pass).
  4. Aggregate by angle -> spend, leads, sets, CPES, set_rate, n_ads. Rank by CPES (lower = better).

Output: angle_index.json, shipped with the service (like soi_zip.json / zillow_zhvi.json) and read at
runtime by angle.py. Mongo is touched ONLY here (build time, locally), never by the deployed service.

Usage:  MONGODB_URL=... ANTHROPIC_API_KEY=... python angle_index_build.py
"""
import os, re, json, time, datetime, collections
import pymongo
from anthropic import Anthropic
import angle_taxonomy as tax

MONGO_URL = os.environ["MONGODB_URL"]
DB_NAME = os.environ.get("MONGO_DB", "revenue-pro")
WINDOW_DAYS = int(os.environ.get("ANGLE_WINDOW_DAYS", "180"))
MIN_SPEND = float(os.environ.get("ANGLE_MIN_SPEND", "500"))
MAX_ADS = int(os.environ.get("ANGLE_MAX_ADS", "200"))          # cap classification cost
CLASSIFY_MODEL = os.environ.get("CLASSIFY_MODEL", "claude-haiku-4-5")
# Any status at/after an estimate was scheduled counts as a "set" (incl. later stages + canceled).
SET_STATUSES = {"estimate_set", "virtual_quote", "proposal_presented",
                "job_booked", "job_lost", "estimate_canceled"}

_client = Anthropic()


def _aid(company_id, ad_name):
    return f"{company_id}|{ad_name}"


def classify(ads):
    """ads: [{aid, primaryText, headline, adName}] -> {aid: angle_key}. Cheap batched LLM tagging."""
    out, BATCH = {}, 20
    for i in range(0, len(ads), BATCH):
        chunk = ads[i:i + BATCH]
        listing = "\n".join(
            f'{j}. adName="{a["adName"]}" | headline="{(a["headline"] or "")[:120]}" | primaryText="{(a["primaryText"] or "")[:400]}"'
            for j, a in enumerate(chunk))
        prompt = (
            "Classify each painting-contractor Facebook ad into exactly ONE angle from this taxonomy "
            "(use the key). Pick the single dominant angle.\n\n"
            f"TAXONOMY:\n{tax.list_for_prompt()}\n\n"
            f"ADS:\n{listing}\n\n"
            'Return ONLY a JSON object mapping the index number (as a string) to the angle key, '
            'e.g. {"0":"owner_identity","1":"transformation"}. No prose.')
        try:
            msg = _client.messages.create(model=CLASSIFY_MODEL, max_tokens=1000,
                                          messages=[{"role": "user", "content": prompt}])
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
            m = re.search(r"\{.*\}", text, re.DOTALL)
            mapping = json.loads(m.group(0)) if m else {}
            for j, a in enumerate(chunk):
                k = mapping.get(str(j))
                out[a["aid"]] = k if k in tax.KEYS else "uncategorized"
        except Exception as e:
            print(f"  classify batch {i} failed: {e}")
            for a in chunk:
                out[a["aid"]] = "uncategorized"
        print(f"  classified {min(i + BATCH, len(ads))}/{len(ads)}")
        time.sleep(0.4)
    return out


def main():
    db = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=20000).get_database(DB_NAME)
    since = (datetime.date.today() - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    print(f"window since {since} | min_spend ${MIN_SPEND:.0f} | model {CLASSIFY_MODEL}")

    # 1) spend + leads + creative per (company, ad)
    agg = {}
    for d in db["fbweeklyanalytics"].find(
            {"weekStartDate": {"$gte": since}, "isDeleted": {"$ne": True}},
            {"companyId": 1, "adName": 1, "creative": 1, "metrics": 1}):
        aid = _aid(d.get("companyId"), d.get("adName") or "")
        a = agg.setdefault(aid, {"spend": 0.0, "leads": 0.0, "primaryText": "", "headline": "",
                                 "adName": d.get("adName") or ""})
        m = d.get("metrics") or {}
        a["spend"] += float(m.get("spend") or 0)
        a["leads"] += float(m.get("total_conversions") or 0)
        cr = d.get("creative") or {}
        if cr.get("primaryText") and not a["primaryText"]:
            a["primaryText"] = cr["primaryText"]
        if cr.get("headline") and not a["headline"]:
            a["headline"] = cr["headline"]
    print(f"aggregated {len(agg)} (company,ad) units from fbweeklyanalytics")

    # 2) estimate sets + total CRM leads per (company, ad). Use Revenue Pro's own lead records (not
    # Meta's total_conversions, which is sparse) so set_rate = sets / RP-leads, per our metrics rules.
    sets = collections.Counter()
    leadcount = collections.Counter()
    for d in db["leads"].find({"isDeleted": {"$ne": True}},
                              {"companyId": 1, "adName": 1, "status": 1}):
        aid = _aid(d.get("companyId"), d.get("adName") or "")
        leadcount[aid] += 1
        if d.get("status") in SET_STATUSES:
            sets[aid] += 1

    # 3) keep meaningful ads, classify
    ads = [{"aid": k, **v} for k, v in agg.items()
           if v["spend"] >= MIN_SPEND and (v["primaryText"] or v["headline"])]
    ads.sort(key=lambda a: -a["spend"])
    ads = ads[:MAX_ADS]
    print(f"classifying {len(ads)} ads (>= ${MIN_SPEND:.0f} spend)")
    tags = classify(ads)

    # 4) aggregate by angle
    by = collections.defaultdict(lambda: {"spend": 0.0, "leads": 0.0, "sets": 0, "n_ads": 0})
    for a in ads:
        ang = tags.get(a["aid"], "uncategorized")
        b = by[ang]
        b["spend"] += a["spend"]
        b["leads"] += leadcount.get(a["aid"], 0)        # Revenue Pro CRM leads
        b["sets"] += sets.get(a["aid"], 0)
        b["n_ads"] += 1

    rows = []
    for ang, b in by.items():
        if ang == "uncategorized":
            continue
        cpes = round(b["spend"] / b["sets"], 0) if b["sets"] else None
        set_rate = round(b["sets"] / b["leads"], 4) if b["leads"] else None
        cpl = round(b["spend"] / b["leads"], 0) if b["leads"] else None
        rows.append({"angle": ang, "name": tax.NAMES[ang], "spend": round(b["spend"], 0),
                     "leads": int(b["leads"]), "sets": b["sets"], "cpes": cpes,
                     "set_rate": set_rate, "cpl": cpl, "n_ads": b["n_ads"]})
    # rank: ads that produced sets first, by CPES asc; then the rest
    rows.sort(key=lambda r: (r["cpes"] is None, r["cpes"] if r["cpes"] is not None else 9e9))

    index = {
        "built_at": datetime.datetime.utcnow().isoformat() + "Z",
        "window_days": WINDOW_DAYS, "min_spend": MIN_SPEND,
        "total_ads_scored": len(ads),
        "total_spend": round(sum(a["spend"] for a in ads), 0),
        "total_sets": sum(sets.get(a["aid"], 0) for a in ads),
        "angles": rows,
    }
    with open(os.path.join(os.path.dirname(__file__), "angle_index.json"), "w") as f:
        json.dump(index, f, indent=2)
    print(f"\nwrote angle_index.json — {len(rows)} angles, "
          f"${index['total_spend']:.0f} spend, {index['total_sets']} sets")
    for r in rows[:8]:
        print(f"  {r['name']:<34} CPES={r['cpes']} set_rate={r['set_rate']} n_ads={r['n_ads']} spend=${r['spend']:.0f}")


if __name__ == "__main__":
    main()
