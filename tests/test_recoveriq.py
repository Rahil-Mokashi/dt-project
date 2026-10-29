import pytest

from app import create_app
from recoveriq import audit, classifier, compliance, db, export, workflow
from recoveriq.workflow import IllegalTransition


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def client(tmp_path):
    return create_app(str(tmp_path / "web.db")).test_client()


def events(conn, case_id):
    return [(e["event"], e["result"]) for e in audit.for_case(conn, case_id)]


# 1. Failure classification
def test_classification_differs_by_failure_type():
    a, b = classifier.classify(51), classifier.classify(54)
    assert a["type"] == "INSUFFICIENT_FUNDS" and b["type"] == "EXPIRED_CARD" and a["type"] != b["type"]
    assert a["rule"] == "code 51 → INSUFFICIENT_FUNDS (soft)"
    assert classifier.classify(62)["type"] == classifier.classify(59)["type"] == "FRAUD_HOLD"


def test_cycle_classifies_failed_cases(conn):
    workflow.run_cycle(conn)
    assert workflow.stats(conn)["states"]["FAILED"] == 0
    assert workflow.get_case(conn, "SP-3021")["failure_type"] == "EXPIRED_CARD"


# 2. Contact hours / DND
def test_message_at_21_00_deferred_to_next_9am(conn):
    db.set_clock(conn, "21:00")
    r = workflow.send_message(conn, "SP-1042")
    c = workflow.get_case(conn, "SP-1042")
    assert not r["ok"] and c["status"] == "ANALYSED" and c["contacts"] == 0
    assert c["deferred_until"] == "2026-10-30 09:00:00"
    top = audit.for_case(conn, "SP-1042")[0]
    assert top["event"] == "DEFERRED" and top["result"] == "FAIL hours" and "30 Oct, 9:00 AM" in top["detail"]


def test_dnd_message_blocked_and_logged(conn):
    r = workflow.send_message(conn, "SP-3311")
    assert not r["ok"] and workflow.get_case(conn, "SP-3311")["contacts"] == 0
    top = audit.for_case(conn, "SP-3311")[0]
    assert (top["event"], top["result"]) == ("BLOCKED", "FAIL DND")


def test_happy_path_send(conn):
    assert workflow.send_message(conn, "SP-1042")["ok"]
    c = workflow.get_case(conn, "SP-1042")
    assert c["status"] == "ACTION_TAKEN" and c["contacts"] == 1 and c["next_retry_at"] == "2026-11-01 10:00:00"
    assert ("MSG_SENT", "PASS 6/6") in events(conn, "SP-1042")


# 3. Consent withdrawal
def test_stop_blocks_every_later_message_and_retry(conn):
    workflow.hard_stop(conn, "SP-1042", "Customer", "replied STOP")
    c = workflow.get_case(conn, "SP-1042")
    assert c["status"] == "HARD_STOP" and c["hard_stop"] == 1 and c["confirmation_id"] == "HS-1042-2910"
    assert not workflow.send_message(conn, "SP-1042")["ok"]
    assert not workflow.attempt_retry(conn, "SP-1042")["ok"]
    assert workflow.get_case(conn, "SP-1042")["retries"] == 0
    assert events(conn, "SP-1042")[0] == ("BLOCKED", "FAIL hard stop")
    assert not workflow.attempt_retry(conn, "SP-0388")["ok"]  # seeded hard stop


# 4. Retry / contact limits
def test_fourth_contact_prevented_and_escalated(conn):
    conn.execute("UPDATE cases SET contacts=3 WHERE id='SP-1042'")
    r = workflow.send_message(conn, "SP-1042")
    c = workflow.get_case(conn, "SP-1042")
    assert not r["ok"] and c["status"] == "ESCALATED" and c["contacts"] == 3
    assert events(conn, "SP-1042")[0] == ("ESCALATED", "FAIL contact 3/3")


def test_fifth_retry_prevented(conn):
    workflow.run_cycle(conn)
    conn.execute("UPDATE cases SET retries=4, status='ACTION_TAKEN' WHERE id='SP-1730'")
    r = workflow.attempt_retry(conn, "SP-1730")
    c = workflow.get_case(conn, "SP-1730")
    assert not r["ok"] and c["retries"] == 4 and c["status"] == "ESCALATED"
    assert events(conn, "SP-1730")[0][1] == "FAIL retries 4/4"


