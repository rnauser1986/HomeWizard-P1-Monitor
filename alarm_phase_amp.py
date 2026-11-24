import time

from config import (
    ALARM_PHASE_COOLDOWN as COOLDOWN,
    ALARM_PHASE_LIMIT_A as LIMIT_A,
    ALARM_PHASE_NEED as NEED,
)
from notify_signal import send_signal

_state = {
    1: {"n": 0, "last": 0.0, "peak": 0.0},
    2: {"n": 0, "last": 0.0, "peak": 0.0},
    3: {"n": 0, "last": 0.0, "peak": 0.0},
}


def check_phase_amp(l1, l2, l3):
    vals = {1: l1, 2: l2, 3: l3}
    now = time.time()
    for phase, val in vals.items():
        st = _state[phase]
        if val is not None and float(val) >= LIMIT_A:
            fval = float(val)
            st["n"] += 1
            if fval > st["peak"]:
                st["peak"] = fval
            if st["n"] >= NEED and (now - st["last"]) >= COOLDOWN:
                st["last"] = now
                peak = st["peak"]
                st["peak"] = 0.0
                msg = (
                    f"HomeWizard warning: phase L{phase} stayed at >= {LIMIT_A:.0f} A "
                    f"(now {fval:.1f} A, peak {peak:.1f} A; L1={l1} A, L2={l2} A, L3={l3} A)"
                )
                print(f"[PHASE_AMP] {msg}", flush=True)
                send_signal(msg)
        else:
            st["n"] = 0
            st["peak"] = 0.0
