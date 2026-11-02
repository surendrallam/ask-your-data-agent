"""Web UI: a single page over the agent.

Run:  python -m askdata.web.app    then open http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from askdata.config import DATA_THROUGH, MODELS, TODAY, MissingKeyError, api_key

STATIC = Path(__file__).parent / "static"

EXAMPLES = [
    "What were the top 5 products by net revenue in FY2026?",
    "Show monthly net revenue for FY2026.",
    "How many active customers do we have?",
    "What was the on-time shipping rate in the West region last quarter?",
    "What was each region's target attainment in Q1 FY2027?",
    "What were Priya's sales last quarter?",
]

app = FastAPI(title="Ask your data", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_agent = None
_agent_lock = threading.Lock()


def get_agent():
    """One shared agent (thread-safe: each question gets its own tool session)."""
    global _agent
    with _agent_lock:
        if _agent is None:
            from askdata.agent import Agent

            _agent = Agent()
        return _agent


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    model: str = "auto"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta() -> dict:
    return {
        "key_present": api_key() is not None,
        "today": TODAY,
        "data_through": DATA_THROUGH,
        "examples": EXAMPLES,
        "models": [{"key": "auto", "label": "Auto (Haiku 5.5, then Sonnet 5.5 if needed)"}]
        + [{"key": k, "label": m.label} for k, m in MODELS.items()],
    }


@app.post("/api/ask")
def ask(req: AskRequest) -> dict:
    if req.model not in ("auto", *MODELS):
        raise HTTPException(400, f"Unknown model {req.model!r}")
    try:
        agent = get_agent()
    except MissingKeyError as e:
        raise HTTPException(503, str(e)) from None
    result = agent.ask(req.question.strip(), config=req.model)
    out = result.to_dict()
    out["model_label"] = MODELS[result.model_used].label if result.model_used else None
    return out


def main() -> None:
    import uvicorn

    host = os.environ.get("ASKDATA_HOST", "127.0.0.1")
    port = int(os.environ.get("ASKDATA_PORT", "8000"))
    print(f"Ask your data: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
