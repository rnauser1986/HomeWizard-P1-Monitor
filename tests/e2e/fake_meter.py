"""A fake HomeWizard P1 meter for the Docker test. It answers /api/v1/data with fixed readings.

Run it with: python fake_meter.py
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

READING = {
    "wifi_strength": 90,
    "active_power_w": 500,
    "active_power_l1_w": 100,
    "active_power_l2_w": 200,
    "active_power_l3_w": 200,
    "active_voltage_l1_v": 230.0,
    "active_voltage_l2_v": 231.0,
    "active_voltage_l3_v": 229.0,
    "active_current_a": 2.0,
    "active_current_l1_a": 1.0,
    "active_current_l2_a": 1.0,
    "active_current_l3_a": 1.0,
    "total_power_import_kwh": 100.5,
    "total_power_export_kwh": 1.5,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/v1/data":
            body = json.dumps(READING).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def make_server(host="0.0.0.0", port=8080):
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    make_server().serve_forever()
