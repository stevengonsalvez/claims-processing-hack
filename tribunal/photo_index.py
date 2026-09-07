"""Recycled-photo detection: gpt-4.1-mini describes each damage photo, the description
is embedded (text-embedding-3-large) and indexed with the claim it was filed under.
The Fraud Investigator then learns "this photo was submitted before under CLM-x".

    python -m tribunal.photo_index seed     # index the 5 demo photos + planted CLM-0412 reuse of crash4.jpg
    python -m tribunal.photo_index match challenge-0/data/images/crash4.jpg
"""
import asyncio
import glob
import os
import sys

from azure.core.exceptions import HttpResponseError
from azure.search.documents import SearchClient
from azure.search.documents.indexes.models import SearchFieldDataType, SimpleField
from azure.search.documents.models import VectorizedQuery
from dotenv import load_dotenv

from .foundry import image_part, run_agent, text_part
from .search_tools import SEARCH_ENDPOINT, _cred, embed, ensure_index
from .workflow import REPO, _data_url

load_dotenv(override=True)
PHOTO_INDEX = os.environ.get("PHOTOS_INDEX", "claim-photos")
DESCRIBE = """Describe this vehicle damage photo for forensic matching: vehicle type, colour, visible make/model cues,
which panels are damaged and how, distinctive marks (stickers, plate fragments, rust, dents shape), background and lighting.
One dense paragraph, no speculation about cause. No preamble."""


async def describe(photo_path: str) -> str:
    return await run_agent("TribunalPhotoDescriber", DESCRIBE, [text_part("Photo:"), image_part(_data_url(photo_path))])


def ensure() -> None:
    ensure_index(PHOTO_INDEX, [
        SimpleField(name="claim_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="file_name", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="filed_at", type=SearchFieldDataType.String),
    ])


async def index_photo(claim_id: str, photo_path: str, filed_at: str, doc_id: str | None = None) -> dict:
    text = await describe(photo_path)
    doc = {"id": doc_id or f"{claim_id}-{os.path.basename(photo_path)}".replace(".", "-"), "claim_id": claim_id,
           "file_name": os.path.basename(photo_path), "filed_at": filed_at,
           "title": f"{claim_id} · {os.path.basename(photo_path)}", "content": text}
    doc["content_vector"] = embed(text)
    SearchClient(SEARCH_ENDPOINT, PHOTO_INDEX, _cred).upload_documents([doc])
    doc.pop("content_vector")
    return doc


async def match_photo(photo_path: str, top: int = 3, exclude_claim: str | None = None) -> tuple[str, list[dict]]:
    """Describe the incoming photo and return (description, prior photos ranked by similarity)."""
    text = await describe(photo_path)
    try:
        vq = VectorizedQuery(vector=embed(text), k_nearest_neighbors=top + 1, fields="content_vector")
        results = SearchClient(SEARCH_ENDPOINT, PHOTO_INDEX, _cred).search(search_text=None, vector_queries=[vq], top=top + 1)
        out = []
        for r in results:
            if exclude_claim and r.get("claim_id") == exclude_claim:
                continue
            out.append({"claim_id": r["claim_id"], "file_name": r["file_name"], "filed_at": r["filed_at"],
                        "similarity": round(r["@search.score"], 3), "description": r["content"][:240]})
        return text, out[:top]
    except HttpResponseError:
        return text, []


async def seed() -> None:
    ensure()
    images = sorted(glob.glob(os.path.join(REPO, "challenge-0", "data", "images", "crash*.jpg")))
    # Historic photos: the synthetic prior claims CLM-0001..0005 each "filed" one of the demo photos, except crash4,
    # which is planted under CLM-0412 (the paid duplicate the fraud agent already knows about).
    plan = []
    for i, p in enumerate(images, 1):
        name = os.path.basename(p)
        if name == "crash4.jpg":
            plan.append(("CLM-0412", p, "2025-05-14"))
        else:
            plan.append((f"CLM-{i:04d}", p, f"2025-0{i}-1{i}"))
    docs = await asyncio.gather(*[index_photo(c, p, d) for c, p, d in plan])
    for d in docs:
        print(f"indexed {d['id']}: {d['content'][:80]}...")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "seed"
    if cmd == "seed":
        asyncio.run(seed())
    else:
        import json
        text, hits = asyncio.run(match_photo(sys.argv[2]))
        print(text[:200], "...")
        print(json.dumps(hits, indent=1))