# 5. PayTrack status
@pytest.mark.parametrize("code,step", [
    ("1042", "Reminder sent"),    # ANALYSED
    ("2217", "Awaiting retry"),   # ACTION_TAKEN
    ("1189", None),               # RECOVERED: every step done
    ("0388", "Stopped at your request"),  # HARD_STOP
])
def test_paytrack_current_step(client, code, step):
    html = client.get(f"/t/{code}").get_data(as_text=True)
    if step is None:
        assert 'aria-current="step"' not in html and "Payment received" in html
    else:
        assert f'data-step="{step}" aria-current="step"' in html or f'data-step="{step}"  aria-current="step"' in html


def test_paytrack_hard_stop_page(client):
    html = client.get("/t/0388").get_data(as_text=True)
    assert "Reminders stopped" in html and "HS-0388-2810" in html and "ends on 31 Oct" in html


def test_paytrack_pay_recovers_and_updates_dashboard(client):
    client.post("/t/1042/pay")
    home = client.get("/").get_data(as_text=True)
    assert "₹1,595" in home and "Latest: +₹499 · Priya S." in home


# 6. Audit export + append-only
def test_csv_has_every_entry_with_timestamps(conn):
    rows = export.csv_bytes(conn, {}).decode("utf-8-sig").strip().splitlines()
    assert len(rows) == 1 + conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    assert rows[1].split(",")[1].startswith("2026-10-")
    assert export.pdf_bytes(conn, {}).startswith(b"%PDF")


def test_audit_is_append_only(conn):
    for sql in ("UPDATE audit SET detail='x' WHERE n=1", "DELETE FROM audit WHERE n=1"):
        with pytest.raises(Exception, match="append-only"):
            conn.execute(sql)
    assert not [n for n in dir(audit) if n.startswith(("update", "delete", "remove"))]


# Seed + state machine
def test_seed_headline_numbers(conn):
    s = workflow.stats(conn)
    assert (s["failed"], s["at_risk"], s["recovered"], s["attention"], s["blocked_total"]) == (47, 11762, 1096, 14, 5)
    assert s["states"] == {"FAILED": 6, "ANALYSED": 9, "ACTION_TAKEN": 17, "RECOVERED": 4, "ESCALATED": 6, "HARD_STOP": 5}
    assert dict(s["reasons"]) == {"Insufficient funds": 18, "Expired card": 12, "Bank timeout": 8,
                                  "Invalid account": 5, "Fraud hold": 4}
    assert s["blocked_text"] == "3 consent withdrawn · 1 DND · 1 contact cap"
    a = audit.summary(conn)
    assert (a["cases"], a["blocked"], a["hard_stops"], a["outside"], a["recovered"]) == (47, 5, 5, 0, 1096)
    assert audit.query(conn, limit=1)[0]["id"] == "A-0193"
    assert conn.execute("SELECT detail FROM audit WHERE n=187").fetchone()[0].endswith("moved to 29 Oct 09:00")


def test_illegal_transitions_raise(conn):
    with pytest.raises(IllegalTransition):
        workflow.transition(conn, "SP-3021", "ACTION_TAKEN", "Owner", "skip ahead")   # FAILED -> ACTION_TAKEN
    with pytest.raises(IllegalTransition):
        workflow.transition(conn, "SP-1189", "ESCALATED", "Owner", "reopen")          # RECOVERED is final
    with pytest.raises(IllegalTransition):
        workflow.transition(conn, "SP-0388", "ANALYSED", "Owner", "reopen")           # HARD_STOP is final
    assert workflow.get_case(conn, "SP-3021")["status"] == "FAILED"


def test_fraud_hold_never_auto_retried(conn):
    r = workflow.attempt_retry(conn, "SP-0915")
    c = workflow.get_case(conn, "SP-0915")
    assert not r["ok"] and c["retries"] == 0 and c["status"] == "ESCALATED"
    workflow.run_cycle(conn)
    assert workflow.get_case(conn, "SP-0915")["retries"] == 0


def test_silent_retry_runs_only_when_due(conn):
    workflow.run_cycle(conn)
    assert workflow.get_case(conn, "SP-1730")["retries"] == 1      # due 16:00, clock 14:20
    db.set_clock(conn, "16:30")
    workflow.run_cycle(conn)
    assert workflow.get_case(conn, "SP-1730")["retries"] == 2


def test_screens_render(client):
    for url in ("/", "/?page=2&tier=Premium", "/?q=priya", "/cases/SP-1042", "/cases/SP-0388", "/cases/SP-3021",
                "/audit", "/audit?result=blocked", "/rules", "/t/1042", "/t/0388", "/t/1189", "/audit.csv", "/audit.pdf"):
        assert client.get(url).status_code == 200, url
    assert b"Demo mode" in client.get("/").data
