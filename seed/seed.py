"""Seed both banks into one Firestore (emulator or real).

Bank A (payer): Sunita Rao with 90 days of ordinary history, her trusted contact,
and 12 other customers who each paid rakesh.kumar@bankb for the first time today.
Bank B (receiver): Rakesh Kumar's 9-day-old account with 12 first-time inflows
swept out within 20 minutes, plus three ordinary accounts.

Run:  uv run --project services/bank python seed/seed.py
Env:  GOOGLE_CLOUD_PROJECT, FIRESTORE_EMULATOR_HOST (for local)
"""
import os
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "bank"))
os.environ.setdefault("BANK_ID", "A")

from app import config, db, receiver  # noqa: E402

random.seed(7)
NOW = db.now()


def wipe(bank: str):
    for name in ["customers", "payees", "transactions", "payee_reputation", "payee_blocklist",
                 "cases", "events", "accounts", "holds"]:
        for d in db.col(name, bank).stream():
            db.client().recursive_delete(d.reference)
    print(f"  wiped bank {bank}")


def tx(bank: str, customer_id: str, vpa: str, amount: float, at, status="completed"):
    db.col("transactions", bank).add({"customer_id": customer_id, "payee_vpa": vpa, "amount": amount,
                                      "at": at, "status": status})


def payee(bank: str, customer_id: str, vpa: str, name: str, added_at):
    db.col("payees", bank).document(f"{customer_id}__{config.vpa_doc_id(vpa)}").set(
        {"customer_id": customer_id, "vpa": vpa, "name": name, "added_at": added_at})


def seed_bank_a():
    A = "A"
    db.col("customers", A).document("cust_sunita").set({
        "name": "Sunita Rao", "vpa": "sunita@banka", "city": "Pune", "language": "English",
        "customer_since": NOW - timedelta(days=365 * 11), "device_id": "pixel-7a-sunita",
        "trusted_contact": {"name": "Priya Rao", "relation": "daughter", "phone": "+91 98XXX XXX21"},
    })
    payees_a = {
        "pune.dairy@bankb": "Pune Dairy", "more.pune@banka": "More Supermarket", "msedcl@banka": "MSEDCL Electricity",
        "apollo.pune@banka": "Apollo Pharmacy", "lata.help@banka": "Lata (house help)", "ruby.hall@banka": "Ruby Hall Clinic",
    }
    for vpa, name in payees_a.items():
        payee(A, "cust_sunita", vpa, name, NOW - timedelta(days=random.randint(200, 2000)))
        db.col("payee_reputation", A).document(config.vpa_doc_id(vpa)).set(
            {"vpa": vpa, "first_seen": NOW - timedelta(days=random.randint(400, 3000))})

    for day in range(1, 91):
        at = NOW - timedelta(days=day, hours=random.randint(6, 9))
        tx(A, "cust_sunita", "pune.dairy@bankb", 80.0, at)                      # daily milk
        if day % 7 == 3:
            tx(A, "cust_sunita", "more.pune@banka", float(random.randint(1800, 3400)), at)
        if day % 30 == 5:
            tx(A, "cust_sunita", "msedcl@banka", 1900.0, at)
            tx(A, "cust_sunita", "lata.help@banka", 6000.0, at)
        if day % 15 == 2:
            tx(A, "cust_sunita", "apollo.pune@banka", float(random.randint(600, 1400)), at)
    tx(A, "cust_sunita", "ruby.hall@banka", 22000.0, NOW - timedelta(days=40, hours=5))   # the 90-day max

    # Fan-in: 12 customers who had never paid Rakesh Kumar, all paying him today.
    db.col("payee_reputation", A).document(config.vpa_doc_id("rakesh.kumar@bankb")).set(
        {"vpa": "rakesh.kumar@bankb", "first_seen": NOW - timedelta(hours=26)})
    first_names = ["Anil", "Meena", "Rajesh", "Kavita", "Suresh", "Geeta", "Mohan", "Asha", "Vikram", "Nirmala",
                   "Prakash", "Sudha"]
    for i, fn in enumerate(first_names, start=1):
        cid = f"cust_fan_{i:02d}"
        db.col("customers", A).document(cid).set({
            "name": f"{fn} Deshmukh", "vpa": f"{fn.lower()}.d@banka", "city": "Pune", "language": "English",
            "customer_since": NOW - timedelta(days=random.randint(300, 4000)),
            "trusted_contact": {"name": "Family", "phone": "+91 9XXXX XXXXX"}})
        for day in range(2, 60, random.randint(3, 6)):
            tx(A, cid, "more.pune@banka", float(random.randint(400, 2600)), NOW - timedelta(days=day))
        tx(A, cid, "rakesh.kumar@bankb", float(random.choice([45000, 60000, 85000, 120000, 150000, 180000])),
           NOW - timedelta(hours=random.randint(1, 20), minutes=random.randint(0, 59)))
    print("  bank A: Sunita + 12 fan-in customers seeded")


def seed_bank_b():
    B = "B"
    accounts = [
        {"vpa": "rakesh.kumar@bankb", "name": "Rakesh Kumar", "opened_at": NOW - timedelta(days=9), "kyc_level": "minimal",
         "first_time_inflows_24h": 12, "median_minutes_to_sweep": 18, "balance": 1250.0},
        {"vpa": "pune.dairy@bankb", "name": "Pune Dairy", "opened_at": NOW - timedelta(days=365 * 6), "kyc_level": "full",
         "first_time_inflows_24h": 1, "median_minutes_to_sweep": None, "balance": 284300.0},
        {"vpa": "meera.traders@bankb", "name": "Meera Traders", "opened_at": NOW - timedelta(days=20), "kyc_level": "full",
         "first_time_inflows_24h": 5, "median_minutes_to_sweep": 100, "balance": 56000.0},
        {"vpa": "arjun.s@bankb", "name": "Arjun Shinde", "opened_at": NOW - timedelta(days=800), "kyc_level": "full",
         "first_time_inflows_24h": 0, "median_minutes_to_sweep": None, "balance": 91000.0},
    ]
    for a in accounts:
        prob, band, feats = receiver.mule_score(a)
        db.col("accounts", B).document(config.vpa_doc_id(a["vpa"])).set(
            {**a, "mule_score": prob, "band": band, "features": feats, "scored_at": NOW})
        print(f"  bank B: {a['vpa']:<24} score {prob:.3f} band {band}")
    ref = db.col("accounts", B).document(config.vpa_doc_id("rakesh.kumar@bankb"))
    for i in range(12):
        at = NOW - timedelta(hours=random.randint(1, 20))
        ref.collection("inflows").add({"amount": float(random.choice([45000, 60000, 85000, 120000])), "from_bank": "A",
                                       "ref": f"seed_{i}", "at": at, "swept_out_minutes": random.randint(9, 28)})


if __name__ == "__main__":
    print(f"seeding project {config.PROJECT} "
          f"({'emulator ' + os.environ['FIRESTORE_EMULATOR_HOST'] if os.environ.get('FIRESTORE_EMULATOR_HOST') else 'LIVE'})")
    wipe("A")
    wipe("B")
    seed_bank_a()
    seed_bank_b()
    print("done")
