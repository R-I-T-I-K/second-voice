import logging
from datetime import timedelta

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import config, conversation, db, gemini, receiver, scoring
from .models import (ActionRequest, AnswerRequest, CreditRequest, OutcomeRequest, PayRequest, ValidateRequest)

logging.basicConfig(level=logging.INFO)
app = FastAPI(title=f"Second Voice {config.BANK_NAME}", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    try:
        db.bank_ref().get()
        fs = "ok"
    except Exception as e:  # noqa: BLE001
        fs = f"error: {e}"
    return {"bank": config.BANK_ID, "name": config.BANK_NAME, "gemini_mode": config.GEMINI_MODE,
            "firestore": fs, "peer": config.PEER_BANK_URL or None}


@app.get("/customers")
def customers():
    return [{"id": d.id, **d.to_dict()} for d in db.col("customers").stream()]


@app.get("/scenarios")
def scenarios():
    """Presets for the mock app so a judge can reproduce the video in a minute."""
    return [
        {"id": "digital_arrest", "name": "Digital arrest", "customer_id": "cust_sunita",
         "payee_vpa": "rakesh.kumar@bankb", "payee_name": "Rakesh Kumar", "amount": 240000,
         "device": {"active_call_minutes": 23, "remote_access_app_installed_minutes_ago": 40,
                    "payee_added_minutes_ago": 2, "seconds_on_pay_screen": 95}, "registry_hit": True,
         "blurb": "Sunita, 58, is on a video call with a 'CBI officer'. Her Aadhaar was 'used for money laundering'. "
                  "She must move her savings to a verification account and tell no one."},
        {"id": "normal", "name": "Normal payment", "customer_id": "cust_sunita",
         "payee_vpa": "pune.dairy@bankb", "payee_name": "Pune Dairy", "amount": 2400,
         "device": {"active_call_minutes": 0, "remote_access_app_installed_minutes_ago": None,
                    "payee_added_minutes_ago": None, "seconds_on_pay_screen": 12}, "registry_hit": False,
         "blurb": "Sunita pays her regular milk vendor. Nothing should interrupt this."},
        {"id": "custom", "name": "Custom", "customer_id": "cust_sunita", "payee_vpa": "rakesh.kumar@bankb",
         "payee_name": "Rakesh Kumar", "amount": 15000,
         "device": {"active_call_minutes": 0, "remote_access_app_installed_minutes_ago": None,
                    "payee_added_minutes_ago": 5, "seconds_on_pay_screen": 30}, "registry_hit": False,
         "blurb": "Flip the toggles yourself and watch the score move."},
    ]


# ---------- address validation (the UPI name-lookup leg, plus one field) ----------

@app.post("/validate-address")
def validate_address(req: ValidateRequest):
    bank = config.bank_of_vpa(req.vpa)
    if bank == config.BANK_ID:
        return {**receiver.resolve(req.vpa), "source": "own_book"}
    if not config.PEER_BANK_URL:
        return {"vpa": req.vpa, "name": None, "bank": bank, "band": None, "found": False, "source": "no_peer"}
    try:
        r = httpx.post(f"{config.PEER_BANK_URL}/resolve", json={"vpa": req.vpa}, timeout=5)
        r.raise_for_status()
        return {**r.json(), "source": "peer_bank"}
    except Exception as e:  # noqa: BLE001
        return {"vpa": req.vpa, "name": None, "bank": bank, "band": None, "found": False, "source": f"peer_error: {e}"}


@app.post("/resolve")
def resolve(req: ValidateRequest):
    """Receiver side of address validation."""
    return receiver.resolve(req.vpa)


# ---------- the hook ----------

@app.post("/pay")
def pay(req: PayRequest):
    cust_doc = db.col("customers").document(req.customer_id).get()
    if not cust_doc.exists:
        raise HTTPException(404, "unknown customer")
    cust = cust_doc.to_dict()

    resolved = validate_address(ValidateRequest(vpa=req.payee_vpa))
    payee_name = resolved.get("name") or req.payee_name
    f = scoring.features(req.customer_id, req.payee_vpa, req.amount, req.device, resolved.get("band"), req.registry_hit)
    prob, chips = scoring.score(f)
    tier = scoring.tier_for(prob, resolved.get("band"))
    script = gemini.classify_script(f, chips) if tier != "low" else {"script_id": "none", "confidence": 0, "reason": ""}
    from .scripts import SCRIPTS
    script_doc = {"id": script["script_id"], "name": SCRIPTS.get(script["script_id"], {}).get("name", "none"),
                  "confidence": script.get("confidence", 0), "reason": script.get("reason", "")}

    case = {
        "customer_id": req.customer_id, "customer_name": cust["name"], "language": cust.get("language", "English"),
        "trusted_contact": cust.get("trusted_contact", {}),
        "payee_vpa": req.payee_vpa, "payee_name": payee_name, "payee_bank": resolved.get("bank"),
        "receiver_band": resolved.get("band"), "amount": req.amount,
        "device": req.device.model_dump(), "features": {k: (v if not hasattr(v, "isoformat") else v) for k, v in f.items()},
        "signals": chips, "score": prob, "tier": tier, "script": script_doc,
        "status": "open", "created_at": db.now(), "updated_at": db.now(),
    }
    # inf is not storable
    if case["features"]["amount_ratio"] == float("inf"):
        case["features"]["amount_ratio"] = None
    _, case_ref = db.col("cases").add(case)
    db.event("payment_scored", {"case_id": case_ref.id, "score": prob, "tier": tier, "amount": req.amount,
                                "payee_vpa": req.payee_vpa})

    if tier == "low":
        db.col("transactions").add({"customer_id": req.customer_id, "payee_vpa": req.payee_vpa, "amount": req.amount,
                                    "at": db.now(), "status": "completed", "case_id": case_ref.id})
        credit = conversation._send_credit(case_ref.id, case)
        case_ref.set({"status": "released", "decision": "allowed", "credit_result": credit}, merge=True)
        return {"decision": "allow", "case_id": case_ref.id, "score": prob, "tier": tier, "signals": chips,
                "script": script_doc, "credit_result": credit}

    if tier == "very_high":
        msg = ("We have stopped this payment. The account you are paying has been flagged by the receiving bank, "
               "and the pattern matches a known scam. A fraud specialist will call you within 10 minutes.")
        case_ref.collection("turns").document("001").set({"n": 1, "role": "agent", "text": msg, "options": [
            {"id": "talk_to_bank", "label": "Talk to the bank now"}], "at": db.now(), "reason": "blocked"})
        case_ref.set({"status": "blocked", "decision": "blocked", "updated_at": db.now()}, merge=True)
        db.event("payment_blocked", {"case_id": case_ref.id})
        conversation._finish(case_ref)
        return {"decision": "block", "case_id": case_ref.id, "score": prob, "tier": tier, "signals": chips,
                "script": script_doc, "message": msg}

    turn = conversation.start(case_ref, case)
    return {"decision": "hold", "case_id": case_ref.id, "score": prob, "tier": tier, "signals": chips,
            "script": script_doc, "turn": turn}


@app.post("/cases/{case_id}/answer")
def case_answer(case_id: str, req: AnswerRequest):
    ref = db.col("cases").document(case_id)
    if not ref.get().exists:
        raise HTTPException(404, "no such case")
    return conversation.answer(ref, req.text, req.option)


@app.post("/cases/{case_id}/action")
def case_action(case_id: str, req: ActionRequest):
    ref = db.col("cases").document(case_id)
    if not ref.get().exists:
        raise HTTPException(404, "no such case")
    return conversation.act(ref, req.action)


# ---------- receiver side ----------

@app.post("/credit")
def credit(req: CreditRequest):
    return receiver.credit(req.vpa, req.amount, req.from_bank, req.ref)


# ---------- ops ----------

@app.post("/ops/cases/{case_id}/outcome")
def case_outcome(case_id: str, req: OutcomeRequest):
    ref = db.col("cases").document(case_id)
    case = ref.get()
    if not case.exists:
        raise HTTPException(404, "no such case")
    ref.set({"outcome": req.outcome, "outcome_at": db.now()}, merge=True)
    if req.outcome == "confirmed_scam":
        vpa = case.to_dict()["payee_vpa"]
        db.col("payee_blocklist").document(config.vpa_doc_id(vpa)).set(
            {"vpa": vpa, "case_id": case_id, "added_at": db.now(), "i4c_report": "queued"})
        db.event("payee_blocklisted", {"vpa": vpa, "case_id": case_id})
    return {"ok": True}


@app.post("/ops/holds/{hold_id}/release")
def release_hold(hold_id: str):
    ref = db.col("holds").document(hold_id)
    if not ref.get().exists:
        raise HTTPException(404, "no such hold")
    ref.set({"status": "released", "released_at": db.now()}, merge=True)
    return {"ok": True}


@app.post("/ops/accounts/rescore")
def rescore_accounts():
    out = []
    for d in db.col("accounts").stream():
        prob, band, feats = receiver.mule_score(d.to_dict())
        d.reference.set({"mule_score": prob, "band": band, "features": feats, "scored_at": db.now()}, merge=True)
        out.append({"vpa": d.to_dict()["vpa"], "band": band, "score": prob})
    return out
