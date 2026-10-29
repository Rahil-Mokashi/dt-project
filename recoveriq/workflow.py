"""Workflow state machine plus every action that changes a case. Each transition is audited."""
from datetime import timedelta

from . import audit, classifier, compliance, db, strategy
from .fmt import dshort, pdt, tshort

STATES = ["FAILED", "ANALYSED", "ACTION_TAKEN", "RECOVERED", "ESCALATED", "HARD_STOP"]
OPEN = ("FAILED", "ANALYSED", "ACTION_TAKEN", "ESCALATED")
TRANSITIONS = {
    "FAILED": {"ANALYSED", "ESCALATED", "HARD_STOP"},
    "ANALYSED": {"ACTION_TAKEN", "ESCALATED", "HARD_STOP", "RECOVERED"},
    "ACTION_TAKEN": {"RECOVERED", "ESCALATED", "HARD_STOP"},
    "ESCALATED": {"HARD_STOP", "RECOVERED"},
    "RECOVERED": set(),
    "HARD_STOP": set(),
}
_EVENT = {"ANALYSED": "STRATEGY", "ACTION_TAKEN": "MSG_SENT", "RECOVERED": "RECOVERED",
          "ESCALATED": "ESCALATED", "HARD_STOP": "HARD_STOP"}


class IllegalTransition(Exception):
    pass


# ---------- helpers ----------
def get_case(conn, case_id):
    r = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    return dict(r) if r else None


