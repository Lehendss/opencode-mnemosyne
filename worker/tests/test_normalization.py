import unittest

from memory_worker.normalization import memories_from_envelope, session_from_envelope


class NormalizationTest(unittest.TestCase):
    def envelope(self):
        return {
            "event_id": "event-1",
            "event_type": "message.part.updated",
            "project_id": "project-1",
            "project_label": "project",
            "session_id": "session-1",
            "message_id": "message-1",
            "occurred_at": "2026-07-17T12:00:00Z",
            "payload": {
                "properties": {
                    "part": {
                        "id": "part-1",
                        "sessionID": "session-1",
                        "messageID": "message-1",
                        "type": "text",
                        "text": "A durable technical decision",
                    }
                }
            },
        }

    def test_text_part_becomes_stable_memory(self):
        memories = memories_from_envelope(self.envelope(), 1000)
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["source_id"], "part-1")
        self.assertEqual(memories[0]["content"], "A durable technical decision")

    def test_reasoning_is_not_persisted_as_memory(self):
        envelope = self.envelope()
        envelope["payload"]["properties"]["part"]["type"] = "reasoning"
        self.assertEqual(memories_from_envelope(envelope, 1000), [])

    def test_snapshot_extracts_preference_decision_and_procedure(self):
        envelope = self.envelope()
        envelope["event_type"] = "memory.session.snapshot"
        envelope["payload"] = {
            "session": {"id": "session-1", "title": "Build durable memory"},
            "messages": [
                {
                    "info": {"id": "user-1", "role": "user"},
                    "parts": [
                        {
                            "id": "user-part",
                            "messageID": "user-1",
                            "type": "text",
                            "text": "Prefiero backups cifrados. Soluciona el error de memoria.",
                        }
                    ],
                },
                {
                    "info": {
                        "id": "assistant-1",
                        "parentID": "user-1",
                        "role": "assistant",
                    },
                    "parts": [
                        {
                            "id": "tool-part",
                            "messageID": "assistant-1",
                            "type": "tool",
                            "tool": "bash",
                            "state": {
                                "status": "completed",
                                "input": {"command": "make test"},
                                "output": "7 tests passed",
                            },
                        },
                        {
                            "id": "assistant-part",
                            "messageID": "assistant-1",
                            "type": "text",
                            "text": "La estrategia es usar una outbox durable. Las pruebas pasaron.",
                        },
                    ],
                },
            ],
        }
        memories = memories_from_envelope(envelope, 8000)
        kinds = {memory["kind"] for memory in memories}
        self.assertIn("preference", kinds)
        self.assertIn("decision", kinds)
        self.assertIn("bug_resolution", kinds)
        procedure = next(memory for memory in memories if memory["kind"] == "bug_resolution")
        self.assertIn("make test", procedure["content"])
        self.assertGreaterEqual(procedure["importance"], 0.9)

    def test_long_quoted_article_is_not_a_preference(self):
        envelope = self.envelope()
        envelope["event_type"] = "memory.session.snapshot"
        envelope["payload"] = {
            "session": {"id": "session-1", "title": "Review article"},
            "messages": [
                {
                    "info": {"id": "user-1", "role": "user"},
                    "parts": [
                        {
                            "id": "article-part",
                            "messageID": "user-1",
                            "type": "text",
                            "text": "I prefer this quoted article. " + ("external content " * 100),
                        }
                    ],
                }
            ],
        }
        memories = memories_from_envelope(envelope, 8000)
        self.assertNotIn("preference", {memory["kind"] for memory in memories})

    def test_tool_error_becomes_incident(self):
        envelope = self.envelope()
        envelope["event_type"] = "memory.session.snapshot"
        envelope["payload"] = {
            "session": {"id": "session-1", "title": "Diagnose failure"},
            "messages": [
                {
                    "info": {"id": "assistant-1", "role": "assistant"},
                    "parts": [
                        {
                            "id": "failed-tool",
                            "messageID": "assistant-1",
                            "type": "tool",
                            "tool": "bash",
                            "state": {
                                "status": "error",
                                "input": {"command": "make test"},
                                "error": "compilation failed",
                            },
                        }
                    ],
                }
            ],
        }
        memories = memories_from_envelope(envelope, 8000)
        incident = next(memory for memory in memories if memory["kind"] == "incident")
        self.assertIn("compilation failed", incident["content"])
        self.assertEqual(incident["confidence"], 0.98)


if __name__ == "__main__":
    unittest.main()
