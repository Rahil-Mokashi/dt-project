from datetime import date, datetime

TS = "%Y-%m-%d %H:%M:%S"


def pdt(s):
    return datetime.strptime(s, TS)


def pd(s):
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def dshort(s):
    """'2026-10-27 09:12:00' -> '27 Oct'"""
    d = pd(s) if isinstance(s, str) else s
    return f"{d.day} {d:%b}"


def tshort(dt):
    """datetime -> '2:20 PM'"""
    return dt.strftime("%I:%M %p").lstrip("0")


def dtfull(dt):
    return f"{dt.day} {dt:%b %Y}, {dt.strftime('%I:%M:%S %p').lstrip('0')}"


def inr(n):
    """Indian digit grouping: 1234567 -> ₹12,34,567"""
    s = str(int(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return "₹" + s


def months(n):
    return f"{n} month{'' if n == 1 else 's'}"
