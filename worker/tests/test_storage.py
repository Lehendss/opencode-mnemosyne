import sys
import types
import unittest
from unittest.mock import Mock


class ClientError(Exception):
    pass


boto3 = types.ModuleType("boto3")
boto3.client = Mock()
botocore = types.ModuleType("botocore")
botocore_exceptions = types.ModuleType("botocore.exceptions")
botocore_exceptions.ClientError = ClientError
psycopg = types.ModuleType("psycopg")
sys.modules.setdefault("boto3", boto3)
sys.modules.setdefault("botocore", botocore)
sys.modules.setdefault("botocore.exceptions", botocore_exceptions)
sys.modules.setdefault("psycopg", psycopg)

from memory_worker.storage import RawStorage, _json


class RawStorageTest(unittest.TestCase):
    def test_json_metadata_removes_nul_escape(self):
        self.assertEqual(_json({"value": "before\x00after"}), '{"value": "beforeafter"}')

    def envelope(self):
        return {
            "schema_version": 1,
            "event_id": "event-1",
            "event_type": "memory.session.snapshot",
            "project_id": "project-1",
            "occurred_at": "2026-07-20T12:00:00+00:00",
            "captured_at": "2026-07-20T12:05:00+00:00",
            "payload_sha256": "payload-1",
            "payload": {"session": {"id": "session-1"}},
        }

    def storage(self, stored_envelope):
        storage = RawStorage.__new__(RawStorage)
        storage.bucket = "memory-raw"
        storage.client = Mock()
        storage.client.head_object.return_value = {"Metadata": {"sha256": "older-envelope"}}
        storage.load = Mock(return_value=(stored_envelope, "older-envelope", 123))
        return storage

    def test_reuses_existing_object_for_same_event_payload(self):
        envelope = self.envelope()
        stored = dict(envelope, captured_at="2026-07-20T12:01:00+00:00")
        key, digest, size = self.storage(stored).store(envelope)

        self.assertTrue(key.endswith("event-1.json.gz"))
        self.assertEqual(digest, "older-envelope")
        self.assertEqual(size, 123)

    def test_rejects_existing_object_with_different_payload(self):
        envelope = self.envelope()
        stored = dict(envelope, payload_sha256="different-payload")

        with self.assertRaises(ValueError):
            self.storage(stored).store(envelope)


if __name__ == "__main__":
    unittest.main()
