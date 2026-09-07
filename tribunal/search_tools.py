"""Azure AI Search: policy documents index + prior-claims index, client-side embeddings
(text-embedding-3-large) and hybrid (keyword + vector) queries."""
import os

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)

SEARCH_ENDPOINT = os.environ.get("SEARCH_SERVICE_ENDPOINT", "")
SEARCH_KEY = os.environ.get("SEARCH_ADMIN_KEY", "")
POLICY_INDEX = os.environ.get("SEARCH_INDEX_NAME", "insurance-documents-index")
PRIOR_INDEX = os.environ.get("PRIOR_CLAIMS_INDEX", "prior-claims")
EMBED_MODEL = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
DIMS = 3072

_cred = AzureKeyCredential(SEARCH_KEY)
_emb: OpenAI | None = None


def embed(text: str) -> list[float]:
    global _emb
    if _emb is None:
        _emb = OpenAI(base_url=os.environ.get("AZURE_OPENAI_BASE_URL"), api_key=os.environ.get("AZURE_OPENAI_KEY"))
    return _emb.embeddings.create(model=EMBED_MODEL, input=text[:8000]).data[0].embedding


def _base_fields() -> list:
    return [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=DIMS,
            vector_search_profile_name="tribunal-profile",
        ),
    ]


def ensure_index(name: str, extra_fields: list) -> None:
    vs = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="tribunal-hnsw")],
        profiles=[VectorSearchProfile(name="tribunal-profile", algorithm_configuration_name="tribunal-hnsw")],
    )
    SearchIndexClient(SEARCH_ENDPOINT, _cred).create_or_update_index(
        SearchIndex(name=name, fields=_base_fields() + extra_fields, vector_search=vs)
    )


def upload(name: str, docs: list[dict]) -> None:
    for d in docs:
        d["content_vector"] = embed(d["title"] + "\n" + d["content"])
    SearchClient(SEARCH_ENDPOINT, name, _cred).upload_documents(docs)


def hybrid(name: str, text: str, top: int = 5) -> list[dict]:
    vq = VectorizedQuery(vector=embed(text), k_nearest_neighbors=top, fields="content_vector")
    results = SearchClient(SEARCH_ENDPOINT, name, _cred).search(search_text=text, vector_queries=[vq], top=top)
    out = []
    for r in results:
        d = {k: v for k, v in r.items() if k != "content_vector" and not k.startswith("@")}
        d["score"] = round(r["@search.score"], 4)
        out.append(d)
    return out


def search_policies(policy_number: str, claim_text: str) -> list[dict]:
    """Hybrid search picks the policy document, then every chunk of that document is
    returned in order: policies are 6-10 KB, so the analyst reads the whole contract
    (deductibles and exclusions live in sections a top-k query tends to skip)."""
    hits = hybrid(POLICY_INDEX, f"{policy_number} {claim_text}", top=3)
    if not hits:
        return []
    file_name = hits[0].get("file_name")
    client = SearchClient(SEARCH_ENDPOINT, POLICY_INDEX, _cred)
    chunks = client.search(search_text="*", filter=f"file_name eq '{file_name}'", top=100,
                           select=["id", "title", "content", "file_name"])
    out = [dict(c) for c in chunks]
    out.sort(key=lambda c: int(c["id"].rsplit("-", 1)[1]))
    for c in out:
        c["score"] = hits[0]["score"]
    return out


def search_prior_claims(claim_text: str) -> list[dict]:
    """Hybrid search; `score` is the RRF fusion score, so also return cosine-style
    similarity from a pure vector query for the fraud agent to reason about."""
    vec = embed(claim_text)
    vq = VectorizedQuery(vector=vec, k_nearest_neighbors=5, fields="content_vector")
    results = SearchClient(SEARCH_ENDPOINT, PRIOR_INDEX, _cred).search(search_text=None, vector_queries=[vq], top=5)
    out = []
    for r in results:
        d = {k: v for k, v in r.items() if k != "content_vector" and not k.startswith("@")}
        d["similarity"] = round(r["@search.score"], 3)  # ponytail: HNSW cosine score, 1.0 = identical
        out.append(d)
    return out
