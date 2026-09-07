"""Upload the Challenge 0 source documents (policies, handwritten statements, damage
photos) to Blob Storage so Azure AI Search indexes point at a real document store.

    python -m tribunal.blob_upload            # upload everything, print blob URLs

Idempotent: every blob is written with overwrite=True, so re-running is safe.
`blob_url()` is imported by tribunal.seed to stamp `source_url` on each policy chunk.
"""
import glob
import os
import sys

from azure.storage.blob import BlobServiceClient, ContentSettings
from dotenv import load_dotenv

from .workflow import REPO

load_dotenv(override=True)

CONTAINER = os.environ.get("BLOB_CONTAINER", "claims-data")
DATA = os.path.join(REPO, "challenge-0", "data")
CONN = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")


def _account_name() -> str:
    """AZURE_STORAGE_ACCOUNT_NAME if set, else parse AccountName= out of the connection
    string, so blob_url() can never emit a hostname with an empty account."""
    name = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "").strip()
    if name:
        return name
    for part in CONN.split(";"):
        if part.strip().lower().startswith("accountname="):
            return part.split("=", 1)[1].strip()
    raise RuntimeError("no AZURE_STORAGE_ACCOUNT_NAME and no AccountName= in AZURE_STORAGE_CONNECTION_STRING")


# blob name prefix -> glob of local files
SOURCES = {
    "policies": os.path.join(DATA, "policies", "*.md"),
    "statements": os.path.join(DATA, "statements", "*.jpeg"),
    "images": os.path.join(DATA, "images", "*.jpg"),
}
CONTENT_TYPES = {".md": "text/markdown", ".jpeg": "image/jpeg", ".jpg": "image/jpeg"}


def blob_url(prefix: str, file_name: str) -> str:
    """Deterministic blob URL for a source document; no network call, so tribunal.seed
    can stamp `source_url` on a chunk whether or not the upload ran in this session."""
    return f"https://{_account_name()}.blob.core.windows.net/{CONTAINER}/{prefix}/{file_name}"


def _service() -> BlobServiceClient:
    if not CONN:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is empty")
    return BlobServiceClient.from_connection_string(CONN)


def upload_all(verbose: bool = True) -> list[str]:
    svc = _service()
    try:
        svc.create_container(CONTAINER)
    except Exception as exc:  # ponytail: container already exists is the normal path
        if "ContainerAlreadyExists" not in str(exc):
            raise
    urls = []
    for prefix, pattern in SOURCES.items():
        for path in sorted(glob.glob(pattern)):
            name = os.path.basename(path)
            ct = CONTENT_TYPES.get(os.path.splitext(name)[1], "application/octet-stream")
            with open(path, "rb") as fh:
                svc.get_blob_client(CONTAINER, f"{prefix}/{name}").upload_blob(
                    fh, overwrite=True, content_settings=ContentSettings(content_type=ct)
                )
            url = blob_url(prefix, name)
            urls.append(url)
            if verbose:
                print(url)
    if verbose:
        print(f"uploaded {len(urls)} blobs -> container '{CONTAINER}'")
    return urls


if __name__ == "__main__":
    upload_all()
    sys.exit(0)
