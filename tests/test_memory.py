"""The systems engineer's long-term memory: real mem0 and Qdrant on disk, with the embedding model
replaced by a deterministic stand-in so the test needs no download."""
import hashlib

import numpy as np


class FakeEmbedding:
    embedding_size = 384

    def __init__(self, model_name=None, **kw):
        pass

    def embed(self, text):
        texts = [text] if isinstance(text, str) else text
        for t in texts:
            v = np.zeros(384)
            for word in t.lower().split():
                v[int(hashlib.md5(word.strip('.,').encode()).hexdigest(), 16) % 384] += 1
            yield v / (np.linalg.norm(v) or 1)


def test_mem0_backend_round_trip_with_project_scopes(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    import mem0.embeddings.fastembed as fe
    monkeypatch.setattr(fe, "TextEmbedding", FakeEmbedding)
    from agents.memory import SHARED, Mem0Lessons

    mem = Mem0Lessons(tmp_path)
    mem.add("Check solar array area against summed loads first.", scope="sat-a", run="sat-a")
    mem.add("Take the margin on delivered power when a bus is decomposed.", scope=SHARED, run="")
    mem.add("Node pools must be sized per zone, not per cluster.", scope="k8s-b", run="k8s-b")
    assert len(mem.entries(scope=None)) == 3
    hits = mem.search("solar array area", k=3, scope="sat-a", shared=True)
    assert any("solar array" in h for h in hits) and not any("Node pools" in h for h in hits)   # another project's lessons stay out
    assert any("delivered power" in h for h in mem.search("decomposed bus power", k=3, scope="k8s-b", shared=True))   # the shared pool reaches every project
    assert not any("delivered power" in h for h in mem.search("decomposed bus power", k=3, scope="k8s-b", shared=False))