def _set(conn, case_id, **kw):
    conn.execute(f"UPDATE cases SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), case_id))


def _log(conn, case, event, result, actor, detail, amount=None):
    return audit.log(conn, case["id"], case["name"], event, result, actor, detail, amount)


def transition(conn, case_id, new, actor, reason, event=None, result="—", amount=None):
    """Move a case to `new`, or raise IllegalTransition. Writes the audit entry with its reason."""
    case = get_case(conn, case_id)
    if new not in TRANSITIONS[case["status"]]:
        raise IllegalTransition(f"{case['status']} → {new} is not allowed")
    _set(conn, case_id, status=new)
    return _log(conn, case, event or _EVENT[new], result, actor, f"{case['status']} → {new}: {reason}", amount)


# ---------- classification + strategy ----------
def classify_and_draft(conn, case_id, actor="Agent"):
    case = get_case(conn, case_id)
    c = classifier.classify(case["decline_code"])
    _set(conn, case_id, failure_type=c["type"])
    case["failure_type"] = c["type"]
    _log(conn, case, "CLASSIFIED", "—", actor, c["rule"])
    p = strategy.plan(case)
    transition(conn, case_id, "ANALYSED", actor, f"strategy: {p['name']}")
    if p["action"] == "message":
        text, src = strategy.generate_message(case)
        _set(conn, case_id, message=text, message_source=src)
    elif p["action"] == "silent_retry":
        when = (db.now(conn) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:00")
        _set(conn, case_id, next_retry_at=when)
        transition(conn, case_id, "ACTION_TAKEN", "System", f"silent retry at {tshort(pdt(when))}, no message sent",
                   event="RETRY_SCHEDULED")
    else:
        transition(conn, case_id, "ESCALATED", actor, "fraud hold: human review, never auto-retried")
    conn.commit()


def run_cycle(conn):
    """Classify FAILED cases, draft strategies, run due silent retries. Never sends owner-approval messages."""
    done = {"classified": 0, "retried": 0}
    for r in conn.execute("SELECT id FROM cases WHERE status='FAILED' ORDER BY id").fetchall():
        classify_and_draft(conn, r["id"])
        done["classified"] += 1
    now = db.now_str(conn)
    for r in conn.execute("SELECT id FROM cases WHERE status='ACTION_TAKEN' AND failure_type='BANK_TIMEOUT' "
                          "AND next_retry_at<=? ORDER BY id", (now,)).fetchall():
        attempt_retry(conn, r["id"])
        done["retried"] += 1
    return done


# ---------- gated actions ----------
def _route_block(conn, case, gate, what, now):
    """Apply the routing rules after a failed gate. Returns the routing action."""
    action, res = gate["route"], gate["result"]
    if action == "hard_stop":
        _log(conn, case, "BLOCKED", res, "System", f"{what} blocked: hard-stop flag set, no contact or retry")
    elif action == "escalate":
        reason = "RETRY_LIMIT" if "retries" in gate["failed"] else "CONTACT_CAP"
        _set(conn, case["id"], blocked_reason=reason)
        if "ESCALATED" in TRANSITIONS[case["status"]]:
            why = "4th contact prevented" if reason == "CONTACT_CAP" else "next retry prevented"
            transition(conn, case["id"], "ESCALATED", "System", f"{what} blocked ({res[5:]}), {why}", result=res)
        else:
            _log(conn, case, "BLOCKED", res, "System", f"{what} blocked: {res[5:]}")
    elif action == "defer":
        nxt = compliance.next_open(now).strftime("%Y-%m-%d %H:%M:00")
        _set(conn, case["id"], **({"deferred_until": nxt} if what == "message" else {"next_retry_at": nxt}))
        _log(conn, case, "DEFERRED", res, "System",
             f"{what} blocked outside 9 AM–8 PM IST (demo clock {tshort(now)}); moved to {dshort(nxt)}, {tshort(pdt(nxt))}")
    elif action == "paytrack_only":
        _set(conn, case["id"], blocked_reason="DND")
        _log(conn, case, "BLOCKED", res, "System", "Number on DND: no WhatsApp/SMS, PayTrack status only")
    else:
        paused = gate["checks"][3]["value"].startswith("Paused")
        _set(conn, case["id"], blocked_reason="PAUSED" if paused else "CONSENT")
        _log(conn, case, "BLOCKED", res, "System", f"{what} blocked: consent {gate['checks'][3]['value'].lower()}")
    return action


def send_message(conn, case_id, actor="Owner"):
    """Approve and send the drafted message (simulated). Returns {'ok', 'outcome', 'msg'}."""
    case = get_case(conn, case_id)
    now = db.now(conn)
    gate = compliance.check(case, now, "message")
    _log(conn, case, "GATE_CHECK", gate["result"], "System", "Pre-send compliance check")
    if not gate["ok"]:
        out = _route_block(conn, case, gate, "message", now)
        conn.commit()
        return {"ok": False, "outcome": out, "msg": f"Blocked: {gate['result']}"}
    if case["status"] != "ANALYSED" or not case["message"]:
        return {"ok": False, "outcome": "state", "msg": f"Cannot send in state {case['status']}"}
    p = strategy.plan(case)
    _set(conn, case_id, contacts=case["contacts"] + 1, deferred_until=None)
    transition(conn, case_id, "ACTION_TAKEN", actor, "WhatsApp message sent (simulated)", result=gate["result"])
    if p["retry_at"]:
        _set(conn, case_id, next_retry_at=p["retry_at"])
        _log(conn, case, "RETRY_SCHEDULED", "—", "System", f"Retry scheduled {dshort(p['retry_at'])}, 10:00 AM")
    conn.commit()
    return {"ok": True, "outcome": "sent", "msg": "Message sent (simulated)"}


def attempt_retry(conn, case_id, success=None, actor="System", note=None):
    """Retry the charge (simulated). Runs the gate first. Fraud holds are never retried."""
    case = get_case(conn, case_id)
    now = db.now(conn)
    if case["failure_type"] == classifier.FRAUD_HOLD or case["decline_code"] in (59, 62):
        _log(conn, case, "BLOCKED", "FAIL fraud hold", "System", "Retry refused: fraud hold is never auto-retried")
        conn.commit()
        return {"ok": False, "outcome": "fraud", "msg": "Fraud holds are never retried"}
    gate = compliance.check(case, now, "retry")
    _log(conn, case, "GATE_CHECK", gate["result"], "System", "Pre-retry compliance check")
    if not gate["ok"]:
        out = _route_block(conn, case, gate, "retry", now)
        conn.commit()
        return {"ok": False, "outcome": out, "msg": f"Blocked: {gate['result']}"}
    n = case["retries"] + 1
    _set(conn, case_id, retries=n, next_retry_at=None)
    if success is None:
        success = (int(case["id"][3:]) + case["retries"]) % 2 == 0  # deterministic simulated outcome
    if success:
        _recover(conn, case, actor, note or f"retry {n} succeeded (simulated), ₹{case['amount']} matched to invoice")
        conn.commit()
        return {"ok": True, "outcome": "recovered", "msg": "Payment recovered"}
    if n >= compliance.MAX_RETRIES:
        _set(conn, case_id, blocked_reason="RETRY_LIMIT")
        transition(conn, case_id, "ESCALATED", "System", f"retry {n} failed, retry limit reached",
                   result=f"FAIL retries {n}/{compliance.MAX_RETRIES}")
    else:
        when = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:00")
        _set(conn, case_id, next_retry_at=when)
        _log(conn, case, "RETRY_SCHEDULED", "—", "System",
             f"Retry {n} failed (simulated); next at {dshort(when)}, {tshort(pdt(when))}")
    conn.commit()
    return {"ok": True, "outcome": "failed", "msg": "Retry failed"}


def _recover(conn, case, actor, reason):
    _set(conn, case["id"], recovered_amount=case["amount"], recovered_at=db.now_str(conn), next_retry_at=None)
    transition(conn, case["id"], "RECOVERED", actor, reason, amount=case["amount"])


# ---------- owner / customer actions ----------
def escalate(conn, case_id, actor="Owner"):
    transition(conn, case_id, "ESCALATED", actor, "escalated to a human by the owner")
    conn.commit()


def hard_stop(conn, case_id, actor, how):
    """Final. Customer-initiated stops count as consent withdrawn."""
    case = get_case(conn, case_id)
    if case["status"] == "HARD_STOP":
        return
    now = db.now(conn)
    kw = dict(hard_stop=1, next_retry_at=None, deferred_until=None)
    if actor == "Customer":
        kw.update(consent_withdrawn_at=now.strftime("%Y-%m-%d %H:%M:%S"), blocked_reason="HARD_STOP",
                  confirmation_id=f"HS-{int(case['id'][3:]):04d}-{now.day:02d}{now.month:02d}")
    transition(conn, case_id, "HARD_STOP", actor, f"customer {how}" if actor == "Customer" else how, result="FLAG SET")
    _set(conn, case_id, **kw)
    conn.commit()


def pay_now(conn, case_id):
    case = get_case(conn, case_id)
    _recover(conn, case, "Customer", "customer paid on PayTrack (simulated), amount matched")
    conn.commit()


def update_method(conn, case_id):
    """Customer adds a new card/UPI on PayTrack (simulated); the retry runs through the gate."""
    return attempt_retry(conn, case_id, success=True, actor="Customer",
                         note="new payment method added on PayTrack; retry succeeded (simulated), amount matched")


def pause_reminders(conn, case_id, days=7):
    case = get_case(conn, case_id)
    until = (db.now(conn) + timedelta(days=days)).strftime("%Y-%m-%d")
    _set(conn, case_id, pause_until=until)
    _log(conn, case, "PAUSED", "—", "Customer", f"Customer paused reminders for {days} days (until {dshort(until)})")
    conn.commit()


def set_message(conn, case_id, text):
    case = get_case(conn, case_id)
    text = text.strip()
    if not text.endswith(strategy.STOP_LINE):
        text += "\n" + strategy.STOP_LINE
    _set(conn, case_id, message=text, message_source="edited")
    _log(conn, case, "STRATEGY", "—", "Owner", "Message draft edited by the owner")
    conn.commit()


def regenerate_message(conn, case_id):
    case = get_case(conn, case_id)
    text, src = strategy.generate_message(case)
    _set(conn, case_id, message=text, message_source=src)
    _log(conn, case, "STRATEGY", "—", "Owner", f"Message draft regenerated ({src})")
    conn.commit()


# ---------- views ----------
def next_step(c):
    s, t = c["status"], c["failure_type"]
    if s == "RECOVERED":
        return f"₹{c['recovered_amount']} collected {dshort(c['recovered_at'])} · closed"
    if s == "HARD_STOP":
        return (f"Consent withdrawn {dshort(c['consent_withdrawn_at'])} — no contact"
                if c["consent_withdrawn_at"] else "Hard stop set by owner — no contact")
    if s == "ESCALATED":
        if t == classifier.FRAUD_HOLD:
            return "Human review — never auto-retried"
        if c["contacts"] >= compliance.MAX_CONTACTS:
            return "Contact cap reached — call by owner"
        if c["retries"] >= compliance.MAX_RETRIES:
            return "Retry limit reached — owner review"
        return "Escalated — owner follow-up"
    if s == "FAILED":
        return "Waiting for the classifier"
    if s == "ANALYSED":
        if c["dnd"]:
            return "Blocked: number on DND — PayTrack only"
        if c["deferred_until"]:
            return f"Deferred to {dshort(c['deferred_until'])}, {tshort(pdt(c['deferred_until']))}"
        if c["blocked_reason"] == "PAUSED":
            return "Customer paused reminders — PayTrack only"
        return "Message ready for approval"
    if t == classifier.BANK_TIMEOUT:
        return f"Silent retry at {tshort(pdt(c['next_retry_at']))}" if c["next_retry_at"] else "Silent retry done"
    if t == classifier.INSUFFICIENT_FUNDS and c["next_retry_at"]:
        return f"Retry on {dshort(c['next_retry_at'])}, {tshort(pdt(c['next_retry_at']))}"
    if t == classifier.EXPIRED_CARD:
        return "Card-update link sent — waiting for customer"
    return "Waiting for a new payment method"


def priority(c):
    s = c["status"]
    if s == "ANALYSED":
        rank = 3 if c["blocked_reason"] or c["deferred_until"] else 0
    else:
        rank = {"ESCALATED": 1, "FAILED": 2, "ACTION_TAKEN": 4, "RECOVERED": 5, "HARD_STOP": 6}[s]
    return (rank, -c["amount"], c["fail_at"], c["id"])


BLOCK_LABEL = {"HARD_STOP": "consent withdrawn", "DND": "DND", "CONTACT_CAP": "contact cap",
               "RETRY_LIMIT": "retry limit", "CONSENT": "no consent", "PAUSED": "paused"}


def stats(conn):
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    marks = ",".join("?" * len(OPEN))
    esc = one("SELECT COUNT(*) FROM cases WHERE status='ESCALATED'")
    wait = one("SELECT COUNT(*) FROM cases WHERE status='ANALYSED' AND blocked_reason IS NULL")
    latest = conn.execute("SELECT * FROM cases WHERE status='RECOVERED' ORDER BY recovered_at DESC LIMIT 1").fetchone()
    blocked = {r[0]: r[1] for r in conn.execute(
        "SELECT blocked_reason, COUNT(*) FROM cases WHERE blocked_reason IS NOT NULL GROUP BY 1")}
    reasons = {}
    for r in conn.execute("SELECT decline_code FROM cases"):
        label = classifier.LABELS[classifier.classify(r[0])["type"]]
        reasons[label] = reasons.get(label, 0) + 1
    return {
        "failed": one("SELECT COUNT(*) FROM cases"),
        "at_risk": one(f"SELECT COALESCE(SUM(amount),0) FROM cases WHERE status IN ({marks})", *OPEN),
        "recovered": one("SELECT COALESCE(SUM(recovered_amount),0) FROM cases WHERE status='RECOVERED'"),
        "latest": dict(latest) if latest else None,
        "escalated": esc, "awaiting": wait, "attention": esc + wait,
        "blocked_total": sum(blocked.values()),
        "blocked_text": " · ".join(f"{blocked[k]} {l}" for k, l in BLOCK_LABEL.items() if k in blocked),
        "reasons": sorted(reasons.items(), key=lambda kv: -kv[1]),
        "states": {s: one("SELECT COUNT(*) FROM cases WHERE status=?", s) for s in STATES},
    }
