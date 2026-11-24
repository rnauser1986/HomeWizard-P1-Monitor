import time

from config import (
    ALARM_IMBALANCE_COOLDOWN as COOLDOWN,
    ALARM_IMBALANCE_DIFF_A as DIFF_A,
    ALARM_IMBALANCE_NEED as NEED,
)
from notify_signal import send_signal

_n = 0
_last = 0.0
_peak_diff = 0.0


def check_imbalance(l1, l2, l3):
    global _n, _last, _peak_diff
    vals = [x for x in (l1, l2, l3) if x is not None]
    if len(vals) < 3:
        return

    fvals = [float(x) for x in vals]
    diff = max(fvals) - min(fvals)
    now = time.time()

    if diff >= DIFF_A:
        _n += 1
        if diff > _peak_diff:
            _peak_diff = diff
        if _n >= NEED and (now - _last) >= COOLDOWN:
            _last = now
            peak = _peak_diff
            _peak_diff = 0.0
            msg = (
                f"HomeWizard warning: phase imbalance >= {DIFF_A:.1f} A "
                f"(diff now {diff:.1f} A, peak {peak:.1f} A; L1={l1} A, L2={l2} A, L3={l3} A)"
            )
            print(f"[IMBALANCE] {msg}", flush=True)
            send_signal(msg)
    else:
        _n = 0
        _peak_diff = 0.0
