"""Intervention flow on top of a case document.

Case lifecycle: open -> (released | held) -> held -> (cancelled | overridden | released)
Every turn is a document in cases/{id}/turns so the phone and the console both
render it from a Firestore listener."""
from datetime import timedelta

import httpx

from . import config, db, gemini


def _turns(case_ref) -> list[dict]:
    return [t.to_dict() for t in case_ref.collection("turns").order_by("n").stream()]


def _add_turn(case_ref, n: int, role: str, text: str, options: list[dict] | None = None, reason: str | None = None):
    case_ref.collection("turns").document(f"{n:03d}").set(
        {"n": n, "role": role, "text": text, "options": options or [], "reason": reason, "at": db.now()})


def start(case_ref, case: dict) -> dict:
    turn = gemini.opening_turn(case)
    _add_turn(case_ref, 1, "agent", turn["message"], turn["options"], turn["reason"])
    case_ref.set({"status": "held", "stage": "questioning", "updated_at": db.now()}, merge=True)
    db.event("intervention_started", {"case_id": case_ref.id, "tier": case["tier"]})
    return turn


def answer(case_ref, text: str | None, option: str | None) -> dict:
    case = case_ref.get().to_dict()
    turns = _turns(case_ref)
    n = len(turns) + 1
    customer_input = option or text or ""
    label = next((o["label"] for t in turns for o in t.get("options", []) if o["id"] == option), None) if option else None
    _add_turn(case_ref, n, "customer", label or customer_input)

    # Fixed-meaning options bypass the model.
    if option in ("trusted_contact", "cancel", "talk_to_bank", "proceed_anyway"):
        return act(case_ref, {"trusted_contact": "trusted_contact", "cancel": "cancel",
                              "talk_to_bank": "talk_to_bank", "proceed_anyway": "override"}[option])

    turn = gemini.next_turn(case, turns, customer_input)
    _add_turn(case_ref, n + 1, "agent", turn["message"], turn.get("options", []), turn.get("reason"))
    new_score = round(min(max(case["score"] + float(turn.get("coercion_delta", 0)), 0.0), 0.999), 3)
    update = {"score_after_conversation": new_score, "updated_at": db.now()}
    if turn["assessment"] == "release":
        update.update(status="released", decision="released_after_conversation")
        db.event("payment_released", {"case_id": case_ref.id})
    elif turn["assessment"] == "hold":
        update.update(stage="resolving", hold_started_at=db.now())
    elif turn["assessment"] == "cancel":
        update.update(status="cancelled", decision="cancelled_by_customer")
    case_ref.set(update, merge=True)
    return {"turn": turn, "status": update.get("status", case["status"])}


def act(case_ref, action: str) -> dict:
    case = case_ref.get().to_dict()
    n = len(_turns(case_ref)) + 1
    if action == "trusted_contact":
        tc = case.get("trusted_contact", {})
        msg = (f"Calling {tc.get('name', 'your trusted contact')} now. The payment stays paused for "
               f"{config.HOLD_HOURS} hours. Nothing will leave your account until you and {tc.get('name', 'they')} "
               f"have spoken.")
        _add_turn(case_ref, n, "agent", msg, [{"id": "cancel", "label": "Cancel the payment"}, {"id": "proceed_anyway", "label": "Proceed anyway"}], "trusted contact called")
        case_ref.set({"trusted_contact_called": True, "trusted_contact_called_at": db.now(), "status": "held",
                      "hold_until": db.now() + timedelta(hours=config.HOLD_HOURS), "updated_at": db.now()}, merge=True)
        db.event("trusted_contact_called", {"case_id": case_ref.id, "contact": tc.get("name")})
        return {"status": "held", "message": msg}
    if action == "talk_to_bank":
        msg = "Connecting you to the bank's fraud desk. The payment stays paused."
        _add_turn(case_ref, n, "agent", msg, [{"id": "cancel", "label": "Cancel the payment"}], "escalated to bank")
        case_ref.set({"status": "held", "escalated": True, "updated_at": db.now()}, merge=True)
        return {"status": "held", "message": msg}
    if action == "cancel":
        msg = "Cancelled. Nothing has left your account. If someone is still on the call, it is safe to hang up."
        _add_turn(case_ref, n, "agent", msg, [], "customer cancelled")
        case_ref.set({"status": "cancelled", "decision": "cancelled_by_customer", "updated_at": db.now()}, merge=True)
        db.event("payment_cancelled", {"case_id": case_ref.id})
        _finish(case_ref)
        return {"status": "cancelled", "message": msg}
    if action == "override":
        started = case.get("hold_started_at") or case.get("created_at")
        waited = (db.now() - started).total_seconds()
        if waited < config.COOLING_OFF_SECONDS:
            left = int(config.COOLING_OFF_SECONDS - waited)
            msg = f"You can proceed in {left} seconds. Please use that time to call someone you trust."
            _add_turn(case_ref, n, "agent", msg, [{"id": "cancel", "label": "Cancel the payment"},
                                                 {"id": "proceed_anyway", "label": "Proceed anyway"}], "cooling-off")
            return {"status": "held", "message": msg, "cooling_off_seconds_left": left}
        result = _send_credit(case_ref.id, case)
        msg = ("Payment sent. The receiving bank has placed it on hold for review." if result.get("status") == "held"
               else "Payment sent against our advice. You can report it within the hour from the app.")
        _add_turn(case_ref, n, "agent", msg, [], "customer overrode the hold")
        case_ref.set({"status": "overridden", "decision": "overridden_by_customer", "credit_result": result,
                      "updated_at": db.now()}, merge=True)
        db.event("hold_overridden", {"case_id": case_ref.id, "credit_result": result})
        _finish(case_ref)
        return {"status": "overridden", "message": msg, "credit_result": result}
    if action == "release":
        case_ref.set({"status": "released", "decision": "released_by_ops", "updated_at": db.now()}, merge=True)
        _finish(case_ref)
        return {"status": "released"}
    return {"error": f"unknown action {action}"}


def _send_credit(case_id: str, case: dict) -> dict:
    """The debit went through. Deliver the credit to the receiving bank, which
    may hold it. In production this is the UPI leg; here it is a direct call."""
    payload = {"vpa": case["payee_vpa"], "amount": case["amount"], "from_bank": config.BANK_ID, "ref": case_id}
    if config.bank_of_vpa(case["payee_vpa"]) == config.BANK_ID:
        from . import receiver
        return receiver.credit(**payload)
    if not config.PEER_BANK_URL:
        return {"status": "posted", "note": "no peer bank configured"}
    try:
        r = httpx.post(f"{config.PEER_BANK_URL}/credit", json=payload, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def _finish(case_ref):
    case = case_ref.get().to_dict()
    summary = gemini.summarize(case, _turns(case_ref))
    case_ref.set({"summary": summary}, merge=True)
