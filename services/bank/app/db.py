"""Firestore access. Every collection is scoped under banks/{BANK_ID} so both
deployments share one database in the demo without seeing each other's data."""
from datetime import datetime, timezone

from google.cloud import firestore

from . import config

_client: firestore.Client | None = None


def client() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(project=config.PROJECT)
    return _client


def bank_ref(bank_id: str | None = None):
    return client().collection("banks").document(bank_id or config.BANK_ID)


def col(name: str, bank_id: str | None = None):
    return bank_ref(bank_id).collection(name)


def now() -> datetime:
    return datetime.now(timezone.utc)


def event(kind: str, payload: dict) -> None:
    """Append to the console event stream."""
    col("events").add({"type": kind, "at": now(), "bank": config.BANK_ID, **payload})
