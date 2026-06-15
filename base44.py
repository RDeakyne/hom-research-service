"""Base44 REST client for the HOM Client Portal.
Key gotcha: Base44 sits behind Cloudflare — requests MUST send a browser User-Agent or writes
(POST/PUT) return Cloudflare error 1010. Reads tolerate it, writes do not. So we always send a Mozilla UA.
Also: Base44 has transient 5xx blips (503s) — every request retries with backoff so a hiccup doesn't
crash a run. Persistent failures still raise (caller decides).
"""
import os, time, httpx

APP_ID = os.environ["BASE44_APP_ID"]
KEY = os.environ["BASE44_API_KEY"]
BASE = f'{os.environ.get("BASE44_BASE_URL", "https://app.base44.com")}/api/apps/{APP_ID}'
HEADERS = {
    "api_key": KEY,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}
ENTITY = "ResearchIntelligence"


def _request(method: str, url: str, retries: int = 5, **kw):
    """Issue a request, retrying transient 5xx + network errors (Base44/Cloudflare blips).
    Returns the response (caller calls raise_for_status for 4xx). Raises on persistent failure."""
    last = None
    for attempt in range(retries):
        try:
            with httpx.Client(headers=HEADERS, timeout=40) as c:
                r = c.request(method, url, **kw)
            if r.status_code < 500:
                return r
            last = r
        except httpx.HTTPError as e:
            last = e
        time.sleep(0.9 * (attempt + 1))   # backoff
    if isinstance(last, httpx.Response):
        last.raise_for_status()
    raise last if last else RuntimeError(f"{method} {url} failed")


def get_client_record(client_id: str) -> dict:
    r = _request("GET", f"{BASE}/entities/Client/{client_id}")
    r.raise_for_status()
    return r.json()


def get_research_record(client_id: str):
    r = _request("GET", f"{BASE}/entities/{ENTITY}", params={"client_id": client_id})
    r.raise_for_status()
    data = r.json()
    return data[0] if isinstance(data, list) and data else None


def upsert_research(client_id: str, payload: dict) -> dict:
    """Create or update the single ResearchIntelligence record for a client. Verifies (Gate 1)."""
    payload = {**payload, "client_id": client_id}
    existing = get_research_record(client_id)
    if existing:
        r = _request("PUT", f"{BASE}/entities/{ENTITY}/{existing['id']}", json=payload)
    else:
        r = _request("POST", f"{BASE}/entities/{ENTITY}", json=payload)
    r.raise_for_status()
    return get_research_record(client_id) or {}


def update_research_fields(client_id: str, fields: dict) -> dict:
    """Merge specific fields into the client's RI record (read-modify-write, like set_status). Used
    for the late two-stage publish (competitor_ad_intel + angle_intelligence arrive after the core
    report). Merges with existing so a partial PUT can't wipe already-written fields."""
    existing = get_research_record(client_id)
    body = {**(existing or {}), "client_id": client_id, **fields}
    if existing:
        _request("PUT", f"{BASE}/entities/{ENTITY}/{existing['id']}", json=body).raise_for_status()
    else:
        _request("POST", f"{BASE}/entities/{ENTITY}", json=body).raise_for_status()
    return get_research_record(client_id) or {}


def set_status(client_id: str, status: str, note: str = ""):
    """Flip the run status so the UI can show Running / Done / Error. Best-effort — callers in the
    request path should tolerate failure (a Base44 blip shouldn't crash the endpoint)."""
    existing = get_research_record(client_id)
    body = {"client_id": client_id, "status": status, "status_note": note}
    if existing:
        _request("PUT", f"{BASE}/entities/{ENTITY}/{existing['id']}", json={**existing, **body}).raise_for_status()
    else:
        _request("POST", f"{BASE}/entities/{ENTITY}", json=body).raise_for_status()
