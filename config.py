"""Every setting of the program. Other modules import their constants from here.

A variable in the environment (for example from the systemd unit) wins over .env.
.env wins over the default in this file.
"""
import os
from pathlib import Path

EXAMPLE_METER_URL = "http://192.168.1.50/api/v1/data"


def _load_dotenv(env_path=None):
    """Read KEY=value lines from .env into os.environ. A variable that is already set stays."""
    path = Path(env_path) if env_path else Path(__file__).resolve().parent / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()

BASE_DIR = Path(os.getenv("HW_BASE_DIR", Path(__file__).resolve().parent))
DB_PATH = os.getenv("HW_DB_PATH", str(BASE_DIR / "homewizard.db"))
UI_PATH = os.getenv("HW_UI_PATH", str(BASE_DIR / "ui.html"))

# HomeWizard P1 local API
HOMEWIZARD_URL = os.getenv("HW_METER_URL", EXAMPLE_METER_URL)
POLL_INTERVAL_SEC = int(os.getenv("HW_POLL_INTERVAL_SEC", "10"))
METER_TIMEOUT_SEC = float(os.getenv("HW_METER_TIMEOUT_SEC", "6"))

# Web and API server
API_HOST = os.getenv("HW_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("HW_API_PORT", "8085"))
HEALTH_MAX_AGE_SEC = int(os.getenv("HW_HEALTH_MAX_AGE_SEC", "120"))

# Signal notifications (optional). Empty means: log only.
SIGNAL_URL = os.getenv("HW_SIGNAL_URL", "").rstrip("/")
SIGNAL_FROM = os.getenv("HW_SIGNAL_FROM", "")
SIGNAL_TO = os.getenv("HW_SIGNAL_TO", "")
SIGNAL_TIMEOUT_SEC = float(os.getenv("HW_SIGNAL_TIMEOUT", "4.0"))
SIGNAL_COOLDOWN_SEC = float(os.getenv("HW_SIGNAL_COOLDOWN", "30.0"))

# Alarm thresholds and times
ALARM_IMBALANCE_DIFF_A = float(os.getenv("HW_ALARM_IMBALANCE_DIFF_A", "12.0"))
ALARM_IMBALANCE_NEED = int(os.getenv("HW_ALARM_IMBALANCE_NEED", "6"))
ALARM_IMBALANCE_COOLDOWN = int(os.getenv("HW_ALARM_IMBALANCE_COOLDOWN", "1200"))

ALARM_PHASE_LIMIT_A = float(os.getenv("HW_ALARM_PHASE_LIMIT_A", "20.0"))
ALARM_PHASE_NEED = int(os.getenv("HW_ALARM_PHASE_NEED", "3"))
ALARM_PHASE_COOLDOWN = int(os.getenv("HW_ALARM_PHASE_COOLDOWN", "1800"))

ALARM_SPIKE_LIMIT_A = float(os.getenv("HW_ALARM_SPIKE_LIMIT_A", "22.0"))
ALARM_SPIKE_NEED = int(os.getenv("HW_ALARM_SPIKE_NEED", "3"))
ALARM_SPIKE_COOLDOWN = int(os.getenv("HW_ALARM_SPIKE_COOLDOWN", "1200"))

ALARM_STALE_SEC = int(os.getenv("HW_ALARM_STALE_SEC", "300"))
ALARM_STALE_COOLDOWN = int(os.getenv("HW_ALARM_STALE_COOLDOWN", "1200"))

ALARM_VOLTAGE_LOW_V = float(os.getenv("HW_ALARM_VOLTAGE_LOW_V", "215.0"))
ALARM_VOLTAGE_HIGH_V = float(os.getenv("HW_ALARM_VOLTAGE_HIGH_V", "245.0"))
ALARM_VOLTAGE_NEED = int(os.getenv("HW_ALARM_VOLTAGE_NEED", "3"))
ALARM_VOLTAGE_COOLDOWN = int(os.getenv("HW_ALARM_VOLTAGE_COOLDOWN", "1800"))

# Power quality
PQ_POWER_PEAK_W = float(os.getenv("HW_PQ_POWER_PEAK_W", "9500.0"))
PQ_DV_WARN_V = float(os.getenv("HW_PQ_DV_WARN_V", "8.0"))
PQ_CACHE_TTL_SEC = float(os.getenv("HW_PQ_CACHE_TTL_SEC", "20.0"))
# The longest window the server allows, in hours (1 to 720).
PQ_MAX_HOURS = max(1, min(int(os.getenv("HW_PQ_MAX_HOURS", "168")), 720))

# Electricity price and tariff
DEFAULT_PRICE_AREA = os.getenv("HW_PRICE_AREA", "SE3")
DEFAULT_TARIFF_START_DATE = os.getenv("HW_TARIFF_START_DATE", "2026-11-01")
DEFAULT_FIXED_SEK = float(os.getenv("HW_DEFAULT_FIXED_SEK", "1.5"))
TARIFF_SYNC_COOLDOWN_SEC = int(os.getenv("HW_TARIFF_SYNC_COOLDOWN", "300"))
TARIFF_MAX_SLOT_AGE_HOURS = int(os.getenv("HW_TARIFF_MAX_SLOT_AGE_HOURS", "36"))
