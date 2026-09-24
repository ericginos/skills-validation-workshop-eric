"""
Unit and Integration Tests for Gemini AI Chatbot with Google Search Grounding.
"""

import os
import unittest
from unittest.mock import MagicMock
from google.genai import Client, types

# Import core helper functions from app
from app import extract_grounding_info, build_genai_history, initialize_gemini_client


class TestGeminiChatbot(unittest.TestCase):
    """Test suite for core application components."""

    def test_build_genai_history(self):
        """Tests conversion of session messages to google.genai Content objects."""
        messages = [
            {"role": "user", "content": "What is the capital of France?"},
            {"role": "assistant", "content": "The capital of France is Paris."},
            {"role": "user", "content": "What is its population?"},
        ]
        history = build_genai_history(messages)
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0].role, "user")
        self.assertEqual(history[0].parts[0].text, "What is the capital of France?")
        self.assertEqual(history[1].role, "model")
        self.assertEqual(history[1].parts[0].text, "The capital of France is Paris.")
        self.assertEqual(history[2].role, "user")
        self.assertEqual(history[2].parts[0].text, "What is its population?")

    def test_extract_grounding_info_empty(self):
        """Tests extraction when no grounding metadata exists."""
        res = extract_grounding_info(None)
        self.assertEqual(res["sources"], [])
        self.assertEqual(res["queries"], [])

    def test_extract_grounding_info_populated(self):
        """Tests extraction with mock GroundingMetadata containing chunks and queries."""
        mock_metadata = MagicMock()
        mock_metadata.web_search_queries = ["latest quantum computing news 2026"]

        # Mock web chunk
        mock_web = MagicMock()
        mock_web.uri = "https://example.com/quantum-news"
        mock_web.title = "Breakthroughs in Quantum Computing"
        mock_web.domain = "example.com"

        mock_chunk = MagicMock()
        mock_chunk.web = mock_web

        mock_metadata.grounding_chunks = [mock_chunk]

        info = extract_grounding_info(mock_metadata)
        self.assertEqual(len(info["queries"]), 1)
        self.assertEqual(info["queries"][0], "latest quantum computing news 2026")
        self.assertEqual(len(info["sources"]), 1)
        self.assertEqual(info["sources"][0]["title"], "Breakthroughs in Quantum Computing")
        self.assertEqual(info["sources"][0]["url"], "https://example.com/quantum-news")
        self.assertEqual(info["sources"][0]["domain"], "example.com")

    def test_gemini_client_initialization(self):
        """Tests client initialization using environment variables or Vertex AI."""
        client, mode, err = initialize_gemini_client()
        self.assertIsNotNone(client, f"Client failed to initialize: {err}")
        self.assertIn("Vertex AI", mode)
        self.assertIsNone(err)

    def test_live_search_grounding_call(self):
        """Integration test verifying gemini-2.5-flash with real-time Google Search tool."""
        client, _, _ = initialize_gemini_client()
        if not client:
            self.skipTest("Gemini client not initialized; skipping live API test.")

        config = types.GenerateContentConfig(
            temperature=0.2,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Search the web for the latest stock price and market performance of Alphabet GOOG.",
            config=config,
        )

        self.assertIsNotNone(response.text)
        self.assertTrue(len(response.text) > 0)
        self.assertTrue(len(response.candidates) > 0)

        grounding_meta = response.candidates[0].grounding_metadata
        self.assertIsNotNone(grounding_meta, "GroundingMetadata expected from Google Search tool.")
        info = extract_grounding_info(grounding_meta)
        self.assertTrue(len(info["queries"]) > 0 or len(info["sources"]) > 0)
        print(f"\n[Test Live Response]: {response.text.strip()}")
        print(f"[Test Grounding Queries]: {info['queries']}")
        print(f"[Test Sources Count]: {len(info['sources'])}")


if __name__ == "__main__":
    unittest.main()
