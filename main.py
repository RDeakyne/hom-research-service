"""HOM Research Intelligence service. Base44's "Run Research" button POSTs here.
Returns immediately (202) and runs the pipeline in the background, updating the record's
status field (Running -> Done / Error) so the portal UI can poll it.
"""
import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel
import base44, pipeline

app = FastAPI(title="HOM Research Intelligence")
RUN_TOKEN = os.environ.get("RUN_TOKEN", "")


class RunReq(BaseModel):
    client_id: str


def _job(client_id: str):
    try:
        pipeline.run(client_id)
    except Exception as e:
        base44.set_status(client_id, "Error", str(e)[:300])


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/run", status_code=202)
def run(req: RunReq, bg: BackgroundTasks, x_run_token: str = Header(default="")):
    if RUN_TOKEN and x_run_token != RUN_TOKEN:
        raise HTTPException(401, "bad run token")
    base44.set_status(req.client_id, "Running", "Queued...")
    bg.add_task(_job, req.client_id)
    return {"status": "Running", "client_id": req.client_id}
