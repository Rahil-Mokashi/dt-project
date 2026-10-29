"""CSV and PDF exports of the audit trail. Both take the same filters as the Audit screen."""
import csv
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

from . import audit
from .fmt import inr

HEAD = ["Entry", "Time (IST)", "Case", "Customer", "Event", "Compliance", "By", "Detail", "Amount"]


def _row(e):
    return [e["id"], e["ts"], e["case_id"], e["customer"], e["event"], e["result"], e["actor"],
            e["detail"], e["amount"] if e["amount"] is not None else ""]


def csv_bytes(conn, filters):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(HEAD)
    w.writerows(_row(e) for e in audit.query(conn, filters))
    return out.getvalue().encode("utf-8-sig")


def pdf_bytes(conn, filters):
    rows = audit.query(conn, filters)
    s = audit.summary(conn)
    lo = filters.get("date_from") or (rows[-1]["ts"][:10] if rows else "-")
    hi = filters.get("date_to") or (rows[0]["ts"][:10] if rows else "-")
    st = getSampleStyleSheet()
    small = st["BodyText"].clone("small", fontSize=7, leading=8.5)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24,
                            title="RecoverIQ Nexus compliance report")
    cell = lambda t: Paragraph(escape(str(t)), small)
    data = [HEAD] + [[cell(x) for x in _row(e)] for e in rows]
    t = Table(data, repeatRows=1, colWidths=[40, 78, 44, 58, 66, 76, 46, 330, 40])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#15201C")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, 0), 7),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDE1DC")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    doc.build([
        Paragraph("RecoverIQ Nexus: compliance report", st["Title"]),
        Paragraph(f"StudyPro workspace · synthetic demo data · period {lo} to {hi} · "
                  f"{len(rows)} entries · generated {datetime(2026, 10, 29).strftime('%d %b %Y')} (demo date)", st["BodyText"]),
        Spacer(1, 6),
        Paragraph(f"Cases logged: {s['cases']} · Actions blocked: {s['blocked']} · Hard stops: {s['hard_stops']} · "
                  f"Contacts outside rules: {s['outside']} · Recovered: {escape(inr(s['recovered']))}", st["BodyText"]),
        Spacer(1, 8), t,
        Spacer(1, 6),
        Paragraph("Entries can't be edited or deleted. A correction is added as a new entry that points to the old one.", small),
    ])
    return buf.getvalue()
