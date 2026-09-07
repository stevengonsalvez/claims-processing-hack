"""Azure AI Search: policy documents index + prior-claims index.

Challenge 1 shape: hybrid retrieval = keyword + vector + semantic reranker, with
integrated vectorization (an AzureOpenAIVectorizer on both indexes) so a caller can
send raw text and let the search service embed it server-side (VectorizableTextQuery).
Documents are still written with client-side vectors, which keeps seeding fast and
makes the two paths directly comparable.

    python -m tribunal.search_tools     # proof run: vectorizable, semantic, source_url
"""
import os

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizableTextQuery, VectorizedQuery
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)

SEARCH_ENDPOINT = os.environ.get("SEARCH_SERVICE_ENDPOINT", "")
SEARCH_KEY = os.environ.get("SEARCH_ADMIN_KEY", "")
POLICY_INDEX = os.environ.get("SEARCH_INDEX_NAME", "insurance-documents-index")
PRIOR_INDEX = os.environ.get("PRIOR_CLAIMS_INDEX", "prior-claims")
EMBED_MODEL = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
DIMS = 3072
SEMANTIC_CONFIG = "insurance-semantic"
VECTORIZER_NAME = "insurance-vectorizer"

_cred = AzureKeyCredential(SEARCH_KEY)
_emb: OpenAI | None = None
_policy_numbers: dict[str, str] | None = None


def _vectorizer_resource_url() -> str:
    """The search service's vectorizer only accepts the *.openai.azure.com host, while
    the Foundry account is configured here as *.cognitiveservices.azure.com. Same
    account, both hosts answer (this is the mapping challenge-1/scripts/
    policiesprocessing.ipynb does in _format_azure_openai_endpoint)."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    if endpoint.endswith(".openai.azure.com"):
        return endpoint
    name = os.environ.get("AZURE_OPENAI_SERVICE_NAME", "")
    if not name and endpoint:
        name = endpoint.split("//")[-1].split(".")[0]
    return f"https://{name}.openai.azure.com"


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
        SimpleField(name="source_url", type=SearchFieldDataType.String),
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
        profiles=[VectorSearchProfile(name="tribunal-profile", algorithm_configuration_name="tribunal-hnsw",
                                      vectorizer_name=VECTORIZER_NAME)],
        vectorizers=[AzureOpenAIVectorizer(
            vectorizer_name=VECTORIZER_NAME,
            parameters=AzureOpenAIVectorizerParameters(
                resource_url=_vectorizer_resource_url(),
                deployment_name=EMBED_MODEL,
                model_name="text-embedding-3-large",
                api_key=os.environ.get("AZURE_OPENAI_KEY"),
            ),
        )],
    )
    semantic = SemanticSearch(configurations=[SemanticConfiguration(
        name=SEMANTIC_CONFIG,
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="title"),
            content_fields=[SemanticField(field_name="content")],
        ),
    )])
    SearchIndexClient(SEARCH_ENDPOINT, _cred).create_or_update_index(
        SearchIndex(name=name, fields=_base_fields() + extra_fields, vector_search=vs, semantic_search=semantic)
    )


def upload(name: str, docs: list[dict]) -> None:
    for d in docs:
        d["content_vector"] = embed(d["title"] + "\n" + d["content"])
    SearchClient(SEARCH_ENDPOINT, name, _cred).upload_documents(docs)


def _rows(results) -> list[dict]:
    out = []
    for r in results:
        d = {k: v for k, v in r.items() if k != "content_vector" and not k.startswith("@")}
        d["score"] = round(r["@search.score"], 4)
        if r.get("@search.reranker_score") is not None:
            d["reranker_score"] = round(r["@search.reranker_score"], 4)
        out.append(d)
    return out


def hybrid(name: str, text: str, top: int = 5, semantic: bool = False, server_side_vector: bool = False) -> list[dict]:
    """Keyword + vector. `semantic=True` adds the L2 semantic reranker (adds
    @search.reranker_score); `server_side_vector=True` sends the raw query text and
    lets the index vectorizer embed it (integrated vectorization) instead of calling
    the embedding model from this process."""
    if server_side_vector:
        vq = VectorizableTextQuery(text=text, k_nearest_neighbors=top, fields="content_vector")
    else:
        vq = VectorizedQuery(vector=embed(text), k_nearest_neighbors=top, fields="content_vector")
    kwargs = {}
    if semantic:
        kwargs = {"query_type": "semantic", "semantic_configuration_name": SEMANTIC_CONFIG}
    results = SearchClient(SEARCH_ENDPOINT, name, _cred).search(
        search_text=text, vector_queries=[vq], top=top, **kwargs)
    return _rows(results)


def _known_policy_numbers() -> dict[str, str]:
    """{normalised policy code -> exact policy_number in the index}. One query, cached:
    the policy corpus is five documents, so this is the cheapest way to turn a garbled
    OCR policy code ("COMM AUTO 001") into an exact filter anchor."""
    global _policy_numbers
    if _policy_numbers is None:
        _policy_numbers = {}
        rows = SearchClient(SEARCH_ENDPOINT, POLICY_INDEX, _cred).search(
            search_text="*", top=1000, select=["policy_number"])
        for r in rows:
            pn = (r.get("policy_number") or "").strip()
            if pn:
                _policy_numbers[_norm_pn(pn)] = pn
    return _policy_numbers


def _norm_pn(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _resolve_policy_number(policy_number: str) -> str:
    """Exact index policy_number for a claim's (possibly OCR-garbled) code, or ""."""
    key = _norm_pn(policy_number or "")
    if not key:
        return ""
    try:
        return _known_policy_numbers().get(key, "")
    except Exception as exc:  # ponytail: anchor is an optimisation, ranking still works
        print(f"[search] policy_number lookup failed, ranking instead: {exc}")
        return ""


