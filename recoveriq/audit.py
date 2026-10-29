"""Append-only audit log. No update or delete functions exist; SQLite triggers enforce it too."""
from . import db
from .fmt import pdt

EVENTS = ["FAILED", "CLASSIFIED", "STRATEGY", "GATE_CHECK", "MSG_SENT", "BLOCKED", "DEFERRED",
          "ESCALATED", "HARD_STOP", "RECOVERED", "RETRY_SCHEDULED", "PAUSED"]


def log(conn, case_id, customer, event, result, actor, detail, amount=None,
        ts=None, corrects=None, n=None, commit=True):
    """Append one entry and return its id ('A-0001'). A correction passes corrects='A-0123'."""
    if corrects:
        detail = f"Correction of {corrects}: {detail}"
    n = n or (conn.execute("SELECT COALESCE(MAX(n),0)+1 FROM audit").fetchone()[0])
    conn.execute(
        "INSERT INTO audit(n,ts,case_id,customer,event,result,actor,detail,amount) VALUES (?,?,?,?,?,?,?,?,?)",
        (n, ts or db.now_str(conn), case_id, customer, event, result, actor, detail, amount))
    if commit:
        conn.commit()
    return entry_id(n)


def entry_id(n):
    return f"A-{n:04d}"


def query(conn, f=None, limit=None):
    f = f or {}
    where, args = [], []
    if f.get("date_from"):
        where.append("substr(ts,1,10) >= ?"); args.append(f["date_from"])
    if f.get("date_to"):
        where.append("substr(ts,1,10) <= ?"); args.append(f["date_to"])
    if f.get("q"):
        where.append("(customer LIKE ? OR case_id LIKE ?)"); args += [f"%{f['q']}%"] * 2
    if f.get("event"):
        where.append("event = ?"); args.append(f["event"])
    if f.get("result") == "pass":
        where.append("result LIKE 'PASS%'")
    elif f.get("result") == "blocked":
        where.append("result LIKE 'FAIL%'")
    sql = "SELECT * FROM audit" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY n DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r, id=entry_id(r["n"])) for r in conn.execute(sql, args)]


def for_case(conn, case_id, limit=None):
    return [dict(r, id=entry_id(r["n"])) for r in conn.execute(
        "SELECT * FROM audit WHERE case_id=? ORDER BY n DESC" + (f" LIMIT {int(limit)}" if limit else ""),
        (case_id,))]


def outside_rules(conn):
    """MSG_SENT entries that broke the rules: gate not passed, or sent outside 9 AM–8 PM."""
    bad = 0
    for r in conn.execute("SELECT ts, result FROM audit WHERE event='MSG_SENT'"):
        if not (r["result"] or "").startswith("PASS") or not 9 <= pdt(r["ts"]).hour < 20:
            bad += 1
    return bad


def summary(conn):
    one = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "cases": one("SELECT COUNT(DISTINCT case_id) FROM audit"),
        "blocked": one("SELECT COUNT(*) FROM cases WHERE blocked_reason IS NOT NULL"),
        "hard_stops": one("SELECT COUNT(*) FROM cases WHERE status='HARD_STOP'"),
        "outside": outside_rules(conn),
        "recovered": one("SELECT COALESCE(SUM(recovered_amount),0) FROM cases WHERE status='RECOVERED'"),
    }
