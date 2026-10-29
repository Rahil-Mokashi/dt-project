"""Rule-based failure classifier on the gateway decline code."""

INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
EXPIRED_CARD = "EXPIRED_CARD"
BANK_TIMEOUT = "BANK_TIMEOUT"
INVALID_ACCOUNT = "INVALID_ACCOUNT"
FRAUD_HOLD = "FRAUD_HOLD"

RULES = {
    51: (INSUFFICIENT_FUNDS, "soft"),
    54: (EXPIRED_CARD, "hard"),
    91: (BANK_TIMEOUT, "soft"),
    14: (INVALID_ACCOUNT, "hard"),
    59: (FRAUD_HOLD, "hard"),
    62: (FRAUD_HOLD, "hard"),
}

LABELS = {
    INSUFFICIENT_FUNDS: "Insufficient funds",
    EXPIRED_CARD: "Expired card",
    BANK_TIMEOUT: "Bank timeout",
    INVALID_ACCOUNT: "Invalid account",
    FRAUD_HOLD: "Fraud hold",
}

WHY_CUSTOMER = {
    INSUFFICIENT_FUNDS: "Your bank declined it because of a low balance. Nothing is wrong with your card.",
    EXPIRED_CARD: "The card on file has expired, so the bank couldn't process it.",
    BANK_TIMEOUT: "Your bank took too long to respond. Nothing is wrong on your side; we'll retry quietly.",
    INVALID_ACCOUNT: "The payment account on file couldn't be used. A new payment method will fix it.",
    FRAUD_HOLD: "Your bank paused this payment as a precaution. Our team is looking into it.",
}


def classify(code):
    """Return {'type', 'cls', 'rule'} for a gateway decline code."""
    if code not in RULES:
        raise ValueError(f"unknown decline code {code}")
    ftype, cls = RULES[code]
    return {"type": ftype, "cls": cls, "rule": f"code {code} → {ftype} ({cls})"}
