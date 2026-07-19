"""Constrained cosine agglomeration for local speaker embeddings."""

from __future__ import annotations

import numpy as np

from .onnx_embeddings import EmbeddingResult

_EPS = float(np.finfo(np.float32).eps)


def _unit_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, _EPS)


def cluster_embeddings(
    result: EmbeddingResult,
    *,
    num_speakers: int | None = None,
    threshold: float = 0.35,
) -> np.ndarray:
    """Cluster valid rows while forbidding two speakers from one window to merge.

    Запрошенное число спикеров трактуется как пожелание: если оно недостижимо
    (валидных строк меньше, либо cannot-link ограничения не дают слить кластеры
    до нужного количества), возвращается ближайший достижимый разбор. Ронять
    обработку целого файла из-за значения, выставленного в UI, нельзя.
    """

    valid_rows = np.flatnonzero(result.valid)
    labels = np.full(len(result.valid), -1, dtype=np.int64)
    if len(valid_rows) == 0:
        return labels

    count = len(valid_rows)
    target = None if num_speakers is None else max(1, min(int(num_speakers), count))

    sums = np.asarray(result.embeddings[valid_rows], dtype=np.float32).copy()
    centroids = _unit_rows(sums)
    windows = np.asarray(result.window_indices)[valid_rows]

    # Дистанции пересчитываются только для изменившегося кластера, поэтому
    # стоимость агломерации линейна по числу слияний, а не кубична.
    distances = (1.0 - centroids @ centroids.T).astype(np.float32)
    distances[windows[:, None] == windows[None, :]] = np.inf

    masks = [1 << int(window) for window in windows]
    members: list[list[int]] = [[int(row)] for row in valid_rows]
    active = np.ones(count, dtype=np.bool_)
    nearest = np.argmin(distances, axis=1)
    nearest_distance = distances[np.arange(count), nearest]

    remaining = count
    while remaining > 1:
        if target is not None and remaining <= target:
            break
        candidates = np.where(active, nearest_distance, np.inf)
        left = int(np.argmin(candidates))
        best = float(candidates[left])
        if not np.isfinite(best):
            break
        if target is None and best > threshold:
            break
        right = int(nearest[left])

        sums[left] += sums[right]
        centroids[left] = sums[left] / max(float(np.linalg.norm(sums[left])), _EPS)
        masks[left] |= masks[right]
        members[left].extend(members[right])
        active[right] = False
        remaining -= 1

        distances[right, :] = np.inf
        distances[:, right] = np.inf

        row = (1.0 - centroids @ centroids[left]).astype(np.float32)
        blocked = np.fromiter(
            ((masks[left] & mask) != 0 for mask in masks),
            dtype=np.bool_,
            count=count,
        )
        row[blocked | ~active] = np.inf
        row[left] = np.inf
        distances[left, :] = row
        distances[:, left] = row

        stale = np.flatnonzero(active & ((nearest == left) | (nearest == right)))
        for index in (*stale.tolist(), left):
            nearest[index] = int(np.argmin(distances[index]))
            nearest_distance[index] = distances[index, nearest[index]]

    clusters = [members[index] for index in np.flatnonzero(active)]
    clusters.sort(key=min)
    for label, cluster in enumerate(clusters):
        labels[cluster] = label
    return labels
