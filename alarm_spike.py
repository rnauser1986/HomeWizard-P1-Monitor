import time

from config import (
    ALARM_SPIKE_COOLDOWN as COOLDOWN,
    ALARM_SPIKE_LIMIT_A as LIMIT_A,
    ALARM_SPIKE_NEED as NEED,
)
from notify_signal import send_signal

_n = 0
_last = 0.0


def check_spike(l1, l2, l3):
    global _n, _last
    vals = [x for x in (l1, l2, l3) if x is not None]
    if len(vals) < 3:
        return
    mx = max(float(x) for x in vals)
    if mx >= LIMIT_A:
        _n += 1
        now = time.time()
        if _n >= NEED and (now - _last) >= COOLDOWN:
            _last = now
            msg = (
                f"HomeWizard warning: current spike >= {LIMIT_A:.0f} A "
                f"(max {mx:.1f} A; L1={l1} A, L2={l2} A, L3={l3} A)"
            )
            print(f"[SPIKE] {msg}", flush=True)
            send_signal(msg)
    else:
        _n = 0
