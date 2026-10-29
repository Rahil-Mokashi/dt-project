"""Six-point compliance gate. Runs before EVERY outbound message and EVERY retry."""
from datetime import datetime, timedelta

from .fmt import dshort, pd

MAX_CONTACTS = 3
MAX_RETRIES = 4
OPEN_H, CLOSE_H = 9, 20  # contact window 9:00 AM to 8:00 PM IST

# failed check -> (audit label, routing action)
_ROUTE = [
    ("hard_stop", "FAIL hard stop", "hard_stop"),
    ("contacts", None, "escalate"),
    ("retries", None, "escalate"),
    ("hours", "FAIL hours", "defer"),
    ("dnd", "FAIL DND", "paytrack_only"),
    ("consent", "FAIL consent", "block"),
]


def next_open(now):
    """Next 9:00 AM IST at or after `now`."""
    d = now.date() if now.hour < OPEN_H else now.date() + timedelta(days=1)
    return datetime(d.year, d.month, d.day, OPEN_H)


def check(case, now, kind="message"):
    """kind='message' runs all six checks; kind='retry' applies only hours, hard stop
    and retry limit (a silent charge retry is not a customer contact)."""
    msg = kind == "message"
    paused = bool(case["pause_until"]) and now.date() < pd(case["pause_until"])
    if case["consent_withdrawn_at"]:
        consent_val, consent_ok = "Withdrawn", False
    elif paused:
        consent_val, consent_ok = f"Paused until {dshort(case['pause_until'])}", False
    elif not case["opt_in"]:
        consent_val, consent_ok = "Not recorded", False
    else:
        consent_val, consent_ok = "Valid", True
    in_hours = OPEN_H <= now.hour < CLOSE_H
    rows = [
        ("contacts", "Contact count", f"Max {MAX_CONTACTS} contacts per customer per day",
         case["contacts"] < MAX_CONTACTS or not msg, f"{case['contacts']} of {MAX_CONTACTS}"),
        ("hours", "Contact hours", "Only 9:00 AM–8:00 PM IST (demo clock)",
         in_hours, tshort_(now)),
        ("dnd", "DND status", "No WhatsApp/SMS to numbers on DND",
         not case["dnd"] or not msg, "On DND" if case["dnd"] else "Clear"),
        ("consent", "Consent validity", "WhatsApp opt-in recorded and not withdrawn",
         consent_ok or not msg, consent_val),
        ("hard_stop", "Hard-stop flag", "Blocks every message and retry when set",
         not case["hard_stop"], "Set" if case["hard_stop"] else "Not set"),
        ("retries", "Retry limit", f"Max {MAX_RETRIES} retries per case",
         case["retries"] < MAX_RETRIES, f"{case['retries']} of {MAX_RETRIES}"),
    ]
    checks = [dict(key=k, name=n, rule=r, ok=bool(ok), value=v) for k, n, r, ok, v in rows]
    failed = [c["key"] for c in checks if not c["ok"]]
    res = {"checks": checks, "ok": not failed, "failed": failed,
           "passed": 6 - len(failed), "result": "PASS 6/6", "route": "ok"}
    for key, label, action in _ROUTE:
        if key in failed:
            c = next(c for c in checks if c["key"] == key)
            res["result"] = label or f"FAIL {'contact' if key == 'contacts' else 'retries'} {c['value'].replace(' of ', '/')}"
            res["route"] = action
            break
    return res


def tshort_(now):
    return now.strftime("%I:%M %p").lstrip("0")
