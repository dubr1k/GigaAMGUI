import time

import numpy as np

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


def test_impossible_known_count_degrades_instead_of_raising():
    """cannot-link не даёт слить двух спикеров одного окна в одного."""
    result = _result([[1.0, 0.0], [0.0, 1.0]], [0, 0], [0, 1])

    labels = cluster_embeddings(result, num_speakers=1)

    assert labels.tolist() == [0, 1]


def test_speaker_count_above_valid_rows_is_clamped():
    result = _result([[1.0, 0.0], [0.99, 0.01]], [0, 1], [0, 0])

    labels = cluster_embeddings(result, num_speakers=5)

    assert sorted(set(labels.tolist())) == [0, 1]


def test_speaker_count_below_one_is_clamped():
    result = _result([[1.0, 0.0], [0.99, 0.01]], [0, 1], [0, 0])

    labels = cluster_embeddings(result, num_speakers=0)

    assert labels.tolist() == [0, 0]


def test_large_input_clusters_without_cubic_blowup():
    """Часовая запись не должна упираться в O(n^3) агломерацию."""
    rng = np.random.default_rng(17)
    windows = np.repeat(np.arange(400), 3)
    speakers = np.tile(np.arange(3), 400)
    centers = np.asarray(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32
    )
    embeddings = centers[speakers] + rng.normal(0.0, 0.01, size=(len(speakers), 3))
    result = _result(embeddings, windows, speakers)

    start = time.perf_counter()
    labels = cluster_embeddings(result, num_speakers=3)
    elapsed = time.perf_counter() - start

    assert sorted(set(labels.tolist())) == [0, 1, 2]
    assert elapsed < 10.0
