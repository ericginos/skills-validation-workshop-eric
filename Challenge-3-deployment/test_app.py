"""
Automated unit and integration tests for the Flask Web UI.
"""

import io
import unittest
from app import app


class FlaskAppTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_health_check(self):
        """Test the health check endpoint."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["bigquery_dataset"], "customer_service")
        self.assertEqual(data["bigquery_table"], "transcripts")

    def test_get_index(self):
        """Test GET / renders the main UI page."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Customer Service Transcript Parser", response.data)
        self.assertIn(b"Upload Transcript", response.data)

    def test_upload_empty_redirects(self):
        """Test POST /upload with empty submission redirects with a warning."""
        response = self.client.post("/upload", data={}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Please upload a transcript file or paste transcript text", response.data)

    def test_upload_file_end_to_end(self):
        """Test uploading a transcript file end-to-end with Gemini & BigQuery."""
        sample_transcript = (
            "Call ID: TEST-999\n"
            "Date: 2026-09-23 18:00 UTC\n"
            "Customer: Jordan Miller\n"
            "Agent: Taylor Reed\n"
            "Product: Cloud Sync\n"
            "Issue: File sync paused unexpectedly on desktop.\n"
            "Resolution: Restarted local sync daemon and verified file queue.\n"
            "Escalate: no\n"
        )
        data = {
            "transcript_file": (io.BytesIO(sample_transcript.encode("utf-8")), "test_transcript.txt")
        }
        response = self.client.post(
            "/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        # Check that metadata results are posted in HTML
        self.assertIn(b"Posted Metadata Fields", response.data)
        self.assertIn(b"TEST-999", response.data)
        self.assertIn(b"Jordan Miller", response.data)
        self.assertIn(b"Taylor Reed", response.data)
        self.assertIn(b"Cloud Sync", response.data)

    def test_api_upload_json(self):
        """Test POST /api/upload endpoint with JSON payload."""
        sample_transcript = (
            "Call ID: API-888\n"
            "Date: 2026-09-23 18:30 UTC\n"
            "Customer: Casey Smith\n"
            "Agent: Morgan Davis\n"
            "Product: Billing Engine\n"
            "Issue: Invoice duplicate charges.\n"
            "Resolution: Issued refund for duplicate charge.\n"
            "Escalate: no\n"
        )
        response = self.client.post(
            "/api/upload",
            json={"transcript": sample_transcript, "filename": "api_test.txt"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertIn("metadata", data)
        self.assertEqual(data["metadata"]["call_id"], "API-888")
        self.assertEqual(data["bigquery"]["dataset"], "customer_service")
        self.assertEqual(data["bigquery"]["table"], "transcripts")

    def test_api_transcripts(self):
        """Test GET /api/transcripts retrieves records."""
        response = self.client.get("/api/transcripts?limit=5")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertIsInstance(data["records"], list)


if __name__ == "__main__":
    unittest.main()
