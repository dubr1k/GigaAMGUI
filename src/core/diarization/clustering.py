"""Constrained cosine agglomeration for local speaker embeddings."""

from __future__ import annotations

import numpy as np

from .onnx_embeddings import EmbeddingResult

_EPS = float(np.finfo(np.float32).eps)


def _unit_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, _EPS)


def _cosine_distance(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """1 - косинусное сходство для строк единичной длины.

    Accelerate BLAS на Apple Silicon выставляет FPU-флаги overflow/invalid на
    полностью корректных входах (SIMD считает и padding-дорожки), из-за чего
    numpy печатал RuntimeWarning на каждый файл. Входы санитизируются до
    вызова, поэтому флаги здесь заведомо ложные и глушатся точечно.
    """
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        return (1.0 - left @ right).astype(np.float32)


def _closest_active_pair(
    centroids: np.ndarray,
    active: np.ndarray,
) -> tuple[int, int] | None:
    """Ближайшая пара активных кластеров без учёта cannot-link."""
    active_index = np.flatnonzero(active)
    if len(active_index) < 2:
        return None
    subset = centroids[active_index]
    distances = _cosine_distance(subset, subset.T)
    np.fill_diagonal(distances, np.inf)
    flat = int(np.argmin(distances))
    left, right = divmod(flat, len(active_index))
    return int(active_index[left]), int(active_index[right])


def cluster_embeddings(
    result: EmbeddingResult,
    *,
    num_speakers: int | None = None,
    threshold: float = 0.6,
) -> np.ndarray:
    """Cluster valid rows while forbidding two speakers from one window to merge.

    Порог подобран под эмбеддинги wespeaker-voxceleb-resnet34: на реальном
    двухголосом интервью медиана попарных дистанций около 0.35, поэтому прежнее
    значение 0.35 резало одного спикера на несколько кластеров.

    Явно заданное число спикеров выполняется всегда. Cannot-link (два локальных
    спикера одного окна) — эвристика, и когда она заводит слияния в тупик выше
    запрошенного количества, ограничение ослабляется: пожелание пользователя
    важнее эвристики. Без этого запрос «2 спикера» молча возвращал 4.
    """

    valid_rows = np.flatnonzero(result.valid)
    labels = np.full(len(result.valid), -1, dtype=np.int64)
    if len(valid_rows) == 0:
        return labels

    count = len(valid_rows)
    target = None if num_speakers is None else max(1, min(int(num_speakers), count))

    sums = np.asarray(result.embeddings[valid_rows], dtype=np.float32).copy()
    if not np.isfinite(sums).all():
        # Нечисловой эмбеддинг мог бы разъехаться по всей матрице дистанций,
        # а глушение FPU-флагов ниже скрыло бы это.
        sums = np.nan_to_num(sums, nan=0.0, posinf=0.0, neginf=0.0)
    centroids = _unit_rows(sums)
    windows = np.asarray(result.window_indices)[valid_rows]

    # Дистанции пересчитываются только для изменившегося кластера, поэтому
    # стоимость агломерации линейна по числу слияний, а не кубична.
    distances = _cosine_distance(centroids, centroids.T)
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
        if np.isfinite(best):
            if target is None and best > threshold:
                break
            right = int(nearest[left])
        else:
            # Разрешённых слияний не осталось: кластеры накопили общие окна.
            # Без цели останавливаемся, с целью — ослабляем cannot-link.
            if target is None:
                break
            forced = _closest_active_pair(centroids, active)
            if forced is None:
                break
            left, right = forced

        sums[left] += sums[right]
        centroids[left] = sums[left] / max(float(np.linalg.norm(sums[left])), _EPS)
        masks[left] |= masks[right]
        members[left].extend(members[right])
        active[right] = False
        remaining -= 1

        distances[right, :] = np.inf
        distances[:, right] = np.inf

        row = _cosine_distance(centroids, centroids[left])
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
