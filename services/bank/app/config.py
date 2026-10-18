import os

BANK_ID = os.environ.get("BANK_ID", "A").upper()
BANK_NAME = os.environ.get("BANK_NAME", f"Bank {BANK_ID}")
PEER_BANK_ID = os.environ.get("PEER_BANK_ID", "B" if BANK_ID == "A" else "A")
PEER_BANK_URL = os.environ.get("PEER_BANK_URL", "").rstrip("/")
GEMINI_MODE = os.environ.get("GEMINI_MODE", "stub")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_LOCATION = os.environ.get("GEMINI_LOCATION", "us-central1")
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "second-voice-demo")
COOLING_OFF_SECONDS = int(os.environ.get("COOLING_OFF_SECONDS", "10"))
HOLD_HOURS = int(os.environ.get("HOLD_HOURS", "24"))

# VPA suffix -> bank id. The mock world has two banks.
VPA_BANKS = {"banka": "A", "bankb": "B"}


def bank_of_vpa(vpa: str) -> str | None:
    handle = vpa.split("@")[-1].lower() if "@" in vpa else ""
    return VPA_BANKS.get(handle)


def vpa_doc_id(vpa: str) -> str:
    return vpa.lower().replace("@", "_at_").replace(".", "_")