def _chunks_for(field: str, value: str, anchor: dict | None) -> list[dict]:
    literal = str(value).replace("'", "''")  # OData literal escaping
    chunks = SearchClient(SEARCH_ENDPOINT, POLICY_INDEX, _cred).search(
        search_text="*", filter=f"{field} eq '{literal}'", top=100,
        select=["id", "title", "content", "file_name", "policy_number", "source_url"])
    out = [dict(c) for c in chunks]
    out.sort(key=lambda c: int(c["id"].rsplit("-", 1)[1]))
    for c in out:
        c["score"] = anchor["score"] if anchor else 1.0
        if anchor and "reranker_score" in anchor:
            # document-level, not per-chunk: every chunk of the winning document shares it
            c["doc_reranker_score"] = anchor["reranker_score"]
    return out


def search_policies(policy_number: str, claim_text: str) -> list[dict]:
    """Return every chunk of the one policy document that governs this claim, in order:
    policies are 6-10 KB, so the analyst reads the whole contract (deductibles and
    exclusions live in sections a top-k query tends to skip).

    Document selection is deterministic whenever the claim carries a policy number:
    the code is resolved against the index's `policy_number` values (tolerating OCR
    spacing/punctuation) and the chunks are pulled by exact filter, so a claim can
    never be judged against another customer's contract. Only when the code is missing
    or unrecognised does it fall back to ranking the corpus by claim text (keyword +
    vector + semantic reranker), which picks a plausible document but cannot reliably
    tell the five auto policies apart from damage wording alone."""
    exact = _resolve_policy_number(policy_number)
    if exact:
        chunks = _chunks_for("policy_number", exact, None)
        if chunks:
            return chunks
        print(f"[search] policy_number {exact!r} matched no chunks, ranking instead")
    else:
        print(f"[search] no usable policy_number ({policy_number!r}); "
              f"ranking policies by claim text - document selection is best-effort")

    query = f"{policy_number} {claim_text}"
    try:
        hits = hybrid(POLICY_INDEX, query, top=3, semantic=True)
    except Exception as exc:  # ponytail: semantic ranker is optional, retrieval is not
        print(f"[search] semantic rerank unavailable, falling back to hybrid: {exc}")
        hits = hybrid(POLICY_INDEX, query, top=3)
    if not hits:
        return []
    return _chunks_for("file_name", hits[0].get("file_name", ""), hits[0])


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


def _proof() -> None:
    idx = SearchIndexClient(SEARCH_ENDPOINT, _cred).get_index(POLICY_INDEX)
    print(f"index {idx.name}: vectorizers={[v.vectorizer_name for v in idx.vector_search.vectorizers]} "
          f"resource_url={idx.vector_search.vectorizers[0].parameters.resource_url} "
          f"deployment={idx.vector_search.vectorizers[0].parameters.deployment_name} "
          f"semantic={[c.name for c in idx.semantic_search.configurations]}")

    q = "what is the collision deductible and are rental cars covered"
    print(f"\n[1] VectorizableTextQuery (integrated vectorization, no client-side embed) q={q!r}")
    for h in hybrid(POLICY_INDEX, q, top=3, server_side_vector=True):
        print(f"    score={h['score']:<8} {h['title']}")

    print(f"\n[2] semantic query_type={SEMANTIC_CONFIG} (reranker scores) q={q!r}")
    for h in hybrid(POLICY_INDEX, q, top=3, semantic=True, server_side_vector=True):
        print(f"    score={h['score']:<8} reranker={h.get('reranker_score')} {h['title']}")

    print("\n[3] source_url + deterministic policy_number anchor (search_policies)")
    for pn in ("COMP-AUTO-001", "comm auto 001", ""):
        rows = search_policies(pn, "rear bumper damage after a collision")
        picked = sorted({r.get("file_name") for r in rows})
        print(f"    policy_number={pn!r:<18} -> {len(rows)} chunks {picked} "
              f"doc_reranker_score={rows[0].get('doc_reranker_score') if rows else None}")
        if rows:
            print(f"      source_url={rows[0].get('source_url')}")


if __name__ == "__main__":
    _proof()
