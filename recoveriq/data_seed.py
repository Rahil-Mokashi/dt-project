"""Deterministic synthetic data: 47 failed-payment cases for the demo business StudyPro.
Headline numbers (47 / ₹11,762 / ₹1,096 / 14 / 5) are never stored; they are computed from these rows."""
import random
from datetime import datetime, timedelta

from . import audit, classifier as C, strategy
from .fmt import TS, dshort

PRICE = {"Basic": 199, "Premium": 499}
CODE = {C.INSUFFICIENT_FUNDS: 51, C.EXPIRED_CARD: 54, C.BANK_TIMEOUT: 91, C.INVALID_ACCOUNT: 14, C.FRAUD_HOLD: 59}
IF, EC, BT, IA, FH = (C.INSUFFICIENT_FUNDS, C.EXPIRED_CARD, C.BANK_TIMEOUT, C.INVALID_ACCOUNT, C.FRAUD_HOLD)
B, P = "Basic", "Premium"

# (status, tier, failure type) for the 38 cases that are not named in the brief
GENERATED = (
    [("ESCALATED", P, FH), ("ESCALATED", B, FH), ("ESCALATED", P, FH), ("ESCALATED", B, IF)] +
    [("HARD_STOP", P, IF), ("HARD_STOP", B, EC), ("HARD_STOP", B, IA), ("HARD_STOP", B, IF)] +
    [("RECOVERED", B, IF), ("RECOVERED", B, EC), ("RECOVERED", B, BT)] +
    [("FAILED", P, IF), ("FAILED", B, IF), ("FAILED", B, EC), ("FAILED", B, BT), ("FAILED", B, IA)] +
    [("ANALYSED", P, IF), ("ANALYSED", P, IF), ("ANALYSED", P, EC), ("ANALYSED", B, IF), ("ANALYSED", B, IF),
     ("ANALYSED", B, EC), ("ANALYSED", B, IA)] +
    [("ACTION_TAKEN", P, IF), ("ACTION_TAKEN", P, IF), ("ACTION_TAKEN", P, IF), ("ACTION_TAKEN", B, IF),
     ("ACTION_TAKEN", P, EC), ("ACTION_TAKEN", P, EC), ("ACTION_TAKEN", B, EC), ("ACTION_TAKEN", B, EC),
     ("ACTION_TAKEN", B, EC), ("ACTION_TAKEN", P, BT), ("ACTION_TAKEN", B, BT), ("ACTION_TAKEN", B, BT),
     ("ACTION_TAKEN", B, BT), ("ACTION_TAKEN", B, BT), ("ACTION_TAKEN", B, IA)]
)
FIRST = ["Aarav", "Diya", "Rohan", "Ananya", "Ishaan", "Kavya", "Aditya", "Neha", "Siddharth", "Pooja", "Manish",
         "Riya", "Varun", "Tanvi", "Harsh", "Shreya", "Nikhil", "Isha", "Yash", "Divya", "Amit", "Sakshi", "Rajat",
         "Nisha", "Kunal", "Pallavi", "Mohit", "Swati", "Akash", "Tara", "Deepak", "Mitali", "Gaurav", "Ritu",
         "Sameer", "Pranav", "Aisha", "Omkar"]
BT_RETRY_TIMES = ["11:00", "13:00", "17:30", "18:30", "19:00"]


def _case(id_, name, tier, tenure, status, ftype, fail_at, **kw):
    fd = datetime.strptime(fail_at, TS)
    ontime = f"{tenure - 1}/{tenure - 1}" if tenure > 1 else "First billing cycle"
    c = dict(id=id_, name=name, tier=tier, tenure=tenure, amount=PRICE[tier], status=status,
             decline_code=CODE[ftype], failure_type=None if status == "FAILED" else ftype, fail_at=fail_at,
             contacts=0, retries=0, access_until=(fd + timedelta(days=7)).strftime("%Y-%m-%d"), on_time=ontime,
             opt_in=1, consent_withdrawn_at=None, dnd=0, hard_stop=0, blocked_reason=None, message=None,
             message_source=None, next_retry_at=None, deferred_until=None, pause_until=None,
             confirmation_id=None, recovered_at=None, recovered_amount=0)
    c.update(kw)
    return c


