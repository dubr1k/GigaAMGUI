import numpy as np
import pytest

from src.core.diarization.clustering import cluster_embeddings
from src.core.diarization.onnx_embeddings import EmbeddingResult


def _result(embeddings, windows, speakers=None, valid=None):
    embeddings = np.asarray(embeddings, dtype=np.float32)
    count = len(embeddings)
    return EmbeddingResult(
        embeddings=embeddings,
        valid=np.ones(count, dtype=np.bool_) if valid is None else np.asarray(valid),
        window_indices=np.asarray(windows),
        local_speakers=np.arange(count) if speakers is None else np.asarray(speakers),
    )


def test_known_speaker_count_clusters_similar_embeddings_across_windows():
    result = _result(
        [[1.0, 0.0], [0.0, 1.0], [0.99, 0.01], [0.01, 0.99]],
        [0, 0, 1, 1],
        [0, 1, 0, 1],
    )

    labels = cluster_embeddings(result, num_speakers=2)

    assert labels[0] == labels[2]
    assert labels[1] == labels[3]
    assert labels[0] != labels[1]


def test_same_window_speakers_have_cannot_link_constraint():
    result = _result([[1.0, 0.0], [1.0, 0.0]], [0, 0], [0, 1])

    labels = cluster_embeddings(result, threshold=1.0)

    assert labels.tolist() == [0, 1]


def test_invalid_embeddings_keep_minus_one_label():
    result = _result([[1.0, 0.0], [0.0, 0.0]], [0, 0], valid=[True, False])
    assert cluster_embeddings(result).tolist() == [0, -1]


def test_impossible_known_count_is_reported():
    result = _result([[1.0, 0.0], [0.0, 1.0]], [0, 0], [0, 1])
    with pytest.raises(ValueError, match="cannot-link"):
        cluster_embeddings(result, num_speakers=1)
