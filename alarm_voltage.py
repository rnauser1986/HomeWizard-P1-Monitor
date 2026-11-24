import time

from config import (
    ALARM_VOLTAGE_COOLDOWN as COOLDOWN,
    ALARM_VOLTAGE_HIGH_V as HIGH_V,
    ALARM_VOLTAGE_LOW_V as LOW_V,
    ALARM_VOLTAGE_NEED as NEED,
)
from notify_signal import send_signal

_state = {
    (1, "LOW"): {"n": 0, "last": 0.0, "extreme": None},
    (2, "LOW"): {"n": 0, "last": 0.0, "extreme": None},
    (3, "LOW"): {"n": 0, "last": 0.0, "extreme": None},
    (1, "HIGH"): {"n": 0, "last": 0.0, "extreme": None},
    (2, "HIGH"): {"n": 0, "last": 0.0, "extreme": None},
    (3, "HIGH"): {"n": 0, "last": 0.0, "extreme": None},
}


def check_voltage(v1, v2, v3):
    vals = {1: v1, 2: v2, 3: v3}
    now = time.time()

    for phase, val in vals.items():
        if val is None:
            _state[(phase, "LOW")]["n"] = 0
            _state[(phase, "LOW")]["extreme"] = None
            _state[(phase, "HIGH")]["n"] = 0
            _state[(phase, "HIGH")]["extreme"] = None
            continue

        fval = float(val)

        st_low = _state[(phase, "LOW")]
        if fval < LOW_V:
            st_low["n"] += 1
            if st_low["extreme"] is None or fval < st_low["extreme"]:
                st_low["extreme"] = fval
            if st_low["n"] >= NEED and (now - st_low["last"]) >= COOLDOWN:
                st_low["last"] = now
                low_min = st_low["extreme"]
                st_low["extreme"] = None
                msg = (
                    f"HomeWizard warning: phase L{phase} voltage < {LOW_V:.0f} V "
                    f"(now {fval:.1f} V, lowest {low_min:.1f} V; L1={v1} V, L2={v2} V, L3={v3} V)"
                )
                print(f"[VOLTAGE_LOW] {msg}", flush=True)
                send_signal(msg)
        else:
            st_low["n"] = 0
            st_low["extreme"] = None

        st_high = _state[(phase, "HIGH")]
        if fval > HIGH_V:
            st_high["n"] += 1
            if st_high["extreme"] is None or fval > st_high["extreme"]:
                st_high["extreme"] = fval
            if st_high["n"] >= NEED and (now - st_high["last"]) >= COOLDOWN:
                st_high["last"] = now
                high_max = st_high["extreme"]
                st_high["extreme"] = None
                msg = (
                    f"HomeWizard warning: phase L{phase} voltage > {HIGH_V:.0f} V "
                    f"(now {fval:.1f} V, highest {high_max:.1f} V; L1={v1} V, L2={v2} V, L3={v3} V)"
                )
                print(f"[VOLTAGE_HIGH] {msg}", flush=True)
                send_signal(msg)
        else:
            st_high["n"] = 0
            st_high["extreme"] = None
