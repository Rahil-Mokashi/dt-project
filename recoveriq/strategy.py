"""Strategy selection (failure type + tier + tenure) and WhatsApp message drafting."""
import os
from datetime import date, timedelta

from . import classifier as C
from .fmt import dshort, months, pd

STOP_LINE = "Reply STOP to stop all reminders."
REASON = {
    C.INSUFFICIENT_FUNDS: "because of a low balance",
    C.EXPIRED_CARD: "because your card has expired",
    C.INVALID_ACCOUNT: "because we couldn't use the saved payment account",
}


def first_name(case):
    return case["name"].split()[0]


def link(case):
    return f"studypro.in/t/{int(case['id'][3:])}"


def _first_of_next_month(d):
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def plan(case):
    """Strategy for a classified case: name, reason, 3-row plan, action and retry time."""
    t, tier, ten = case["failure_type"], case["tier"], case["tenure"]
    fd = pd(case["fail_at"])
    au = dshort(case["access_until"])
    who = f"{ten}-month {tier}"
    if t == C.INSUFFICIENT_FUNDS and tier == "Premium" and ten >= 6:
        r = _first_of_next_month(fd)
        return dict(
            name="Reassure, then retry on salary day", action="message", max_messages=2,
            reason=f"{who} subscriber, {case['on_time']} past payments on time. A low balance is likely temporary.",
            retry_at=f"{r} 10:00:00",
            steps=[("Today", "Send one reassuring WhatsApp message"),
                   (f"{dshort(r)}, 10 AM", "Retry the charge once, after salary day"),
                   ("If it fails", f"One reminder on {dshort(r + timedelta(days=1))}, then escalate before access ends on {au}")])
    if t == C.INSUFFICIENT_FUNDS:
        r = fd + timedelta(days=3)
        return dict(
            name="Gentle reminder, retry in 3 days", action="message", max_messages=1,
            reason=f"{who} subscriber. A low balance usually clears within days.",
            retry_at=f"{r} 10:00:00",
            steps=[("Today", "Send one short, polite WhatsApp message"),
                   (f"{dshort(r)}, 10 AM", "Retry the charge once"),
                   ("If it fails", f"Escalate to the owner before access ends on {au}")])
    if t == C.EXPIRED_CARD:
        new = tier == "Basic" and ten <= 3
        return dict(
            name="Fast card-update link" if new else "Card-update link, with thanks",
            action="message", max_messages=1 if new else 2, retry_at=None,
            reason=(f"{who} subscriber with an expired card. The shortest path is a card-update link."
                    if new else f"{who} subscriber. Thank them, then make the update one tap."),
            steps=[("Today", "Send a short message with the card-update link"),
                   ("When updated", "Retry once on the new card (never the expired one)"),
                   ("If no update", f"Escalate to the owner before access ends on {au}")])
    if t == C.INVALID_ACCOUNT:
        return dict(
            name="Ask for a new payment method", action="message", max_messages=1, retry_at=None,
            reason="The saved account cannot be charged. Retrying would only fail again.",
            steps=[("Today", "Ask for a new payment method on WhatsApp"),
                   ("When added", "Retry once on the new method"),
                   ("If no update", f"Escalate to the owner before access ends on {au}")])
    if t == C.BANK_TIMEOUT:
        return dict(
            name="Silent retry, no message", action="silent_retry", max_messages=0, retry_at=None,
            reason="Bank timeouts are usually temporary, so the customer isn't contacted at all.",
            steps=[("Today", "No message sent"), ("In 2 hours", "Retry the charge automatically"),
                   ("If it fails", "Retry up to 4 times in total, then escalate")])
    return dict(
        name="Escalate to a human, no contact", action="escalate", max_messages=0, retry_at=None,
        reason="A fraud hold is never retried automatically and the customer is not contacted.",
        steps=[("Today", "All contact and retries stop"), ("Next", "A person reviews it with the gateway"),
               ("If cleared", "The owner decides the next step")])


def template_message(case, p=None):
    p = p or plan(case)
    t = case["failure_type"]
    fd, au = dshort(case["fail_at"]), dshort(case["access_until"])
    line3 = {
        C.INSUFFICIENT_FUNDS: (f"We'll try once more on {dshort(p['retry_at'])}. " if p["retry_at"] else "") +
                              f"If you'd rather pay now or pause reminders, it's one tap: {link(case)}",
        C.EXPIRED_CARD: f"You can add a new card or UPI in about a minute: {link(case)}",
        C.INVALID_ACCOUNT: f"Please add a new payment method, it takes about a minute: {link(case)}",
    }[t]
    return (f"Hi {first_name(case)}, thank you for learning with StudyPro for {months(case['tenure'])}.\n"
            f"Your ₹{case['amount']} {case['tier']} renewal on {fd} didn't go through {REASON[t]}. "
            f"It happens, and your access is safe until {au}.\n{line3}\n{STOP_LINE}")


def _ask_claude(case, p):
    import anthropic  # imported lazily so the app runs without the SDK
    prompt = (
        "Write one WhatsApp message in English for an Indian ed-tech subscriber whose renewal payment failed.\n"
        f"First name: {first_name(case)}\nTier: {case['tier']}\nTenure: {months(case['tenure'])}\n"
        f"Failure: {C.LABELS[case['failure_type']]} ({REASON[case['failure_type']]})\n"
        f"Amount: ₹{case['amount']}\nFailed on: {dshort(case['fail_at'])}\n"
        f"Access until: {dshort(case['access_until'])}\nPayTrack link: {link(case)}\n"
        f"Retry date: {dshort(p['retry_at']) if p['retry_at'] else 'none (do not mention a retry)'}\n"
        "Rules: polite and short, under 70 words, no threats, no guilt, no urgency pressure. "
        "Mention the reason in plain language, the access-until date and the PayTrack link. "
        f"End with exactly this line: {STOP_LINE}\nReturn only the message text.")
    client = anthropic.Anthropic(timeout=15)
    resp = client.messages.create(model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
                                  max_tokens=300, messages=[{"role": "user", "content": prompt}])
    return resp.content[0].text.strip()


def generate_message(case):
    """Return (text, source) where source is 'claude' or 'template'. Never raises."""
    p = plan(case)
    if p["action"] != "message":
        return None, None
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            text = _ask_claude(case, p)
            if text.endswith(STOP_LINE) and link(case) in text:
                return text, "claude"
        except Exception:
            pass
    return template_message(case, p), "template"