def build_cases():
    rng = random.Random(2026)
    named = [
        _case("SP-1042", "Priya S.", P, 14, "ANALYSED", IF, "2026-10-27 09:12:00", on_time="13/13"),
        _case("SP-0915", "Vikram R.", P, 22, "ESCALATED", FH, "2026-10-28 09:40:00"),
        _case("SP-2604", "Arjun M.", B, 3, "ESCALATED", IA, "2026-10-25 09:30:00", contacts=3, retries=2,
              blocked_reason="CONTACT_CAP"),
        _case("SP-2217", "Rahul K.", B, 1, "ACTION_TAKEN", EC, "2026-10-28 12:00:00", contacts=1),
        _case("SP-1730", "Sneha P.", B, 7, "ACTION_TAKEN", BT, "2026-10-28 10:30:00", retries=1,
              next_retry_at="2026-10-29 16:00:00"),
        _case("SP-3311", "Karan T.", B, 5, "ANALYSED", IF, "2026-10-27 13:10:00", dnd=1, blocked_reason="DND"),
        _case("SP-3021", "Farhan Q.", B, 2, "FAILED", EC, "2026-10-28 13:40:00"),
        _case("SP-0388", "Anjali D.", P, 9, "HARD_STOP", IF, "2026-10-26 10:05:00", access_until="2026-10-31",
              hard_stop=1, consent_withdrawn_at="2026-10-28 19:02:00", blocked_reason="HARD_STOP",
              confirmation_id="HS-0388-2810"),
        _case("SP-1189", "Meera J.", P, 11, "RECOVERED", IF, "2026-10-24 11:15:00",
              recovered_at="2026-10-28 16:44:00", recovered_amount=499),
    ]
    taken = {c["id"] for c in named}
    ids = [f"SP-{n:04d}" for n in rng.sample(range(1000, 4000), 60)]
    ids = [i for i in ids if i not in taken][:len(GENERATED)]
    firsts = FIRST[:]
    rng.shuffle(firsts)
    cases = list(named)
    hs_seen = 0
    bt_times = iter(BT_RETRY_TIMES)  # fresh per call so re-seeding is deterministic
    for i, (status, tier, ftype) in enumerate(GENERATED):
        name = f"{firsts[i]} {rng.choice('ABCDGHJKMNPRSTV')}."
        tenure = rng.randint(1, 9) if tier == B else rng.randint(6, 26)
        fail_at = f"2026-10-{rng.randint(26, 28)} {rng.randint(9, 13):02d}:{rng.randint(0, 59):02d}:00"
        kw = {}
        if status == "ESCALATED" and ftype == IF:
            kw.update(retries=4, contacts=2)  # retry limit reached
        if status == "ESCALATED" and ftype == FH:
            kw["decline_code"] = rng.choice([59, 62])
        if status == "HARD_STOP":
            hs_seen += 1
            if hs_seen <= 2:  # customer withdrew consent
                kw.update(hard_stop=1, blocked_reason="HARD_STOP", confirmation_id=None)
            else:             # owner set the hard stop
                kw.update(hard_stop=1)
        if status == "RECOVERED":
            kw.update(recovered_amount=PRICE[tier], recovered_at=f"2026-10-{rng.randint(26, 28)} {rng.randint(10, 15):02d}:{rng.randint(0, 59):02d}:00")
        c = _case(ids[i], name, tier, tenure, status, ftype, fail_at, **kw)
        if status == "HARD_STOP" and c["blocked_reason"]:
            c["consent_withdrawn_at"] = (datetime.strptime(fail_at, TS) + timedelta(minutes=30)).strftime(TS)
            c["confirmation_id"] = f"HS-{int(c['id'][3:]):04d}-{c['consent_withdrawn_at'][8:10]}{c['consent_withdrawn_at'][5:7]}"
        if status == "ACTION_TAKEN":
            if ftype == BT:
                c["retries"] = rng.choice([0, 0, 1])
                c["next_retry_at"] = f"2026-10-29 {next(bt_times)}:00"
            else:
                c["contacts"] = 1
        cases.append(c)
    for c in cases:  # drafted / sent messages, and scheduled retries for message-based strategies
        if c["failure_type"] and strategy.plan(c)["action"] == "message" and c["status"] in ("ANALYSED", "ACTION_TAKEN"):
            c["message"], c["message_source"] = strategy.template_message(c), "template"
            if c["status"] == "ACTION_TAKEN" and strategy.plan(c)["retry_at"]:
                c["next_retry_at"] = strategy.plan(c)["retry_at"]
    return cases


def _t(c, minutes):
    return (datetime.strptime(c["fail_at"], TS) + timedelta(minutes=minutes)).strftime(TS)


