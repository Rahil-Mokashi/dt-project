from flask import Flask, Response, abort, flash, g, redirect, render_template, request, url_for

from recoveriq import audit, classifier, compliance, db, export, strategy, workflow
from recoveriq.fmt import dshort, dtfull, inr, months, pd, pdt, tshort

PAGE = 10
TIERS = ["Basic", "Premium"]
STEPS = ["FAILED", "ANALYSED", "ACTION_TAKEN"]
TERMINAL = ("RECOVERED", "ESCALATED", "HARD_STOP")


def create_app(db_path=None):
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or db.DEFAULT_DB
    app.secret_key = "recoveriq-demo"  # demo only: flash messages
    with db.connect(app.config["DB_PATH"]) as c:
        db.init_db(c)
    app.jinja_env.filters.update(
        inr=inr, dshort=dshort, tshort=lambda s: tshort(pdt(s)), months=months,
        label=lambda t: classifier.LABELS.get(t, "Not classified"),
        dt=lambda s: f"{dshort(s)}, {tshort(pdt(s))}")

    def conn():
        if "db" not in g:
            g.db = db.connect(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close(_):
        d = g.pop("db", None)
        if d:
            d.close()

    @app.context_processor
    def inject():
        c = conn()
        now = db.now(c)
        return {"clock": db.get_clock(c), "in_hours": compliance.OPEN_H <= now.hour < compliance.CLOSE_H,
                "ep": request.endpoint or ""}

    def case_or_404(code):
        cid = code if code.startswith("SP-") else f"SP-{int(code):04d}" if code.isdigit() else code
        case = workflow.get_case(conn(), cid)
        if not case:
            abort(404)
        return case

    def back(case_id):
        return redirect(url_for("case_page", cid=case_id))

    # ---------- owner screens ----------
    @app.route("/")
    def dashboard():
        c = conn()
        args = {k: request.args.get(k, "") for k in ("q", "reason", "status", "tier")}
        rows = [dict(r) for r in c.execute("SELECT * FROM cases")]
        q = args["q"].strip().lower()
        rows = [r for r in rows
                if (not q or q in r["name"].lower() or q in r["id"].lower())
                and (not args["reason"] or (r["failure_type"] or "NONE") == args["reason"])
                and (not args["status"] or r["status"] == args["status"])
                and (not args["tier"] or r["tier"] == args["tier"])]
        rows.sort(key=workflow.priority)
        for r in rows:
            r["next"] = workflow.next_step(r)
        pages = max(1, -(-len(rows) // PAGE))
        page = min(max(request.args.get("page", 1, type=int), 1), pages)
        qs = {k: v for k, v in args.items() if v}
        return render_template("dashboard.html", s=workflow.stats(c), rows=rows[(page - 1) * PAGE:page * PAGE],
                               total=len(rows), page=page, pages=pages, args=args, qs=qs,
                               reasons=classifier.LABELS, states=workflow.STATES, tiers=TIERS)

    @app.post("/clock")
    def set_clock():
        try:
            db.set_clock(conn(), request.form["time"])
        except ValueError:
            flash("Enter the demo clock as HH:MM.")
        return redirect(request.referrer or url_for("dashboard"))

    @app.post("/run-cycle")
    def run_cycle():
        r = workflow.run_cycle(conn())
        flash(f"Recovery cycle done: {r['classified']} classified, {r['retried']} silent retries run.")
        return redirect(url_for("dashboard"))

    @app.post("/reset")
    def reset():
        db.init_db(conn(), reset=True)
        flash("Demo data rebuilt from the seed.")
        return redirect(url_for("dashboard"))

    @app.route("/cases/<cid>")
    def case_page(cid):
        c = conn()
        case = case_or_404(cid)
        now = db.now(c)
        gate = compliance.check(case, now, "message")
        cls = classifier.classify(case["decline_code"]) if case["failure_type"] else None
        plan = strategy.plan(case) if case["failure_type"] else None
        st = case["status"]
        cur = STEPS.index(st) if st in STEPS else 3
        chain = [{"name": n, "state": "done" if i < cur else "now" if i == cur else "todo"}
                 for i, n in enumerate(STEPS)]
        chain.append({"name": st if st in TERMINAL else "RECOVERED | ESCALATED | HARD_STOP",
                      "state": "now" if st in TERMINAL else "todo"})
        can_send = st == "ANALYSED" and gate["ok"] and bool(case["message"])
        gate_log = next((e for e in audit.for_case(c, case["id"]) if e["event"] == "GATE_CHECK"), None)
        since = pd(case["fail_at"]).replace(day=1)
        y, m = since.year, since.month - case["tenure"]
        while m < 1:
            y, m = y - 1, m + 12
        return render_template(
            "case.html", c=case, gate=gate, cls=cls, plan=plan, chain=chain, can_send=can_send,
            now=now, gate_id=gate_log["id"] if gate_log else "—", history=audit.for_case(c, case["id"], 6),
            since=f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m - 1]} {y}",
            code=int(case["id"][3:]), link=strategy.link(case), next=workflow.next_step(case),
            editing=request.args.get("edit") == "1")

    def act(cid, fn, ok_msg=None):
        try:
            res = fn()
            if isinstance(res, dict):
                flash(res["msg"])
            elif ok_msg:
                flash(ok_msg)
        except workflow.IllegalTransition as e:
            flash(f"Not allowed: {e}")
        return back(cid)

    @app.post("/cases/<cid>/approve")
    def approve(cid):
        return act(cid, lambda: workflow.send_message(conn(), cid, "Owner"))

    @app.post("/cases/<cid>/escalate")
    def escalate(cid):
        return act(cid, lambda: workflow.escalate(conn(), cid), "Escalated to a human.")

    @app.post("/cases/<cid>/hardstop")
    def hardstop(cid):
        return act(cid, lambda: workflow.hard_stop(conn(), cid, "Owner", "hard stop set by the owner"),
                   "Hard stop set. No more messages or retries.")

    @app.post("/cases/<cid>/stop")
    def inbound_stop(cid):
        return act(cid, lambda: workflow.hard_stop(conn(), cid, "Customer", "replied STOP"),
                   "Simulated: customer replied STOP. Hard stop set.")

    @app.post("/cases/<cid>/edit")
    def edit(cid):
        return act(cid, lambda: workflow.set_message(conn(), cid, request.form["message"]), "Draft saved.")

    @app.post("/cases/<cid>/regenerate")
    def regenerate(cid):
        return act(cid, lambda: workflow.regenerate_message(conn(), cid), "Draft regenerated.")

    @app.route("/audit")
    def audit_page():
        c = conn()
        f = audit_filters()
        return render_template("audit.html", rows=audit.query(c, f), s=audit.summary(c), f=f,
                               events=audit.EVENTS, qs={k: v for k, v in f.items() if v})

    def audit_filters():
        return {k: request.args.get(k, "").strip() for k in ("date_from", "date_to", "q", "event", "result")}

    @app.route("/audit.csv")
    def audit_csv():
        return Response(export.csv_bytes(conn(), audit_filters()), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=recoveriq-audit.csv"})

    @app.route("/audit.pdf")
    def audit_pdf():
        return Response(export.pdf_bytes(conn(), audit_filters()), mimetype="application/pdf",
                        headers={"Content-Disposition": "attachment; filename=recoveriq-compliance-report.pdf"})

    @app.route("/rules")
    def rules():
        return render_template("rules.html", checks=compliance.check(
            {"contacts": 0, "pause_until": None, "consent_withdrawn_at": None, "opt_in": 1, "dnd": 0,
             "hard_stop": 0, "retries": 0}, db.now(conn()))["checks"])

    # ---------- PayTrack (customer) ----------
    @app.route("/t/<code>")
    def paytrack(code):
        c = conn()
        case = case_or_404(code)
        st = case["status"]
        t = case["failure_type"] or classifier.classify(case["decline_code"])["type"]
        sent = c.execute("SELECT COUNT(*) FROM audit WHERE case_id=? AND event='MSG_SENT'", (case["id"],)).fetchone()[0]
        last_msg = c.execute("SELECT ts FROM audit WHERE case_id=? AND event='MSG_SENT' ORDER BY n DESC LIMIT 1",
                             (case["id"],)).fetchone()
        plan = strategy.plan(dict(case, failure_type=t))
        retry = case["next_retry_at"] or plan["retry_at"]
        if t == classifier.BANK_TIMEOUT and case["next_retry_at"]:
            retry_text = f"We'll retry quietly at {tshort(pdt(case['next_retry_at']))}."
        elif retry and t == classifier.INSUFFICIENT_FUNDS:
            retry_text = f"We'll try once on {dshort(retry)}, {tshort(pdt(retry))}" + (
                ", after salary day" if plan["name"].startswith("Reassure") else "")
        elif t in (classifier.EXPIRED_CARD, classifier.INVALID_ACCOUNT):
            retry_text = "Add a new payment method and we'll try once on it."
        else:
            retry_text = "Our team is reviewing this with your bank."
        cur = {"FAILED": 0, "ANALYSED": 1, "ACTION_TAKEN": 2, "ESCALATED": 2, "RECOVERED": 4, "HARD_STOP": 1}[st]
        if st == "ACTION_TAKEN" and sent == 0:
            cur = 2
        steps = [
            ("Payment failed", dshort(case["fail_at"])),
            ("Reminder sent", f"{dshort(last_msg['ts'])} · WhatsApp" if last_msg else
             ("No reminder needed" if plan["action"] != "message" else "Reminder coming soon")),
            ("Awaiting retry" if st != "ESCALATED" else "With our team", retry_text),
            ("Resolved", f"Paid {dshort(case['recovered_at'])}" if st == "RECOVERED" else "Your plan stays active"),
        ]
        total_msgs = plan["max_messages"]
        return render_template(
            "paytrack_stop.html" if st == "HARD_STOP" else "paytrack.html", c=case, why=classifier.WHY_CUSTOMER[t],
            steps=steps, cur=cur, sent=sent, more=max(total_msgs - sent, 0), code=int(case["id"][3:]),
            paused=case["pause_until"] and db.now(conn()).date() < pd(case["pause_until"]),
            can_act=st in ("ANALYSED", "ACTION_TAKEN", "ESCALATED"), first=strategy.first_name(case))

    def pt_act(code, fn, ok_msg):
        case = case_or_404(code)
        try:
            res = fn(case["id"])
            flash(res["msg"] if isinstance(res, dict) else ok_msg)
        except workflow.IllegalTransition as e:
            flash(f"Not allowed: {e}")
        return redirect(url_for("paytrack", code=int(case["id"][3:])))

    @app.post("/t/<code>/pay")
    def pt_pay(code):
        return pt_act(code, lambda i: workflow.pay_now(conn(), i), "Payment received (simulated). Thank you.")

    @app.post("/t/<code>/method")
    def pt_method(code):
        return pt_act(code, lambda i: workflow.update_method(conn(), i), "New payment method saved (simulated).")

    @app.post("/t/<code>/pause")
    def pt_pause(code):
        return pt_act(code, lambda i: workflow.pause_reminders(conn(), i), "Reminders paused for 7 days.")

    @app.post("/t/<code>/stop")
    def pt_stop(code):
        return pt_act(code, lambda i: workflow.hard_stop(conn(), i, "Customer", "tapped Stop all reminders"),
                      "Reminders stopped.")

    @app.post("/t/<code>/keep")
    def pt_keep(code):
        case_or_404(code)
        flash("Demo: a customer-initiated payment would be handled outside reminders. The hard stop stays in force.")
        return redirect(url_for("paytrack", code=int(code.replace("SP-", ""))))

    @app.route("/t/<code>/confirmation.txt")
    def pt_confirmation(code):
        c = case_or_404(code)
        text = (f"StudyPro: reminders stopped\nCase: {c['id']}\nConfirmation: {c['confirmation_id']}\n"
                f"Stopped at: {dtfull(pdt(c['consent_withdrawn_at'])) if c['consent_withdrawn_at'] else 'n/a'}\n"
                "No further reminders or automatic charge attempts will be made.\n(Synthetic demo data)\n")
        return Response(text, mimetype="text/plain",
                        headers={"Content-Disposition": f"attachment; filename={c['confirmation_id']}.txt"})

    return app


app = create_app()
