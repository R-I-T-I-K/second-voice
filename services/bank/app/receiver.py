"""Receiver-side mule score. Runs on the bank's OWN accounts, where it can see
what the payer bank never can: account age, KYC depth, inflow fan-in and how
fast money leaves. Emits a band that is shared with the payer bank."""
import math
from datetime import timedelta

from . import config, db

BANDS = [(0.40, "Low"), (0.75, "Elevated"), (1.01, "High")]


def mule_score(acct: dict) -> tuple[float, str, dict]:
    age_days = max((db.now() - acct["opened_at"]).days, 0)
    kyc_full = acct.get("kyc_level") == "full"
    fi = acct.get("first_time_inflows_24h", 0)
    sweep = acct.get("median_minutes_to_sweep")  # None if money tends to stay

    logit = -3.0
    parts = {}
    parts["young_account"] = 1.4 if age_days < 30 else (0.6 if age_days < 180 else 0.0)
    parts["thin_kyc"] = 0.0 if kyc_full else 0.9
    parts["fan_in"] = 1.6 * min(fi, 12) / 12
    parts["fast_sweep"] = 0.0 if sweep is None else (1.5 if sweep <= 30 else (0.7 if sweep <= 180 else 0.0))
    logit += sum(parts.values())
    prob = round(1 / (1 + math.exp(-logit)), 3)
    band = next(name for cut, name in BANDS if prob < cut)
    return prob, band, {"account_age_days": age_days, "kyc_level": acct.get("kyc_level"),
                        "first_time_inflows_24h": fi, "median_minutes_to_sweep": sweep, **parts}


def resolve(vpa: str) -> dict:
    """Address validation answer: name plus band. This is the one extra field
    the pitch proposes adding to the existing UPI name lookup."""
    doc = db.col("accounts").document(config.vpa_doc_id(vpa)).get()
    if not doc.exists:
        return {"vpa": vpa, "name": None, "bank": config.BANK_ID, "band": None, "found": False}
    acct = doc.to_dict()
    prob, band, feats = mule_score(acct)
    doc.reference.set({"mule_score": prob, "band": band, "features": feats, "scored_at": db.now()}, merge=True)
    return {"vpa": vpa, "name": acct["name"], "bank": config.BANK_ID, "band": band, "found": True}


def credit(vpa: str, amount: float, from_bank: str, ref: str) -> dict:
    """A credit arrived. Record it, rescore, and hold it if the band is High."""
    ref_doc = db.col("accounts").document(config.vpa_doc_id(vpa))
    doc = ref_doc.get()
    if not doc.exists:
        return {"status": "rejected", "reason": "no such account"}
    acct = doc.to_dict()
    ref_doc.collection("inflows").add({"amount": amount, "from_bank": from_bank, "ref": ref, "at": db.now()})
    acct["first_time_inflows_24h"] = acct.get("first_time_inflows_24h", 0) + 1
    prob, band, feats = mule_score(acct)
    ref_doc.set({"first_time_inflows_24h": acct["first_time_inflows_24h"], "mule_score": prob,
                 "band": band, "features": feats, "scored_at": db.now()}, merge=True)
    if band == "High":
        release_at = db.now() + timedelta(hours=config.HOLD_HOURS)
        hold = {"vpa": vpa, "name": acct["name"], "amount": amount, "from_bank": from_bank, "ref": ref,
                "band": band, "mule_score": prob, "status": "held", "created_at": db.now(), "release_at": release_at,
                "reason": f"High-band account: {feats['first_time_inflows_24h']} first-time inflows in 24 h, "
                          f"opened {feats['account_age_days']} days ago"}
        _, hold_ref = db.col("holds").add(hold)
        db.event("credit_held", {"vpa": vpa, "amount": amount, "hold_id": hold_ref.id})
        return {"status": "held", "hold_id": hold_ref.id, "release_at": release_at.isoformat(), "band": band}
    db.event("credit_posted", {"vpa": vpa, "amount": amount})
    return {"status": "posted", "band": band}
