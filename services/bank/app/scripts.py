"""Scam script library and the rule-based fallback classifier.
Gemini does the classification in vertex mode; these rules run in stub mode and
as a safety net when the model returns something unusable."""
import json
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "prompts" / "scam_scripts.json"
SCRIPTS: dict[str, dict] = {s["id"]: s for s in json.loads(_PATH.read_text())["scripts"]}


def rule_based_guess(f: dict) -> dict:
    """f is the feature dict from scoring.features()."""
    on_call = f["active_call_minutes"] > 0
    remote = f["remote_access_app_installed_minutes_ago"] is not None
    big = f["amount_ratio"] >= 3
    if on_call and (remote or big) and f["is_new_payee"]:
        return {"script_id": "digital_arrest", "confidence": 0.8,
                "reason": "new payee, large amount, during an active call, remote-access app present"}
    if f["transfers_last_hour"] >= 2 and not on_call:
        return {"script_id": "investment_group", "confidence": 0.5,
                "reason": "repeated transfers to the same new payee without a call"}
    if on_call and f["is_new_payee"]:
        return {"script_id": "parcel_customs", "confidence": 0.4,
                "reason": "new payee during an active call"}
    return {"script_id": "none", "confidence": 0.0, "reason": "no scam pattern"}
