# HOM Research Intelligence — Service

> **Single source of truth:** this folder (`research-service/` in the HOM monorepo) is the canonical code you edit. Render builds from a public mirror repo (`RDeakyne/hom-research-service`). To ship a change: edit here → run **`./deploy.sh`** → Render auto-redeploys. Never hand-edit the mirror. Live service: `srv-d8f4dj8g4nts738guetg` → https://hom-research-intelligence.onrender.com

The backend that powers the **"Run Research"** button in the Base44 client portal. It does everything the `painter-market-research` skill does, autonomously, and writes the result back into each client's Research Intelligence tab.

```
Base44 "Run Research" button (on a client)
  → POST /run { client_id }   (with X-Run-Token header)
  → reads that client's Company Info (service_areas zips, services, avg job size) from Base44
  → scores every zip on ICP-Match (Census + centroids — deterministic Python)
  → researches competitors / review complaints / homeowner concerns (Claude API + web search)
  → writes the ResearchIntelligence record back to Base44, status Running → Done
```

## Files
| File | Role |
|---|---|
| `main.py` | FastAPI app. `/run` endpoint (returns 202, runs in background, updates status). `/health`. |
| `pipeline.py` | Orchestrates one client run: parse → score → bucket (Broad/HQ/Expansion) → research → publish. |
| `scoring.py` | Deterministic ICP-Match per zip (Census Reporter + zippopotam centroids). No LLM. |
| `research.py` | Claude API + web search: competitors, complaints, homeowner concerns → JSON. |
| `base44.py` | Base44 REST client (sends a browser User-Agent — required or Cloudflare returns 1010). |

## Setup
1. `cp .env.example .env` and fill in `BASE44_API_KEY`, `ANTHROPIC_API_KEY`, and a long random `RUN_TOKEN`.
2. `pip install -r requirements.txt`
3. Run locally: `uvicorn main:app --reload --port 8000`
4. Test: `curl -X POST localhost:8000/run -H "Content-Type: application/json" -H "X-Run-Token: <token>" -d '{"client_id":"69f0b67428a77f7a71d7d9d9"}'`  (Ranger)

## Deploy (pick one host)
Any host that runs a Python web service works. Easiest:
- **Render / Railway / Fly.io:** point at this folder, start command `uvicorn main:app --host 0.0.0.0 --port $PORT`, set the env vars. Gives you a public `https://…/run` URL.
- **Google Cloud Run:** containerize (uvicorn), deploy, set env vars.

Set the same `RUN_TOKEN` here and in Base44. **Never commit `.env`.**

## Wire the Base44 button
Add to the client view (paste into Base44's AI builder):
> Add a **"Run Research"** button on each client (and on the Research Intelligence tab). When clicked, POST to `https://<your-service-url>/run` with JSON body `{ "client_id": <this client's id> }` and header `X-Run-Token: <the RUN_TOKEN>`. Show the ResearchIntelligence `status` field next to it (Running / Done / Error) and poll until Done, then refresh the tab.

## Notes / honest limits
- **Two things only you can provide:** a host (public URL) and an `ANTHROPIC_API_KEY` (billed per run — mostly the web-search research; scoring is free).
- Competitor **ad teardown** stays manual (Ads Library isn't machine-readable) — those fields are left blank for the team.
- **Reddit** is often crawler-blocked; the concerns step uses web search across Reddit + forums and flags thin sourcing rather than fabricating.
- High-Quality home-age weighting (1985–2010) is approximated by income+price ranking here; refine in `pipeline.py` if you add year-built (B25034) to `scoring.py`.
