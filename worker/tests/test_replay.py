import sys
import types
import unittest
from unittest.mock import Mock, patch


class ClientError(Exception):
    pass


psycopg = sys.modules.get("psycopg", types.ModuleType("psycopg"))
psycopg.connect = Mock()
sys.modules["psycopg"] = psycopg
boto3 = types.ModuleType("boto3")
boto3.client = Mock()
botocore = types.ModuleType("botocore")
botocore_exceptions = types.ModuleType("botocore.exceptions")
botocore_exceptions.ClientError = ClientError
sys.modules.setdefault("boto3", boto3)
sys.modules.setdefault("botocore", botocore)
sys.modules.setdefault("botocore.exceptions", botocore_exceptions)

from memory_worker.replay import event_records, replay_records


class ReplayTest(unittest.TestCase):
    @patch("memory_worker.replay.psycopg.connect")
    def test_selects_events_by_ingestion_time_with_inclusive_boundary(self, connect):
        cursor = Mock()
        cursor.fetchall.return_value = [("event-1", "v1/event-1.json.gz", "hash-1")]
        connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor

        records = event_records("postgresql://memory", "2026-09-30T00:00:00Z")

        self.assertEqual(records, [("event-1", "v1/event-1.json.gz", "hash-1")])
        query, parameters = cursor.execute.call_args.args
        self.assertIn("ingested_at >= %s", query)
        self.assertIn("ORDER BY ingested_at, event_id", query)
        self.assertEqual(parameters[0], "2026-09-30T00:00:00Z")

    def test_rejects_raw_object_with_mismatched_hash_before_writing(self):
        storage = Mock()
        storage.load.return_value = ({"event_id": "event-1"}, "wrong-hash", 100)
        database = Mock()

        with self.assertRaisesRegex(ValueError, "event-1"):
            replay_records(
                [("event-1", "v1/event-1.json.gz", "expected-hash")],
                storage,
                database,
                Mock(),
                24000,
            )

        database.persist.assert_not_called()

    @patch("memory_worker.replay.tombstones_from_envelope", return_value=[])
    @patch("memory_worker.replay.memories_from_envelope")
    def test_writes_complete_normalized_memory_for_matching_event(self, memories_from_envelope, _tombstones):
        memory = {"content": "updated metadata and content"}
        memories_from_envelope.return_value = [memory]
        envelope = {"event_id": "event-1"}
        storage = Mock()
        storage.load.return_value = (envelope, "expected-hash", 100)
        database = Mock()
        embedder = Mock()
        embedder.embed.return_value = [[0.1, 0.2]]

        count = replay_records(
            [("event-1", "v1/event-1.json.gz", "expected-hash")],
            storage,
            database,
            embedder,
            24000,
        )

        self.assertEqual(count, 1)
        database.persist.assert_called_once_with(
            envelope,
            "v1/event-1.json.gz",
            "expected-hash",
            100,
            None,
            [memory],
            [[0.1, 0.2]],
            [],
        )


if __name__ == "__main__":
    unittest.main()
