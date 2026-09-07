"""FastAPI front door for the Claims Tribunal.

POST /adjudicate   multipart (sample=crashN | statements[] + photo) -> text/event-stream
POST /decision     human approve / deny / override; appended to data/decisions.json and
                   recorded as a searchable precedent (tribunal/precedent.py)
POST /appeal       multipart (claim_id, appeal_text, photo?) -> text/event-stream, session 2
GET  /claims/{id}  the last verdict for a claim plus its decisions and appeals
GET  /samples      demo claims from challenge-0/data
"""
import asyncio
import json
import os
import tempfile
import uuid
from datetime import datetime

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import precedent
from .af_workflow import adjudicate  # Challenge 4: Microsoft Agent Framework workflow graph
from .appeal import adjudicate_appeal
from .workflow import REPO

DATA = os.path.join(REPO, "challenge-0", "data")
DECISIONS = os.path.join(os.path.dirname(__file__), "data", "decisions.json")
CLAIMS = os.path.join(os.path.dirname(__file__), "data", "claims.json")
os.makedirs(os.path.dirname(DECISIONS), exist_ok=True)

# Claim case files: {claim_id: {"verdict": ..., "decisions": [...], "appeals": [...]}}.
# In memory for the live bench, written through to data/claims.json so a reload
# (uvicorn --reload restarts on every edit) does not lose the demo's history.
def _load(path: str, empty):
    """Never let a half-written case file take the API down on boot."""
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return empty


def _atomic(path: str, payload) -> None:
    """Write via .tmp + os.replace: a reload mid-write must not truncate the file."""
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=1, default=str)
    os.replace(tmp, path)


_claims: dict[str, dict] = _load(CLAIMS, {})


def _case(claim_id: str) -> dict:
    return _claims.setdefault(claim_id, {"verdict": None, "decisions": [], "appeals": []})


def _persist() -> None:
    _atomic(CLAIMS, _claims)


async def _sse(claim_id: str, first: tuple[str, dict], run) -> StreamingResponse:
    """Drive one adjudication/appeal on a queue and stream it as server-sent events."""
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event, data):
        await queue.put((event, data))

    async def pump():
        try:
            await run(emit)
        except Exception as e:  # noqa: BLE001 - a failed tribunal is an SSE error, not a 500
            await emit("error", {"agent": "tribunal", "message": str(e)[:500]})
        finally:
            await queue.put(None)

    async def stream():
        yield f"event: {first[0]}\ndata: {json.dumps(first[1])}\n\n"
        task = asyncio.create_task(pump())
        while (item := await queue.get()) is not None:
            event, data = item
            yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        await task
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

app = FastAPI(title="Claims Tribunal", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/data", StaticFiles(directory=DATA), name="data")


def sample_files(name: str) -> tuple[list[str], str | None]:
    statements = [os.path.join(DATA, "statements", f"{name}_{side}.jpeg") for side in ("front", "back")]
    statements = [p for p in statements if os.path.exists(p)]
    photo = os.path.join(DATA, "images", f"{name}.jpg")
    return statements, photo if os.path.exists(photo) else None


@app.get("/samples")
def samples():
    names = sorted({f.split("_")[0] for f in os.listdir(os.path.join(DATA, "statements")) if f.endswith(".jpeg")})
    return [{"name": n, "photo": f"/data/images/{n}.jpg", "statements": [f"/data/statements/{n}_front.jpeg", f"/data/statements/{n}_back.jpeg"]} for n in names]


@app.post("/adjudicate")
async def adjudicate_route(
    sample: str | None = Form(None),
    statements: list[UploadFile] = File([]),
    photo: UploadFile | None = File(None),
):
    claim_id = f"CLM-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    if sample:
        if sample not in {s["name"] for s in samples()}:
            raise HTTPException(400, f"unknown sample {sample!r}")
        statement_paths, photo_path = sample_files(sample)
    else:
        tmp = tempfile.mkdtemp(prefix="tribunal-")
        statement_paths = []
        for i, up in enumerate(statements):
            p = os.path.join(tmp, f"statement_{i}{os.path.splitext(up.filename or '.jpeg')[1] or '.jpeg'}")
            with open(p, "wb") as f:
                f.write(await up.read())
            statement_paths.append(p)
        photo_path = None
        if photo:
            photo_path = os.path.join(tmp, f"photo{os.path.splitext(photo.filename or '.jpg')[1] or '.jpg'}")
            with open(photo_path, "wb") as f:
                f.write(await photo.read())

    async def run(emit):
        verdict = await adjudicate(claim_id, statement_paths, photo_path, emit)
        if verdict:
            _case(claim_id)["verdict"] = verdict
            _persist()

    return await _sse(claim_id, ("claim.start", {"claim_id": claim_id, "sample": sample}), run)


@app.post("/decision")
async def decision(body: dict):
    """Record the human ruling: decisions.json for the audit trail, `precedents` index for the next tribunal."""
    verdict = body.pop("verdict", None) or {}
    claim_id = body.get("claim_id") or verdict.get("claim_id") or ""
    body["recorded_at"] = datetime.now().isoformat()

    precedent_id = None
    if verdict:
        try:
            precedent_id = precedent.record_precedent(
                verdict, body.get("human_decision", ""), body.get("reason", ""))["id"]
        except Exception as e:  # noqa: BLE001 - the audit trail must survive a search outage
            body["precedent_error"] = str(e)[:300]
    body["precedent_id"] = precedent_id

    rows = _load(DECISIONS, [])
    rows.append(body)
    _atomic(DECISIONS, rows)

    if claim_id:
        case = _case(claim_id)
        case["decisions"].append(body)
        if verdict:
            case["verdict"] = verdict
        _persist()
    return {"ok": True, "count": len(rows), "precedent_id": precedent_id}


@app.get("/claims/{claim_id}")
def claim_case(claim_id: str):
    """The claimant page (/claim/<id>) and the bench both read the case file from here."""
    case = _claims.get(claim_id)
    if case is None:
        raise HTTPException(404, f"unknown claim {claim_id!r}")
    return case


@app.post("/appeal")
async def appeal_route(
    claim_id: str = Form(...),
    appeal_text: str = Form(...),
    photo: UploadFile | None = File(None),
):
    """Session 2: the claimant answers verdict v1, optionally with a new photo."""
    case = _claims.get(claim_id)
    if case is None or not case.get("verdict"):
        raise HTTPException(404, f"no verdict on file for {claim_id!r}")
    v1 = case["verdict"]

    photo_path = None
    if photo is not None and photo.filename:
        tmp = tempfile.mkdtemp(prefix="tribunal-appeal-")
        photo_path = os.path.join(tmp, f"appeal{os.path.splitext(photo.filename)[1] or '.jpg'}")
        with open(photo_path, "wb") as f:
            f.write(await photo.read())

    async def run(emit):
        async def relay(event, data):
            await emit(event, data)
            if event == "appeal.verdict":
                case["appeals"].append({"appeal_text": appeal_text, "new_photo": bool(photo_path),
                                        "v2": data["v2"], "outcome": data["outcome"], "diff": data["diff"],
                                        "at": datetime.now().isoformat()})
                case["verdict"] = data["v2"]
                _persist()
        await adjudicate_appeal(claim_id, v1, appeal_text, photo_path, relay)

    return await _sse(claim_id, ("appeal.start", {"claim_id": claim_id, "v1_decision": v1.get("decision")}), run)


@app.get("/decisions")
def decisions():
    return _load(DECISIONS, [])
