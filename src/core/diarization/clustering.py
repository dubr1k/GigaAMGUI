"""Constrained cosine agglomeration for local speaker embeddings."""

from __future__ import annotations

import numpy as np

from .onnx_embeddings import EmbeddingResult


def _cosine_distance(left: np.ndarray, right: np.ndarray) -> float:
    left_norm = float(np.linalg.norm(left))
    right_norm = float(np.linalg.norm(right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 1.0
    return 1.0 - float(np.dot(left, right) / (left_norm * right_norm))


def cluster_embeddings(
    result: EmbeddingResult,
    *,
    num_speakers: int | None = None,
    threshold: float = 0.35,
) -> np.ndarray:
    """Cluster valid rows while forbidding two speakers from one window to merge."""
    valid_rows = np.flatnonzero(result.valid)
    labels = np.full(len(result.valid), -1, dtype=np.int64)
    if len(valid_rows) == 0:
        return labels
    if num_speakers is not None and (num_speakers < 1 or num_speakers > len(valid_rows)):
        raise ValueError("Некорректное число спикеров для clustering")

    clusters: list[set[int]] = [{int(row)} for row in valid_rows]

    def centroid(cluster: set[int]) -> np.ndarray:
        value = result.embeddings[sorted(cluster)].mean(axis=0)
        norm = np.linalg.norm(value)
        return value if norm == 0 else value / norm

    def allowed(left: set[int], right: set[int]) -> bool:
        left_windows = set(result.window_indices[list(left)])
        right_windows = set(result.window_indices[list(right)])
        return left_windows.isdisjoint(right_windows)

    while True:
        candidate: tuple[float, int, int] | None = None
        for left_index in range(len(clusters)):
            for right_index in range(left_index + 1, len(clusters)):
                if not allowed(clusters[left_index], clusters[right_index]):
                    continue
                distance = _cosine_distance(
                    centroid(clusters[left_index]),
                    centroid(clusters[right_index]),
                )
                if candidate is None or distance < candidate[0]:
                    candidate = (distance, left_index, right_index)

        if num_speakers is not None:
            if len(clusters) <= num_speakers:
                break
            if candidate is None:
                raise ValueError("Невозможно выполнить clustering: cannot-link constraints")
        elif candidate is None or candidate[0] > threshold:
            break

        _, left_index, right_index = candidate
        clusters[left_index] |= clusters[right_index]
        clusters.pop(right_index)

    clusters.sort(key=lambda cluster: min(cluster))
    for label, cluster in enumerate(clusters):
        labels[list(cluster)] = label
    return labels
