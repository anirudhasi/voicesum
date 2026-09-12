import sys
import os
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app
from routers.auth import require_admin
import routers.dashboard_router as dashboard_router


ADMIN_USER = {
    "id": "test-admin",
    "name": "Test Admin",
    "email": "admin@example.test",
    "role": "admin",
}


class TestDashboardRouter(unittest.TestCase):
    """
    Behaviour of the dashboard handlers.

    Every route here is administrator-only (W6.1): the log and diagnostics
    endpoints expose meeting participant names and speaking times, and the
    maintenance endpoint changes runtime state. These tests authenticate as an
    administrator so they exercise the handlers rather than the gate.

    The gate itself — anonymous and non-admin callers being refused — is
    covered by tests/test_admin_authorization.py.
    """

    def setUp(self):
        app.dependency_overrides[require_admin] = lambda: ADMIN_USER

        # The WebSocket authorises inline rather than through a dependency,
        # because the bearer scheme cannot run against a handshake.
        self._real_ws_auth = dashboard_router.resolve_admin_from_refresh_cookie

        async def _admin_ws(_raw_cookie):
            return ADMIN_USER

        dashboard_router.resolve_admin_from_refresh_cookie = _admin_ws

        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(require_admin, None)
        dashboard_router.resolve_admin_from_refresh_cookie = self._real_ws_auth

    def test_dashboard_html(self):
        """Test GET /dashboard returns the HTML page."""
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("VoiceSum - Backend Developer Console", response.text)

    def test_dashboard_api_status(self):
        """Test GET /dashboard/api/status returns metrics JSON."""
        response = self.client.get("/dashboard/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Verify required keys in the response
        self.assertIn("system", data)
        self.assertIn("gpu", data)
        self.assertIn("models", data)
        self.assertIn("storage", data)
        self.assertIn("database", data)
        self.assertIn("vector_store", data)
        self.assertIn("performance", data)
        self.assertIn("activity", data)
        self.assertIn("config", data)
        
        # Verify system keys
        self.assertIn("uptime", data["system"])
        self.assertIn("cpu_percent", data["system"])
        self.assertIn("ram_percent", data["system"])
        
        # Verify database keys
        self.assertIn("path", data["database"])
        self.assertIn("size", data["database"])

    def test_dashboard_api_endpoints(self):
        """Test GET /dashboard/api/endpoints returns routes list."""
        response = self.client.get("/dashboard/api/endpoints")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("endpoints", data)
        self.assertIsInstance(data["endpoints"], list)
        self.assertTrue(len(data["endpoints"]) > 0)
        
        # Verify first endpoint structure
        endpoint = data["endpoints"][0]
        self.assertIn("path", endpoint)
        self.assertIn("methods", endpoint)
        self.assertIn("name", endpoint)
        self.assertIn("description", endpoint)

    def test_dashboard_api_logs(self):
        """Test GET /dashboard/api/logs returns logs."""
        response = self.client.get("/dashboard/api/logs")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("logs", data)
        self.assertIsInstance(data["logs"], list)

    def test_dashboard_api_maintenance(self):
        """Test POST /dashboard/api/maintenance triggers operations."""
        # 1. Clear CUDA Cache
        response = self.client.post("/dashboard/api/maintenance", json={"action": "clear_cuda_cache"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

        # 2. Clear Temp Files
        response = self.client.post("/dashboard/api/maintenance", json={"action": "clear_temp_files"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

        # 3. Invalid action
        response = self.client.post("/dashboard/api/maintenance", json={"action": "invalid_action_foo"})
        self.assertEqual(response.status_code, 400)

    def test_dashboard_api_diagnostics_export(self):
        """Test GET /dashboard/api/diagnostics/export downloads JSON file."""
        response = self.client.get("/dashboard/api/diagnostics/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        data = response.json()
        self.assertIn("timestamp", data)
        self.assertIn("status", data)
        self.assertIn("recent_logs", data)

    def test_dashboard_api_diagnostics_logs_download(self):
        """Test GET /dashboard/api/diagnostics/logs/download downloads voicesum.log."""
        response = self.client.get("/dashboard/api/diagnostics/logs/download")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])

    def test_dashboard_websocket(self):
        """Test WebSocket /dashboard/ws connection and logs streaming."""
        with self.client.websocket_connect("/dashboard/ws") as websocket:
            # First message must be the initial logs list
            msg = websocket.receive_json()
            self.assertEqual(msg["type"], "initial_logs")
            self.assertIsInstance(msg["logs"], list)
            
            # Next message must be the status and incremental logs update
            msg = websocket.receive_json()
            self.assertEqual(msg["type"], "update")
            self.assertIn("status", msg)
            self.assertIn("new_logs", msg)