def build_audit(cases):
    """Return (bulk entries sorted by time, fixed entries A-0184..A-0193)."""
    bulk = []

    def add(c, mins, event, result, actor, detail, amount=None):
        bulk.append((_t(c, mins), c["id"], c["name"], event, result, actor, detail, amount))

    for c in cases:
        f = c["failure_type"] or C.classify(c["decline_code"])["type"]
        cid = c["id"]
        add(c, 0, "FAILED", "—", "System", f"Renewal of ₹{c['amount']} failed at the gateway (code {c['decline_code']})", c["amount"])
        if c["status"] == "FAILED":
            continue
        add(c, 2, "CLASSIFIED", "—", "Agent", C.classify(c["decline_code"])["rule"])
        p = strategy.plan(dict(c, failure_type=f))
        if cid != "SP-1042":
            add(c, 3, "STRATEGY", "—", "Agent", f"FAILED → ANALYSED: strategy: {p['name']}")
        s = c["status"]
        if s == "ANALYSED" and cid not in ("SP-1042", "SP-3311"):
            add(c, 5, "GATE_CHECK", "PASS 6/6", "System", "Pre-approval compliance check")
        if s == "ESCALATED" and f == FH and cid != "SP-0915":
            add(c, 4, "ESCALATED", "—", "Agent", "ANALYSED → ESCALATED: fraud hold: human review, never auto-retried")
        if s == "ESCALATED" and f == IF:
            add(c, 10, "GATE_CHECK", "FAIL retries 4/4", "System", "Pre-retry compliance check")
            add(c, 11, "ESCALATED", "FAIL retries 4/4", "System", "ACTION_TAKEN → ESCALATED: retry blocked (retries 4/4), next retry prevented")
        if cid == "SP-2604":
            for k in (20, 40, 60):
                add(c, k, "MSG_SENT", "PASS 6/6", "Owner", "WhatsApp sent (simulated)")
        if s == "ACTION_TAKEN":
            if f == C.BANK_TIMEOUT:
                add(c, 4, "RETRY_SCHEDULED", "—", "System", "ANALYSED → ACTION_TAKEN: silent retry scheduled, no message sent")
            else:
                add(c, 20, "GATE_CHECK", "PASS 6/6", "System", "Pre-send compliance check")
                if cid != "SP-2217":
                    add(c, 22, "MSG_SENT", "PASS 6/6", "Owner", "ANALYSED → ACTION_TAKEN: WhatsApp message sent (simulated)")
                if p["retry_at"]:
                    add(c, 23, "RETRY_SCHEDULED", "—", "System", f"Retry scheduled {dshort(p['retry_at'])}, 10:00 AM")
        if s == "RECOVERED" and cid != "SP-1189":
            add(c, 40, "RECOVERED", "—", "System", "ACTION_TAKEN → RECOVERED: retry succeeded (simulated), amount matched", c["amount"])
        if s == "HARD_STOP" and cid != "SP-0388":
            who = "Customer" if c["consent_withdrawn_at"] else "Owner"
            add(c, 30, "HARD_STOP", "FLAG SET", who, "customer replied STOP" if who == "Customer" else "hard stop set by the owner")
            if who == "Customer":
                add(c, 40, "BLOCKED", "FAIL hard stop", "System", "Scheduled reminder suppressed: hard-stop flag set")
    bulk.sort(key=lambda e: e[0])
    assert len(bulk) == 183, len(bulk)
    S = lambda *a: a
    fixed = [
        ("2026-10-28 15:30:00", "SP-0915", "Vikram R.", "ESCALATED", "—", "Agent",
         "ANALYSED → ESCALATED: fraud hold (code 59), human review, never auto-retried", None),
        ("2026-10-28 16:44:00", "SP-1189", "Meera J.", "RECOVERED", "—", "System",
         "ACTION_TAKEN → RECOVERED: retry succeeded (simulated), ₹499 matched to invoice", 499),
        ("2026-10-28 19:02:00", "SP-0388", "Anjali D.", "HARD_STOP", "FLAG SET", "Customer",
         "ACTION_TAKEN → HARD_STOP: customer replied STOP; confirmation HS-0388-2810", None),
        ("2026-10-28 21:12:00", "SP-1730", "Sneha P.", "DEFERRED", "FAIL hours", "System",
         "Retry blocked outside 9 AM–8 PM IST (21:12); moved to 29 Oct 09:00", None),
        ("2026-10-29 09:41:00", "SP-2217", "Rahul K.", "MSG_SENT", "PASS 6/6", "Owner",
         "ANALYSED → ACTION_TAKEN: card-update link sent on WhatsApp (simulated)", None),
        ("2026-10-29 10:05:00", "SP-3311", "Karan T.", "BLOCKED", "FAIL DND", "System",
         "Number on DND: no WhatsApp/SMS, PayTrack status only", None),
        ("2026-10-29 11:30:00", "SP-2604", "Arjun M.", "ESCALATED", "FAIL contact 3/3", "System",
         "ANALYSED → ESCALATED: message blocked (contact 3/3), 4th contact prevented", None),
        ("2026-10-29 12:00:00", "SP-0388", "Anjali D.", "BLOCKED", "FAIL hard stop", "System",
         "Scheduled reminder suppressed: hard-stop flag set", None),
        ("2026-10-29 14:19:00", "SP-1042", "Priya S.", "STRATEGY", "—", "Agent",
         "FAILED → ANALYSED: strategy: Reassure, then retry on salary day; message drafted", None),
        ("2026-10-29 14:20:06", "SP-1042", "Priya S.", "GATE_CHECK", "PASS 6/6", "System",
         "Pre-approval compliance check", None),
    ]
    return bulk, fixed


def seed(conn):
    cases = build_cases()
    cols = list(cases[0])
    conn.executemany(f"INSERT INTO cases({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [tuple(c[k] for k in cols) for c in cases])
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('clock', '14:20')")
    bulk, fixed = build_audit(cases)
    for n, (ts, cid, cust, ev, res, actor, detail, amt) in enumerate(bulk + fixed, start=1):
        audit.log(conn, cid, cust, ev, res, actor, detail, amt, ts=ts, n=n, commit=False)
    conn.commit()
