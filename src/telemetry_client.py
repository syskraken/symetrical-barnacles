import os
import json
import time
import uuid
import threading

import sys


if getattr(sys, 'frozen', False):
    _bundle_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    _cert_path = os.path.join(_bundle_dir, 'certifi', 'cacert.pem')
    if os.path.exists(_cert_path):
        os.environ['REQUESTS_CA_BUNDLE'] = _cert_path
        os.environ['SSL_CERT_FILE'] = _cert_path

SERVER_URL = "https://kraken.protectiva.site"
PRIVACY_POLICY_URL = "https://kraken.protectiva.site/privacy"
HEARTBEAT_INTERVAL_S = 25
REQUEST_TIMEOUT_S = 5

SETTINGS_FILENAME = "telemetry_settings.json"


class TelemetryClient:
    """Sends a periodic heartbeat with just a random client_id. The server
    determines the client's country from the request IP itself (see
    telemetry_server.py) — the client no longer collects or sends a region.
    The very first heartbeat for a given client_id is also what the server
    counts as one "install" in the fleet total.
    """

    def __init__(self, root, app_dir):
        self.root = root
        self.settings_path = os.path.join(app_dir, SETTINGS_FILENAME)
        self.client_id = None
        self._stop_event = threading.Event()
        self._thread = None

    # Public API

    def start(self):
        settings = self._load_settings()
        if settings is None:
            self.client_id = str(uuid.uuid4())
            self._save_settings()
        else:
            self.client_id = settings["client_id"]
        self._begin_heartbeats()

    def stop(self):
        self._stop_event.set()
        if self.client_id:
            self._post("/api/leave", {"client_id": self.client_id})

    # Local settings

    def _load_settings(self):
        if not os.path.exists(self.settings_path):
            return None
        try:
            with open(self.settings_path) as f:
                return json.load(f)
        except Exception:
            return None

    def _save_settings(self):
        data = {"client_id": self.client_id}
        try:
            with open(self.settings_path, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    # Heartbeat loop

    def _begin_heartbeats(self):
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def _heartbeat_loop(self):
        while not self._stop_event.is_set():
            self._post("/api/heartbeat", {"client_id": self.client_id})
            self._stop_event.wait(HEARTBEAT_INTERVAL_S)

    def _post(self, path, payload):
        try:
            import requests
            requests.post(f"{SERVER_URL}{path}", json=payload, timeout=REQUEST_TIMEOUT_S)
        except Exception:
            pass   # never let a network hiccup interrupt the bot