"""HOM Research Intelligence service. Base44's "Run Research" button POSTs here.
Returns immediately (202) and runs the pipeline in the background, updating the record's
status field (Running -> Done / Error) so the portal UI can poll it.
"""
import os, time, threading
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, BackgroundTasks, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import base44, pipeline

app = FastAPI(title="HOM Research Intelligence")
# The Base44 button calls this from the browser, so allow cross-origin requests
# (incl. the X-Run-Token header + the OPTIONS preflight).
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)
RUN_TOKEN = os.environ.get("RUN_TOKEN", "")


class RunReq(BaseModel):
    client_id: str


def _job(client_id: str):
    try:
        pipeline.run(client_id)
    except Exception as e:
        base44.set_status(client_id, "Error", str(e)[:300])


# --- Portal-triggered polling ---
# The most robust trigger: the Base44 button just sets a ResearchIntelligence record's status to
# "Requested" (a native Base44 write that can't fail with CORS/500). This loop polls Base44 server-side
# and runs any requested research. No browser-to-service call, so the button never errors.
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))


def _poller():
    while True:
        try:
            recs = base44._request("GET", f"{base44.BASE}/entities/{base44.ENTITY}",
                                   params={"limit": 200}).json()
            for r in (recs if isinstance(recs, list) else []):
                if r.get("status") == "Requested" and r.get("client_id"):
                    cid = r["client_id"]
                    try:
                        base44.set_status(cid, "Running", "Picked up from portal request...")  # claim it
                        pipeline.run(cid)
                    except Exception as e:
                        try:
                            base44.set_status(cid, "Error", str(e)[:250])
                        except Exception:
                            pass
        except Exception:
            pass
        time.sleep(POLL_SECONDS)


@app.on_event("startup")
def _start_poller():
    threading.Thread(target=_poller, daemon=True).start()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/run", status_code=200)   # 200 not 202 — some HTTP clients (e.g. the Base44 button) treat non-200 as an error
def run(req: RunReq, bg: BackgroundTasks, x_run_token: str = Header(default="")):
    if RUN_TOKEN and x_run_token != RUN_TOKEN:
        raise HTTPException(401, "bad run token")
    # Best-effort status write — a transient Base44 blip here must NOT crash the button.
    # The background job (which retries) will set status when it runs.
    try:
        base44.set_status(req.client_id, "Running", "Queued...")
    except Exception:
        pass
    bg.add_task(_job, req.client_id)
    return {"status": "Running", "client_id": req.client_id}
