"""FastAPI front door for the Claims Tribunal.

POST /adjudicate   multipart (sample=crashN | statements[] + photo) -> text/event-stream
POST /decision     human approve / deny / override, appended to data/decisions.json
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

from .af_workflow import adjudicate  # Challenge 4: Microsoft Agent Framework workflow graph
from .workflow import REPO

DATA = os.path.join(REPO, "challenge-0", "data")
DECISIONS = os.path.join(os.path.dirname(__file__), "data", "decisions.json")
os.makedirs(os.path.dirname(DECISIONS), exist_ok=True)

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

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event, data):
        await queue.put((event, data))

    async def run():
        try:
            await adjudicate(claim_id, statement_paths, photo_path, emit)
        except Exception as e:
            await emit("error", {"agent": "tribunal", "message": str(e)[:500]})
        finally:
            await queue.put(None)

    async def stream():
        yield f"event: claim.start\ndata: {json.dumps({'claim_id': claim_id, 'sample': sample})}\n\n"
        task = asyncio.create_task(run())
        while (item := await queue.get()) is not None:
            event, data = item
            yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        await task
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/decision")
async def decision(body: dict):
    body["recorded_at"] = datetime.now().isoformat()
    rows = json.load(open(DECISIONS)) if os.path.exists(DECISIONS) else []
    rows.append(body)
    json.dump(rows, open(DECISIONS, "w"), indent=1)
    return {"ok": True, "count": len(rows)}


@app.get("/decisions")
def decisions():
    return json.load(open(DECISIONS)) if os.path.exists(DECISIONS) else []
