"""Gemini client with a stub mode so the whole flow runs offline.

vertex mode uses google-genai against Vertex AI with application-default
credentials (locally: `gcloud auth application-default login`; on Cloud Run:
the service account). Every call returns parsed JSON or falls back to the
stub so the demo never shows a blank screen."""
import json
import logging
from pathlib import Path

from . import config
from .scripts import SCRIPTS, rule_based_guess

log = logging.getLogger("gemini")
_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
_client = None


def _vertex():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(vertexai=True, project=config.PROJECT, location=config.GEMINI_LOCATION)
    return _client


def _generate_json(system: str, user: str) -> dict | None:
    try:
        from google.genai import types
        resp = _vertex().models.generate_content(
            model=config.GEMINI_MODEL,
            contents=user,
            config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                               temperature=0.4),
        )
        return json.loads(resp.text)
    except Exception as e:  # noqa: BLE001
        log.warning("gemini call failed, falling back to stub: %s", e)
        return None


# ---------- script classification ----------

def classify_script(features: dict, chips: list[dict]) -> dict:
    fallback = rule_based_guess(features)
    if config.GEMINI_MODE != "vertex":
        return fallback
    catalogue = [{"id": s["id"], "name": s["name"], "summary": s["summary"], "markers": s["markers"]}
                 for s in SCRIPTS.values()]
    system = ("You classify a paused UPI payment against a catalogue of scam scripts. Respond ONLY with JSON: "
              '{"script_id": one of the catalogue ids or "none", "confidence": 0..1, "reason": "one line"}')
    user = json.dumps({"catalogue": catalogue, "signals": [c for c in chips if c["fired"]],
                       "features": {k: (str(v) if not isinstance(v, (int, float, bool, type(None))) else v)
                                    for k, v in features.items()}})
    out = _generate_json(system, user)
    if out and out.get("script_id") in SCRIPTS or (out and out.get("script_id") == "none"):
        return out
    return fallback


# ---------- intervention turns ----------

def _system_prompt(case: dict) -> str:
    s = SCRIPTS.get(case.get("script", {}).get("id")) or SCRIPTS["digital_arrest"]
    tpl = (_PROMPTS / "intervention_system.md").read_text()
    return tpl.format(language=case.get("language", "English"), script_name=s["name"], script_summary=s["summary"],
                      coaching_lines=" | ".join(s["coaching_lines"]), facts=" ".join(s["facts"]),
                      signals=", ".join(c["label"] for c in case["signals"] if c["fired"]), score=case["score"])


def opening_turn(case: dict) -> dict:
    s = SCRIPTS.get(case.get("script", {}).get("id")) or SCRIPTS["digital_arrest"]
    return {"message": f"{case['customer_name'].split()[0]}, {s['opening_question'][0].lower()}{s['opening_question'][1:]}",
            "options": s["opening_options"], "assessment": "continue", "coercion_delta": 0.0,
            "reason": "opening question from script"}


def next_turn(case: dict, turns: list[dict], customer_input: str) -> dict:
    if config.GEMINI_MODE == "vertex":
        transcript = [{"role": t["role"], "text": t["text"]} for t in turns] + [{"role": "customer", "text": customer_input}]
        out = _generate_json(_system_prompt(case), json.dumps({"transcript": transcript}))
        if out and "message" in out:
            out.setdefault("options", [])
            out.setdefault("assessment", "continue")
            out.setdefault("coercion_delta", 0.0)
            return out
    return _stub_turn(case, turns, customer_input)


def _stub_turn(case: dict, turns: list[dict], customer_input: str) -> dict:
    """Canned flow: one question, then facts plus resolution options."""
    s = SCRIPTS.get(case.get("script", {}).get("id")) or SCRIPTS["digital_arrest"]
    first = customer_input.strip().lower()
    customer_turns = sum(1 for t in turns if t["role"] == "customer")
    denies = first.startswith("no") or first == "no_known"
    if customer_turns == 0 and denies:
        return {"message": "Thank you. One more check: have you met or spoken with this person before today, "
                           "outside of this call?",
                "options": [{"id": "yes_known_before", "label": "Yes, I know them"},
                            {"id": "not_before", "label": "No, only today"}],
                "assessment": "continue", "coercion_delta": -0.1, "reason": "customer denies marker; verifying"}
    if first in ("yes_known_before",) or (customer_turns >= 1 and denies):
        return {"message": "Understood. Releasing the payment now. If anything about this feels wrong later, "
                           "you can report it from the app within the hour.",
                "options": [], "assessment": "release", "coercion_delta": -0.3,
                "reason": "customer knows payee, no markers confirmed"}
    facts = " ".join(s["facts"][:2])
    name = case["customer_name"].split()[0]
    trusted = case.get("trusted_contact", {}).get("name", "your trusted contact")
    return {"message": f"{facts} This looks like a scam that has targeted many people this month. I have paused "
                       f"the payment, {name}. Would you like me to call {trusted} now, on this screen, so they can hear this too?",
            "options": s["resolution_options"], "assessment": "hold", "coercion_delta": 0.2,
            "reason": "customer confirmed the script's marker"}


# ---------- case summary ----------

def summarize(case: dict, turns: list[dict]) -> str:
    fired = [c["label"] for c in case["signals"] if c["fired"]]
    script = case.get("script", {}).get("name", "unknown script")
    fallback = (f"{script} suspected on a ₹{case['amount']:,.0f} transfer from {case['customer_name']} to "
                f"{case['payee_name'] or case['payee_vpa']}; decisive signals: {', '.join(fired[:4])}. "
                f"Outcome: {case.get('status', 'open')}.")
    if config.GEMINI_MODE != "vertex":
        return fallback
    out = _generate_json((_PROMPTS / "summary_system.md").read_text() + ' Respond ONLY with JSON {"summary": "..."}',
                         json.dumps({"case": {k: str(v) for k, v in case.items() if k != "signals"},
                                     "signals": fired, "transcript": [(t["role"], t["text"]) for t in turns]}))
    return (out or {}).get("summary") or fallback
