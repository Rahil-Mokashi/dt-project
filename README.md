# RecoverIQ Nexus: Intelligent Payment Recovery & Trust Platform

A compliance-first recovery layer for Indian subscription businesses. This is a **Design Thinking course prototype** (JSPM's RSCOE, CSBS). It runs on **47 synthetic failed-payment cases** for a made-up ed-tech business, "StudyPro". **All payments and messages are simulated**: there is no real payment gateway, no real WhatsApp and no real customer data. Testing with real subscribers and a legal review of the compliance rules are future work.

Pipeline per case: synthetic data → rule-based failure classifier → AI strategy + message generator → 6-point compliance gate → workflow state machine → owner dashboard + customer PayTrack page → append-only audit log.

## Team
Jyoti Prasad · Gargee Waghmare · Ruble Sarvate · Tanmay Mendhe

## Setup
```
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # optional: add ANTHROPIC_API_KEY (and ANTHROPIC_MODEL)
flask run                       # http://127.0.0.1:5000
pytest                          # run the tests
```
The SQLite file `recoveriq.db` is created and seeded on first run. **Reset demo data** (sidebar) rebuilds it.
Without an API key the app drafts messages from deterministic templates (the case page says "Template message (no API key)"). With `ANTHROPIC_API_KEY` set, Claude (default model `claude-sonnet-5-5`, override with `ANTHROPIC_MODEL`) drafts the message and any API failure falls back to the template. Export the variables in your shell before `flask run`; the app does not read `.env` itself.

## Demo walkthrough (demo date 29 Oct 2026, clock 14:20)
1. **Priya (SP-1042)**: open from the overview, read the failure, strategy and message, then **Approve and send**. The case moves to ACTION_TAKEN and a retry is scheduled for 1 Nov, 10:00. Click **Preview PayTrack page** to see what she sees.
2. **Rahul (SP-2217)**: card-update link already sent; open his PayTrack page and choose **Use a different card or UPI** to see the retry recover the payment (dashboard "Recovered" ticks up).
3. **Karan (SP-3311)**: on DND. The send button is disabled; the gate shows "DND status: On DND".
4. Set the **Demo clock to 21:00** on the overview. Open Priya (or any ANALYSED case): the gate shows "Contact hours" failing. Posting the send anyway is blocked and deferred to 9:00 AM next day (logged).
5. **Anjali (SP-0388)** is already HARD_STOP; on another case use **Simulate: customer replies STOP**, then open its `/t/<id>` page for the hard-stop screen. Every later message or retry is blocked.
6. **Audit trail** → filter, then **Export CSV** and **Compliance report (PDF)** (both respect the filters).

Also try **Run recovery cycle**: it classifies the 6 FAILED cases, drafts strategies, and runs due silent retries for bank timeouts. It never sends owner-approval messages.

## Screens
| URL | Screen |
|---|---|
| `/` | Revenue Risk Overview (KPIs, charts, filterable case table, demo clock) |
| `/cases/<id>` | Customer Recovery Card (failure, strategy, message draft, compliance gate, history) |
| `/audit` | Audit Trail (+ `/audit.csv`, `/audit.pdf`) |
| `/rules` | Compliance rules (read-only) |
| `/t/<id>` | PayTrack customer page; shows the hard-stop state when the case is HARD_STOP |

## Layout
`app.py` (Flask routes) · `recoveriq/` (`data_seed`, `classifier`, `strategy`, `compliance`, `workflow`, `audit`, `db`, `export`) · `templates/` · `static/` · `tests/`

## Notes and simplifications
- Headline numbers (47 / ₹11,762 / ₹1,096 / 14 / 5) are computed from the seeded rows, never hardcoded.
- The audit table is append-only (SQLite triggers raise on UPDATE/DELETE; the module exposes no update/delete function).
- "Contacts today" is a stored counter per case, not derived from the audit log.
- No login or CSRF protection: this is a single-user local demo.
