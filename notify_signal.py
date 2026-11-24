import json
import time
import urllib.request

from config import (
    SIGNAL_COOLDOWN_SEC as COOLDOWN_SEC,
    SIGNAL_FROM,
    SIGNAL_TIMEOUT_SEC as TIMEOUT_SEC,
    SIGNAL_TO,
    SIGNAL_URL,
)

# The text of each message sent lately, with the time. The same text is not sent twice inside COOLDOWN_SEC.
_last_sent_at = {}


def send_signal(msg, now=None):
    """Log the message. Send it through signal-cli-rest-api when Signal is set up. Never raises."""
    print(f"[SIGNAL_MSG] {msg}", flush=True)
    if not SIGNAL_URL or not SIGNAL_FROM or not SIGNAL_TO:
        return

    now = time.time() if now is None else now
    # Forget old messages, so the dictionary cannot grow without limit.
    for old in [text for text, sent in _last_sent_at.items() if now - sent >= COOLDOWN_SEC]:
        del _last_sent_at[old]
    if msg in _last_sent_at:
        return
    _last_sent_at[msg] = now

    try:
        body = json.dumps({
            "message": msg,
            "number": SIGNAL_FROM,
            "recipients": [SIGNAL_TO],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{SIGNAL_URL}/v2/send",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            r.read()
    except Exception as e:
        print(f"[SIGNAL_ERR] {e}", flush=True)
