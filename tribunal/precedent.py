"""Precedent memory: every human approve / deny / override becomes a searchable
precedent that later tribunals retrieve and cite.

Index `precedents` (Azure AI Search, client-side text-embedding-3-large vectors).
"""
import os
import time
from datetime import datetime

from azure.core.exceptions import HttpResponseError
from azure.search.documents import SearchClient
from azure.search.documents.indexes.models import SearchFieldDataType, SimpleField
from azure.search.documents.models import VectorizedQuery
from dotenv import load_dotenv

from .search_tools import SEARCH_ENDPOINT, _cred, embed, ensure_index

load_dotenv(override=True)
PRECEDENT_INDEX = os.environ.get("PRECEDENTS_INDEX", "precedents")
_ready = False


def ensure() -> None:
    global _ready
    if not _ready:
        ensure_index(PRECEDENT_INDEX, [
            SimpleField(name="claim_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="tribunal_decision", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="human_decision", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="reason", type=SearchFieldDataType.String),
            SimpleField(name="policy_number", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="vin", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="fraud_score", type=SearchFieldDataType.Double),
            SimpleField(name="net_payout", type=SearchFieldDataType.Double),
            SimpleField(name="recorded_at", type=SearchFieldDataType.String),
        ])
        _ready = True


def record_precedent(verdict: dict, human_decision: str, reason: str) -> dict:
    """Write one adjuster decision back as a precedent document. Returns the doc (minus vector)."""
    ensure()
    claim = verdict.get("claim") or {}
    fraud = (verdict.get("fraud") or {}).get("score", 0) or 0
    net = (verdict.get("payout") or {}).get("net", 0) or 0
    content = (
        f"Precedent for policy {claim.get('policy_number')}: {claim.get('vehicle_year_make_model')} VIN {claim.get('vehicle_vin')}, "
        f"incident {claim.get('incident_date')}: {claim.get('damage_description')}. "
        f"Tribunal decided {verdict.get('decision')} (fraud score {fraud}, net payout ${net:,}). "
        f"Rationale: {verdict.get('rationale')}. "
        f"Adjuster decision: {human_decision}. Reason: {reason or 'none given'}."
    )
    doc = {
        "id": f"PREC-{verdict.get('claim_id', 'x')}-{datetime.now():%H%M%S}",
        "claim_id": verdict.get("claim_id", ""),
        "title": f"Precedent · {verdict.get('claim_id')} · adjuster {human_decision}",
        "content": content,
        "tribunal_decision": verdict.get("decision", ""),
        "human_decision": human_decision,
        "reason": reason or "",
        "policy_number": claim.get("policy_number") or "",
        "vin": claim.get("vehicle_vin") or "",
        "fraud_score": float(fraud),
        "net_payout": float(net),
        "recorded_at": datetime.now().isoformat(),
    }
    doc["content_vector"] = embed(doc["title"] + "\n" + content)
    client = SearchClient(SEARCH_ENDPOINT, PRECEDENT_INDEX, _cred)
    client.upload_documents([doc])
    for _ in range(20):  # AI Search commits asynchronously; the next tribunal must see this ruling
        try:
            client.get_document(key=doc["id"])
            break
        except HttpResponseError:
            time.sleep(0.25)
    doc.pop("content_vector")
    return doc


def search_precedents(claim_text: str, top: int = 3) -> list[dict]:
    """Vector search over adjuster precedents; empty list when the index does not exist yet."""
    try:
        vq = VectorizedQuery(vector=embed(claim_text), k_nearest_neighbors=top, fields="content_vector")
        results = SearchClient(SEARCH_ENDPOINT, PRECEDENT_INDEX, _cred).search(search_text=None, vector_queries=[vq], top=top)
        out = []
        for r in results:
            d = {k: v for k, v in r.items() if k != "content_vector" and not k.startswith("@")}
            d["similarity"] = round(r["@search.score"], 3)
            out.append(d)
        return out
    except HttpResponseError:
        return []


def purge() -> int:
    """Delete every precedent document. Rehearsal reset: the demo needs an empty index
    so crash4 opens on REFER before the adjuster override creates the first precedent."""
    ensure()
    client = SearchClient(SEARCH_ENDPOINT, PRECEDENT_INDEX, _cred)
    ids = [{"id": r["id"]} for r in client.search(search_text="*", select=["id"], top=1000)]
    if ids:
        client.delete_documents(ids)
    return len(ids)


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) > 1 and sys.argv[1] == "purge":
        print(f"purged {purge()} precedents from {PRECEDENT_INDEX}")
        raise SystemExit(0)
    v = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(open("tribunal/data/verdict_crash4.json"))
    print(json.dumps(record_precedent(v, "override", "SIU cleared the VIN reuse: vehicle sold by Bennett in June, bill of sale on file"), indent=1))
    print(json.dumps(search_precedents("2011 Subaru Outback rear-end collision same VIN prior claim"), indent=1))
