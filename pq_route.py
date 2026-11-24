from flask import Blueprint, jsonify, request

from config import DB_PATH, PQ_MAX_HOURS
from pq_data import get_power_quality

pq_bp = Blueprint("pq", __name__)


def _hours():
    try:
        return max(1, min(int(request.args.get("hours", 24)), PQ_MAX_HOURS))
    except (TypeError, ValueError):
        return 24


@pq_bp.get("/api/power-quality")
def power_quality():
    return jsonify(get_power_quality(DB_PATH, _hours()))
