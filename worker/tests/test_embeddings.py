import unittest
from unittest.mock import Mock, patch

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


if __name__ == "__main__":
    unittest.main()
