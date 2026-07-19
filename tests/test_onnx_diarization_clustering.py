import time
import warnings

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


def test_explicit_speaker_count_wins_over_cannot_link():
    """Явный запрос числа спикеров важнее эвристики cannot-link.

    Раньше здесь был ValueError, потом — молчаливый возврат лишних кластеров.
    На реальном интервью это превращало запрос «2 спикера» в 4.
    """
    result = _result([[1.0, 0.0], [0.0, 1.0]], [0, 0], [0, 1])

    labels = cluster_embeddings(result, num_speakers=1)

    assert labels.tolist() == [0, 0]


def test_cannot_link_still_holds_without_explicit_count():
    """Без запрошенного числа спикеров ограничение остаётся жёстким."""
    result = _result([[1.0, 0.0], [1.0, 0.0]], [0, 0], [0, 1])

    labels = cluster_embeddings(result, threshold=1.0)

    assert labels.tolist() == [0, 1]


def test_deadlocked_constraints_still_reach_requested_count():
    """Кластеры накапливают общие окна и упираются в тупик выше цели."""
    rng = np.random.default_rng(11)
    windows = np.repeat(np.arange(30), 2)
    speakers = np.tile(np.arange(2), 30)
    centers = np.asarray([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    embeddings = centers[speakers] + rng.normal(0.0, 0.2, size=(len(speakers), 3))
    result = _result(embeddings, windows, speakers)

    labels = cluster_embeddings(result, num_speakers=2)

    assert sorted(set(labels.tolist())) == [0, 1]


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


def test_clustering_is_silent_on_realistic_embedding_dimensions():
    """Accelerate BLAS сыпал RuntimeWarning на каждый файл — вывод должен быть чистым."""
    rng = np.random.default_rng(3)
    windows = np.repeat(np.arange(40), 3)
    speakers = np.tile(np.arange(3), 40)
    centers = rng.normal(size=(3, 256)).astype(np.float32)
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    embeddings = centers[speakers] + rng.normal(0.0, 0.01, size=(len(speakers), 256))
    embeddings = embeddings.astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    result = _result(embeddings, windows, speakers)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        labels = cluster_embeddings(result, num_speakers=3)

    assert sorted(set(labels.tolist())) == [0, 1, 2]
    assert [str(w.message) for w in caught if w.category is RuntimeWarning] == []


def test_non_finite_embeddings_do_not_poison_the_distance_matrix():
    rng = np.random.default_rng(5)
    embeddings = rng.normal(size=(6, 8)).astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings[2] = np.nan
    embeddings[4, 0] = np.inf
    result = _result(embeddings, [0, 1, 2, 3, 4, 5], np.zeros(6, dtype=int))

    labels = cluster_embeddings(result, num_speakers=2)

    assert set(labels.tolist()) == {0, 1}
