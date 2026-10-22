import unittest
from unittest.mock import Mock, patch

import requests

from memory_worker.embeddings import OllamaEmbeddings


class OllamaEmbeddingsTest(unittest.TestCase):
    @patch("memory_worker.embeddings.requests.post")
    def test_embeds_in_bounded_batches_and_reports_progress(self, post):
        def response_for_batch(_url, json, timeout):
            response = Mock()
            response.json.return_value = {
                "embeddings": [[float(index), 1.0] for index, _text in enumerate(json["input"])]
            }
            return response

        post.side_effect = response_for_batch
        progress = []
        embedder = OllamaEmbeddings("http://ollama", "bge-m3", 2, batch_size=2, timeout=30)

        embeddings = embedder.embed(
            ["one", "two", "three", "four", "five"],
            lambda completed, total: progress.append((completed, total)),
        )

        self.assertEqual(len(embeddings), 5)
        self.assertEqual([call.kwargs["json"]["input"] for call in post.call_args_list], [
            ["one", "two"],
            ["three", "four"],
            ["five"],
        ])
        self.assertTrue(all(call.kwargs["timeout"] == 30 for call in post.call_args_list))
        self.assertEqual(progress, [(2, 5), (4, 5), (5, 5)])

    @patch("memory_worker.embeddings.requests.post")
    def test_rejects_incomplete_ollama_response(self, post):
        response = Mock()
        response.json.return_value = {"embeddings": [[0.0, 1.0]]}
        post.return_value = response
        embedder = OllamaEmbeddings("http://ollama", "bge-m3", 2, batch_size=2)

        with self.assertRaisesRegex(ValueError, "unexpected embedding count"):
            embedder.embed(["one", "two"])

    @patch("memory_worker.embeddings.requests.post")
    def test_splits_batch_when_ollama_rejects_it(self, post):
        rejected = Mock(status_code=400)
        rejected.text = "input length exceeds maximum context length"

        def response_for_batch(_url, json, timeout):
            if len(json["input"]) > 1:
                return rejected
            response = Mock(status_code=200)
            response.json.return_value = {"embeddings": [[0.0, 1.0]]}
            return response

        post.side_effect = response_for_batch
        embedder = OllamaEmbeddings("http://ollama", "bge-m3", 2, batch_size=2)

        self.assertEqual(len(embedder.embed(["one", "two"])), 2)

    @patch("memory_worker.embeddings.requests.post")
    def test_shortens_single_text_until_ollama_accepts_it(self, post):
        def response_for_batch(_url, json, timeout):
            response = Mock(status_code=400 if len(json["input"][0]) > 1000 else 200)
            if response.status_code == 200:
                response.json.return_value = {"embeddings": [[0.0, 1.0]]}
            else:
                response.text = "input length exceeds maximum context length"
                response.raise_for_status.side_effect = requests.HTTPError("bad request")
            return response

        post.side_effect = response_for_batch
        embedder = OllamaEmbeddings("http://ollama", "bge-m3", 2, batch_size=1)

        self.assertEqual(len(embedder.embed(["x" * 4000])), 1)
        self.assertEqual([len(call.kwargs["json"]["input"][0]) for call in post.call_args_list], [4000, 2000, 1000])

    @patch("memory_worker.embeddings.requests.post")
    def test_does_not_retry_unrelated_bad_request(self, post):
        response = Mock(status_code=400, text="model does not support embeddings")
        response.raise_for_status.side_effect = requests.HTTPError("bad request")
        post.return_value = response
        embedder = OllamaEmbeddings("http://ollama", "bge-m3", 2)

        with self.assertRaises(requests.HTTPError):
            embedder.embed(["one", "two"])

        post.assert_called_once()

    @patch("memory_worker.embeddings.requests.post")
    def test_recovers_from_http_413_by_splitting_and_shortening_inputs(self, post):
        def response_for_batch(_url, json, timeout):
            inputs = json["input"]
            if len(inputs) > 1 or len(inputs[0]) > 1000:
                response = Mock(status_code=413, text="Request Entity Too Large")
                response.raise_for_status.side_effect = requests.HTTPError("request too large")
                return response
            response = Mock(status_code=200, text="")
            response.json.return_value = {"embeddings": [[0.0, 1.0]]}
            return response

        post.side_effect = response_for_batch
        embedder = OllamaEmbeddings("http://ollama", "bge-m3", 2, batch_size=2)
        inputs = ["x" * 4000, "short"]

        self.assertEqual(embedder.embed(inputs), [[0.0, 1.0], [0.0, 1.0]])
        self.assertEqual(len(inputs[0]), 4000)
        self.assertEqual(post.call_count, 5)


if __name__ == "__main__":
    unittest.main()
