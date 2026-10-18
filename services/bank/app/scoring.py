"""Payer-side coercion score.

A weighted logistic model over a handful of interpretable features. Weights are
hand-set for the prototype and documented here so the pitch can say exactly
what the score is. Tune SCALE and BIAS to move the tiers; the demo scenario
(Sunita, digital arrest) lands around 0.91 and the milk-vendor payment around
0.18.
"""
import math
from datetime import timedelta

from . import config, db
from .models import DeviceContext

WEIGHTS = {
    "is_new_payee": 1.2,
    "payee_added_recently": 0.8,
    "amount_ratio_log": 0.5,       # per log2 of amount / 90-day max, capped at 4
    "active_call": 1.5,
    "remote_access_app": 1.3,
    "rapid_transfers": 0.6,        # 2+ transfers in the last hour
    "fan_in": 1.2,                 # first-time payers to this VPA in 24 h, /12 capped at 1
    "band_elevated": 0.8,
    "band_high": 1.6,
    "registry_hit": 0.7,
}
BIAS = -4.0
SCALE = 0.383

TIERS = [(0.30, "low"), (0.60, "medium"), (0.95, "high"), (1.01, "very_high")]


def tier_for(score: float, band: str | None) -> str:
    for cut, name in TIERS:
        if score < cut:
            if name == "very_high" and band != "High":
                return "high"
            return name
    return "very_high"


def _history(customer_id: str, days: int = 90) -> list[dict]:
    since = db.now() - timedelta(days=days)
    q = (db.col("transactions").where("customer_id", "==", customer_id)
         .where("at", ">=", since))
    return [d.to_dict() for d in q.stream()]


def _fan_in(payee_vpa: str, hours: int = 24) -> dict:
    """How many of OUR customers paid this VPA in the window, and how many of
    them had never paid it before. Computed from our own book only."""
    since = db.now() - timedelta(hours=hours)
    recent = [d.to_dict() for d in db.col("transactions")
              .where("payee_vpa", "==", payee_vpa).where("at", ">=", since).stream()]
    payers = {t["customer_id"] for t in recent}
    first_time = 0
    for cid in payers:
        earlier = (db.col("transactions").where("customer_id", "==", cid)
                   .where("payee_vpa", "==", payee_vpa).where("at", "<", since).limit(1).stream())
        if not list(earlier):
            first_time += 1
    all_time = db.col("payee_reputation").document(config.vpa_doc_id(payee_vpa)).get()
    first_seen = all_time.to_dict().get("first_seen") if all_time.exists else None
    return {"payers_24h": len(payers), "first_time_payers_24h": first_time, "first_seen": first_seen}


def features(customer_id: str, payee_vpa: str, amount: float, device: DeviceContext,
             receiver_band: str | None, registry_hit: bool) -> dict:
    hist = _history(customer_id)
    to_payee = [t for t in hist if t["payee_vpa"] == payee_vpa]
    hist_max = max((t["amount"] for t in hist), default=0.0)
    last_hour = db.now() - timedelta(hours=1)
    fan = _fan_in(payee_vpa)
    return {
        "is_new_payee": len(to_payee) == 0,
        "payee_added_minutes_ago": device.payee_added_minutes_ago,
        "amount": amount,
        "history_max_90d": hist_max,
        "amount_ratio": amount / hist_max if hist_max else float("inf"),
        "active_call_minutes": device.active_call_minutes,
        "remote_access_app_installed_minutes_ago": device.remote_access_app_installed_minutes_ago,
        "transfers_last_hour": sum(1 for t in hist if t["at"] >= last_hour),
        "fan_in_payers_24h": fan["payers_24h"],
        "fan_in_first_time_payers_24h": fan["first_time_payers_24h"],
        "payee_first_seen": fan["first_seen"],
        "receiver_band": receiver_band,
        "registry_hit": registry_hit,
    }


def score(f: dict) -> tuple[float, list[dict]]:
    """Returns (probability, signal chips). Each chip says whether it fired and
    how much it contributed, so the console can show the reasoning."""
    chips = []
    logit = BIAS

    def add(key, fired, weight, label):
        nonlocal logit
        if fired:
            logit += weight
        chips.append({"key": key, "label": label, "fired": bool(fired), "weight": round(weight if fired else 0, 2)})

    add("new_payee", f["is_new_payee"], WEIGHTS["is_new_payee"], "first payment to this payee")
    recently = f["payee_added_minutes_ago"] is not None and f["payee_added_minutes_ago"] <= 10
    add("payee_added_recently", recently, WEIGHTS["payee_added_recently"],
        f"payee added {f['payee_added_minutes_ago']} min ago" if recently else "payee not newly added")
    ratio = f["amount_ratio"]
    if math.isinf(ratio):
        ratio_log = 4.0
    else:
        ratio_log = min(max(math.log2(ratio), 0.0), 4.0) if ratio > 1 else 0.0
    add("amount_ratio", ratio_log > 0, WEIGHTS["amount_ratio_log"] * ratio_log,
        f"amount {ratio:.0f}x her 90-day max" if ratio_log > 0 and not math.isinf(ratio) else "amount within normal range")
    add("active_call", f["active_call_minutes"] > 0, WEIGHTS["active_call"],
        f"call active {f['active_call_minutes']} min" if f["active_call_minutes"] else "no active call")
    remote = f["remote_access_app_installed_minutes_ago"]
    add("remote_access_app", remote is not None, WEIGHTS["remote_access_app"],
        f"screen-share app installed {remote} min ago" if remote is not None else "no remote-access app")
    add("rapid_transfers", f["transfers_last_hour"] >= 2, WEIGHTS["rapid_transfers"],
        f"{f['transfers_last_hour']} transfers in the last hour")
    fi = f["fan_in_first_time_payers_24h"]
    add("fan_in", fi > 0, WEIGHTS["fan_in"] * min(fi, 12) / 12,
        f"{fi} of our customers paid it today, all first-time" if fi else "no fan-in on our book")
    band = f["receiver_band"]
    add("receiver_band", band in ("Elevated", "High"),
        WEIGHTS["band_high"] if band == "High" else WEIGHTS["band_elevated"],
        f"receiver band: {band or 'unknown'}")
    add("registry_hit", f["registry_hit"], WEIGHTS["registry_hit"], "registry hit: payee mobile flagged (stubbed)")

    prob = 1 / (1 + math.exp(-SCALE * logit))
    return round(prob, 3), chips
