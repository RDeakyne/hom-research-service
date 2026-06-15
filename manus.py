"""Manus API client — the Meta Ad Library competitor ad teardown (Phase 2).

The Ad Library has no usable public API for commercial (non-political) US ads, so we drive Manus
(an agentic browser) to read it. Manus is ASYNC: task.create -> poll task.detail -> read the result.
We force a structured JSON result via `structured_output_schema` so it parses cleanly into the
ResearchIntelligence `competitor_ad_intel` field.

Key metric (per the Limitless method): AD LONGEVITY — an ad still running 60+ days is proven/converting.
Plus the WHITESPACE: which angles / objections nobody is running. Skipped gracefully if MANUS_API_KEY
is unset. Runs minutes (browsing), so the pipeline calls this as a late step and updates the record
when it returns.
"""
import os, time, json, httpx

KEY = os.environ.get("MANUS_API_KEY", "")
BASE = os.environ.get("MANUS_BASE", "https://api.manus.ai")
PROFILE = os.environ.get("MANUS_PROFILE", "manus-1.6")
POLL_SECONDS = int(os.environ.get("MANUS_POLL_SECONDS", "10"))
MAX_WAIT = int(os.environ.get("MANUS_MAX_WAIT", "1500"))     # 25 min hard cap

# JSON schema Manus must return — mirrors the competitor_ad_intel contract. Manus's structured-output
# subset requires EVERY object to list ALL properties in `required` and set additionalProperties=false.
def _obj(props):
    return {"type": "object", "properties": props,
            "required": list(props.keys()), "additionalProperties": False}


_ADVERTISER = _obj({
    "name": {"type": "string"},
    "facebook_page_url": {"type": "string"},
    "page_unresolved": {"type": "boolean"},
    "ads_active": {"type": "integer"},
    "formats": {"type": "array", "items": {"type": "string"}},
    "oldest_active_ad_started": {"type": "string"},
    "oldest_active_ad_days": {"type": "integer"},
    "lead_offer": {"type": "string"},
    "offer_framing": {"type": "string"},
    "top_hooks": {"type": "array", "items": {"type": "string"}},
    "dominant_angles": {"type": "array", "items": {"type": "string"}},
})
_LONGEVITY = _obj({
    "advertiser": {"type": "string"}, "start_date": {"type": "string"},
    "duration_days": {"type": "integer"}, "offer": {"type": "string"},
    "hook": {"type": "string"}, "angle": {"type": "string"},
})
_WHITESPACE = _obj({
    "angles_nobody_runs": {"type": "array", "items": {"type": "string"}},
    "objections_nobody_addresses": {"type": "array", "items": {"type": "string"}},
    "obvious_differentiator_invisible_in_all_ads": {"type": "string"},
})
SCHEMA = _obj({
    "advertisers": {"type": "array", "items": _ADVERTISER},
    "ad_longevity_table": {"type": "array", "items": _LONGEVITY},
    "offers_in_market": {"type": "array", "items": {"type": "string"}},
    "messaging_patterns": {"type": "array", "items": {"type": "string"}},
    "whitespace": _WHITESPACE,
    "sourcing_note": {"type": "string"},
})

EMPTY = {"advertisers": [], "ad_longevity_table": [], "offers_in_market": [],
         "messaging_patterns": [], "whitespace": {}, "sourcing_note": ""}


def _h():
    return {"x-manus-api-key": KEY, "Content-Type": "application/json"}


def _mission(client_name, advertisers):
    lines = "\n".join(
        f"- {a['name']}: {a.get('facebook_page_url') or '(find their Facebook Page by name + city)'}"
        for a in advertisers)
    return f"""META AD LIBRARY COMPETITOR AD TEARDOWN — for {client_name}, a residential painting contractor.

Go to https://www.facebook.com/ads/library/ , set Country = United States and Ad category = All ads,
and search EACH advertiser below by their Facebook Page:
{lines}

FOR EACH ADVERTISER, find:
1. AD INVENTORY — how many ads are currently active; the formats used (video / image / carousel);
   the oldest currently-active ad and the date it started running.
2. AD LONGEVITY (most important) — list ads still running 60+ days (proven/converting). For each:
   start date, days running, the offer, and the opening line / hook. 60+ days = proven, 30-59 = promising,
   under 30 = untested.
3. OFFER — the intro offer they lead with and how it's framed (discount / urgency / risk-reversal /
   financing / free estimate).
4. HOOK & MESSAGING — the opening line of their longest-running ad; the problem or emotional state hit
   in the first 3 seconds; language patterns repeated across their creative.
5. DOMINANT ANGLES — the 1-3 main angles they lean on (e.g. before/after, owner identity, social proof,
   seasonal urgency, risk reversal, problem/pain).

THEN, across ALL advertisers reviewed (the WHITESPACE — this is the most valuable output):
- Which painting ad angles or hooks is NOBODY running?
- Which homeowner objection is NOBODY addressing in their ads?
- What obvious differentiator is invisible in everyone's ads?

If a Page cannot be found or has no ads, set page_unresolved=true for it and skip it — do NOT invent ads.
Quote real ad copy verbatim. Return the structured JSON result."""


def _extract(messages):
    for m in messages:
        if m.get("type") == "structured_output_result":
            v = (m.get("structured_output_result") or {}).get("value")
            if v:
                return v
    # fallback: parse JSON out of the assistant text
    text = "\n".join((m.get("assistant_message") or {}).get("content", "")
                     for m in messages if m.get("type") == "assistant_message")
    import re
    mt = re.search(r"\{.*\}", text, re.DOTALL)
    if mt:
        try:
            return json.loads(mt.group(0))
        except Exception:
            pass
    return None


def teardown(client_name, advertisers, log=print):
    """advertisers: [{name, facebook_page_url}]. Returns competitor_ad_intel dict (EMPTY-shaped on
    failure, with a sourcing_note). Returns None only if Manus is not configured / no advertisers."""
    if not KEY or not advertisers:
        return None
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(f"{BASE}/v2/task.create", headers=_h(), json={
                "message": {"content": _mission(client_name, advertisers)},
                "agent_profile": PROFILE, "interactive_mode": False,
                "hide_in_task_list": True, "structured_output_schema": SCHEMA,
                "title": f"Ad Library teardown — {client_name}",
            })
            r.raise_for_status()
            task_id = r.json()["task_id"]
        log(f"manus task {task_id} created; polling...")

        waited = 0
        status = "running"
        while waited < MAX_WAIT:
            time.sleep(POLL_SECONDS); waited += POLL_SECONDS
            with httpx.Client(timeout=30) as c:
                t = c.get(f"{BASE}/v2/task.detail", headers=_h(),
                          params={"task_id": task_id}).json().get("task", {})
            status = t.get("status", "running")
            if status in ("stopped", "error"):
                break
        log(f"manus task {status} after ~{waited}s (credits: {t.get('credit_usage')})")

        msgs, cursor = [], None
        for _ in range(20):
            with httpx.Client(timeout=60) as c:
                resp = c.get(f"{BASE}/v2/task.listMessages", headers=_h(),
                             params={"task_id": task_id, "order": "asc", "limit": 100,
                                     **({"cursor": cursor} if cursor else {})}).json()
            msgs.extend(resp.get("messages", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        data = _extract(msgs)
        if not isinstance(data, dict):
            return {**EMPTY, "sourcing_note": f"Manus returned no parseable result (status {status})."}
        for k, v in EMPTY.items():
            data.setdefault(k, v)
        return data
    except Exception as e:
        return {**EMPTY, "sourcing_note": f"Manus teardown failed: {str(e)[:200]}"}
