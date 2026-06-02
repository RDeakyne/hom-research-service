"""Base44 REST client for the HOM Client Portal.
Key gotcha discovered in testing: Base44 sits behind Cloudflare — requests MUST send a
browser User-Agent or writes (POST/PUT) return Cloudflare error 1010. Reads tolerate it,
writes do not. So we always send a Mozilla UA.
"""
import os, httpx

APP_ID = os.environ["BASE44_APP_ID"]
KEY = os.environ["BASE44_API_KEY"]
BASE = f'{os.environ.get("BASE44_BASE_URL", "https://app.base44.com")}/api/apps/{APP_ID}'
HEADERS = {
    "api_key": KEY,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}
ENTITY = "ResearchIntelligence"


def _client():
    return httpx.Client(headers=HEADERS, timeout=40)


def get_client_record(client_id: str) -> dict:
    with _client() as c:
        r = c.get(f"{BASE}/entities/Client/{client_id}")
        r.raise_for_status()
        return r.json()


def get_research_record(client_id: str):
    with _client() as c:
        r = c.get(f"{BASE}/entities/{ENTITY}", params={"client_id": client_id})
        r.raise_for_status()
        data = r.json()
        return data[0] if isinstance(data, list) and data else None


def upsert_research(client_id: str, payload: dict) -> dict:
    """Create or update the single ResearchIntelligence record for a client. Verifies (Gate 1)."""
    payload = {**payload, "client_id": client_id}
    existing = get_research_record(client_id)
    with _client() as c:
        if existing:
            r = c.put(f"{BASE}/entities/{ENTITY}/{existing['id']}", json=payload)
        else:
            r = c.post(f"{BASE}/entities/{ENTITY}", json=payload)
        r.raise_for_status()
    check = get_research_record(client_id)  # Gate 1: read back
    return check or {}


def set_status(client_id: str, status: str, note: str = ""):
    """Flip the run status so the UI can show Running / Done / Error.
    Stored on the ResearchIntelligence record (status, status_note fields)."""
    existing = get_research_record(client_id)
    body = {"client_id": client_id, "status": status, "status_note": note}
    with _client() as c:
        if existing:
            c.put(f"{BASE}/entities/{ENTITY}/{existing['id']}", json={**existing, **body})
        else:
            c.post(f"{BASE}/entities/{ENTITY}", json=body)
