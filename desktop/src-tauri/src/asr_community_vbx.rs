//! Community-1 clustering from the pyannote.audio 4.0.7 VBx pipeline.
//!
//! Adapted from pyannote.audio 4.0.7:
//! `src/pyannote/audio/utils/vbx.py` (Apache License, Version 2.0),
//! itself derived from BUTSpeechFIT/VBx. The original revision history credits
//! L. Burget, P. Pálka and H. Bredin. This version translates the algorithm to
//! Rust and integrates it with the app's model and cancellation handling.
//!
//! Licensed under the Apache License, Version 2.0 (the "License");
//! you may not use this file except in compliance with the License.
//! You may obtain a copy of the License at
//!
//!     http://www.apache.org/licenses/LICENSE-2.0
//!
//! Unless required by applicable law or agreed to in writing, software
//! distributed under the License is distributed on an "AS IS" BASIS,
//! WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//! See the License for the specific language governing permissions and
//! limitations under the License.
//!
//! PLDA preprocessing and constrained clustering also follow the MIT-licensed
//! pyannote.audio `core/plda.py` and `pipelines/clustering.py`:
//!
//! Copyright (c) 2021-2025 CNRS
//! Copyright (c) 2025- pyannoteAI
//!
//! Permission is hereby granted, free of charge, to any person obtaining a copy
//! of this software and associated documentation files (the "Software"), to deal
//! in the Software without restriction, including without limitation the rights
//! to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
//! copies of the Software, and to permit persons to whom the Software is
//! furnished to do so, subject to the following conditions:
//!
//! The above copyright notice and this permission notice shall be included in all
//! copies or substantial portions of the Software.
//!
//! THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
//! IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
//! FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
//! AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
//! LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
//! OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
//! SOFTWARE.

use std::fs::File;
use std::io::BufReader;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};

use serde::Deserialize;

const LOCAL_SPEAKERS: usize = 3;
const LDA_DIM: usize = 128;
const AHC_THRESHOLD: f64 = 0.6;
const FA: f64 = 0.07;
const FB: f64 = 0.8;

#[derive(Deserialize)]
pub(super) struct Plda {
    mean1: Vec<f64>,
    mean2: Vec<f64>,
    lda: Vec<Vec<f64>>,
    mu: Vec<f64>,
    tr: Vec<Vec<f64>>,
    phi: Vec<f64>,
}

/// Counts and raw (not unit-normalized) embeddings for one 10-second chunk.
/// `clean` counts single-speaker frames, even when overlap was included in the
/// mask used to extract an embedding.
pub(super) struct Chunk {
    pub embeddings: [Option<Vec<f32>>; LOCAL_SPEAKERS],
    pub active: [usize; LOCAL_SPEAKERS],
    pub clean: [usize; LOCAL_SPEAKERS],
}

fn cancelled(flag: &AtomicBool) -> Result<(), String> {
    if flag.load(Ordering::Acquire) {
        Err("Диаризация отменена".into())
    } else {
        Ok(())
    }
}

impl Plda {
    pub(super) fn load(path: &Path) -> Result<Self, String> {
        let file = File::open(path).map_err(|e| format!("Не удалось открыть PLDA: {e}"))?;
        let plda: Self = serde_json::from_reader(BufReader::new(file))
            .map_err(|e| format!("Некорректный PLDA: {e}"))?;
        plda.validate()?;
        Ok(plda)
    }

    fn validate(&self) -> Result<(), String> {
        let input = self.mean1.len();
        let projected = self.mean2.len();
        if input != 256 || projected < LDA_DIM || projected > input
            || self.lda.len() != input
            || self.lda.iter().any(|row| row.len() != projected)
            || self.mu.len() != projected
            || self.tr.len() != LDA_DIM
            || self.tr.iter().any(|row| row.len() != projected)
            || self.phi.len() != LDA_DIM
            || self.phi.iter().any(|x| !x.is_finite() || *x < 0.0)
            || self.mean1.iter().chain(&self.mean2).chain(&self.mu)
                .chain(self.lda.iter().flatten()).chain(self.tr.iter().flatten())
                .any(|x| !x.is_finite())
        {
            return Err("Неверные размеры или значения PLDA Community-1".into());
        }
        Ok(())
    }

    fn project(&self, embedding: &[f32]) -> Result<Vec<f64>, String> {
        if embedding.len() != self.mean1.len() || embedding.iter().any(|x| !x.is_finite()) {
            return Err("Некорректный вектор говорящего Community-1".into());
        }
        let mut centered: Vec<f64> = embedding.iter().zip(&self.mean1)
            .map(|(x, mean)| f64::from(*x) - mean).collect();
        normalize_to(&mut centered, (self.mean1.len() as f64).sqrt())?;
        let mut transformed = vec![0.0; self.mean2.len()];
        for (value, row) in centered.iter().zip(&self.lda) {
            for (out, weight) in transformed.iter_mut().zip(row) {
                *out += value * weight;
            }
        }
        for (value, mean) in transformed.iter_mut().zip(&self.mean2) {
            *value -= mean;
        }
        normalize_to(&mut transformed, (self.mean2.len() as f64).sqrt())?;
        for (value, mean) in transformed.iter_mut().zip(&self.mu) {
            *value -= mean;
        }
        let result: Vec<_> = self.tr.iter().map(|row| dot(row, &transformed)).collect();
        if result.iter().any(|value| !value.is_finite()) {
            return Err("PLDA выдал некорректный вектор".into());
        }
        Ok(result)
    }
}

fn dot(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}

fn normalize_to(values: &mut [f64], scale: f64) -> Result<(), String> {
    let norm = dot(values, values).sqrt();
    if !norm.is_finite() || norm <= 0.0 {
        return Err("Нулевой или некорректный вектор PLDA".into());
    }
    for value in values {
        *value *= scale / norm;
    }
    Ok(())
}

fn distance2(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b).map(|(x, y)| (x - y) * (x - y)).sum()
}

/// Centroid-linkage AHC, cut at the configured distance. An above-threshold
/// merge cannot later produce a cluster whose maximum descendant merge height
/// is below threshold, including in the presence of centroid inversions.
/// Tracking each active cluster's nearest neighbour avoids a quadratic heap.
fn centroid_ahc(mut centers: Vec<Vec<f64>>, flag: &AtomicBool) -> Result<Vec<usize>, String> {
    let n = centers.len();
    let mut members: Vec<Vec<usize>> = (0..n).map(|i| vec![i]).collect();
    let mut alive = vec![true; n];
    let mut nearest = vec![None; n];
    let mut best = vec![f64::INFINITY; n];

    for i in 0..n {
        if i % 32 == 0 { cancelled(flag)?; }
        for j in (i + 1)..n {
            let d = distance2(&centers[i], &centers[j]);
            if d < best[i] { best[i] = d; nearest[i] = Some(j); }
            if d < best[j] { best[j] = d; nearest[j] = Some(i); }
        }
    }
    loop {
        cancelled(flag)?;
        let Some(i) = (0..n).filter(|&i| alive[i] && nearest[i].is_some())
            .min_by(|&a, &b| best[a].total_cmp(&best[b]).then(a.cmp(&b))) else { break };
        if best[i] > AHC_THRESHOLD * AHC_THRESHOLD { break; }
        let j = nearest[i].ok_or("Ошибка кластеризации")?;
        let left = members[i].len();
        let right = members[j].len();
        for k in 0..centers[i].len() {
            centers[i][k] = (centers[i][k] * left as f64 + centers[j][k] * right as f64)
                / (left + right) as f64;
        }
        let moved = std::mem::take(&mut members[j]);
        members[i].extend(moved);
        alive[j] = false;
        nearest[j] = None;
        best[j] = f64::INFINITY;
        nearest[i] = None;
        best[i] = f64::INFINITY;
        for k in 0..n {
            if !alive[k] || k == i { continue; }
            if k % 128 == 0 { cancelled(flag)?; }
            let d = distance2(&centers[i], &centers[k]);
            if d < best[i] { best[i] = d; nearest[i] = Some(k); }
            if nearest[k] == Some(i) || nearest[k] == Some(j) {
                best[k] = f64::INFINITY;
                nearest[k] = None;
                for m in 0..n {
                    if alive[m] && m != k {
                        let candidate = if m == i { d } else { distance2(&centers[k], &centers[m]) };
                        if candidate < best[k] { best[k] = candidate; nearest[k] = Some(m); }
                    }
                }
            } else if d < best[k] {
                best[k] = d;
                nearest[k] = Some(i);
            }
        }
    }
    let mut labels = vec![0; n];
    let mut label = 0;
    for group in members {
        if !group.is_empty() {
            for i in group { labels[i] = label; }
            label += 1;
        }
    }
    Ok(labels)
}

/// Return per-observation posteriors and the surviving-component indices.
fn vbx(features: &[Vec<f64>], phi: &[f64], labels: &[usize], flag: &AtomicBool)
    -> Result<(Vec<Vec<f64>>, Vec<usize>), String>
{
    let n = features.len();
    let speakers = labels.iter().copied().max().ok_or("Нет данных для VBx")? + 1;
    let dims = phi.len();
    let smooth = 7.0f64.exp();
    let denominator = smooth + speakers as f64 - 1.0;
    let mut gamma = vec![vec![1.0 / denominator; speakers]; n];
    for (row, &label) in gamma.iter_mut().zip(labels) { row[label] = smooth / denominator; }
    let mut priors = vec![1.0 / speakers as f64; speakers];
    let sqrt_phi: Vec<_> = phi.iter().map(|x| x.sqrt()).collect();
    let rho: Vec<Vec<f64>> = features.iter().map(|row| row.iter().zip(&sqrt_phi)
        .map(|(x, v)| x * v).collect()).collect();
    let constant: Vec<_> = features.iter().map(|row|
        -0.5 * (dot(row, row) + dims as f64 * (2.0 * std::f64::consts::PI).ln())).collect();
    let mut previous = None;
    let mut inverse = vec![vec![0.0; dims]; speakers];
    let mut alpha = vec![vec![0.0; dims]; speakers];
    for _ in 0..20 {
        cancelled(flag)?;
        let mut sums = vec![0.0; speakers];
        for row in &gamma {
            for (sum, value) in sums.iter_mut().zip(row) { *sum += value; }
        }
        for s in 0..speakers {
            for d in 0..dims {
                inverse[s][d] = 1.0 / (1.0 + FA / FB * sums[s] * phi[d]);
                let sufficient: f64 = gamma.iter().zip(&rho).map(|(g, r)| g[s] * r[d]).sum();
                alpha[s][d] = FA / FB * inverse[s][d] * sufficient;
            }
        }
        let mut likelihood = 0.0;
        for (t, row) in gamma.iter_mut().enumerate() {
            let mut maximum = f64::NEG_INFINITY;
            for s in 0..speakers {
                let penalty: f64 = (0..dims).map(|d|
                    (inverse[s][d] + alpha[s][d] * alpha[s][d]) * phi[d]).sum();
                row[s] = FA * (dot(&rho[t], &alpha[s]) - 0.5 * penalty + constant[t])
                    + (priors[s] + 1e-8).ln();
                maximum = maximum.max(row[s]);
            }
            let sum: f64 = row.iter().map(|x| (x - maximum).exp()).sum();
            let normalizer = maximum + sum.ln();
            if !normalizer.is_finite() { return Err("VBx выдал некорректные вероятности".into()); }
            likelihood += normalizer;
            for value in row { *value = (*value - normalizer).exp(); }
        }
        priors.fill(0.0);
        for row in &gamma {
            for (prior, value) in priors.iter_mut().zip(row) { *prior += value; }
        }
        for prior in &mut priors { *prior /= n as f64; }
        let regularization: f64 = (0..speakers).flat_map(|s| (0..dims).map(move |d| (s, d)))
            .map(|(s, d)| inverse[s][d].ln() - inverse[s][d] - alpha[s][d] * alpha[s][d] + 1.0)
            .sum();
        let elbo = likelihood + FB * 0.5 * regularization;
        if !elbo.is_finite() { return Err("VBx не сошёлся: некорректное значение".into()); }
        if previous.is_some_and(|last| elbo - last < 1e-4) { break; }
        previous = Some(elbo);
    }
    let kept: Vec<_> = priors.iter().enumerate().filter_map(|(i, &p)| (p > 1e-7).then_some(i)).collect();
    if kept.is_empty() { return Err("VBx не обнаружил кластеров".into()); }
    Ok((gamma, kept))
}

/// Minimum-cost rectangular assignment (rows <= columns), used with three
/// local speakers or fewer. Returns the selected column for each row.
fn assignment(cost: &[Vec<f64>]) -> Vec<usize> {
    let rows = cost.len();
    let cols = cost[0].len();
    let mut u = vec![0.0; rows + 1];
    let mut v = vec![0.0; cols + 1];
    let mut p = vec![0usize; cols + 1];
    let mut way = vec![0usize; cols + 1];
    for i in 1..=rows {
        p[0] = i;
        let mut j0 = 0;
        let mut min = vec![f64::INFINITY; cols + 1];
        let mut used = vec![false; cols + 1];
        loop {
            used[j0] = true;
            let i0 = p[j0];
            let mut delta = f64::INFINITY;
            let mut j1 = 0;
            for j in 1..=cols {
                if !used[j] {
                    let cur = cost[i0 - 1][j - 1] - u[i0] - v[j];
                    if cur < min[j] { min[j] = cur; way[j] = j0; }
                    if min[j] < delta { delta = min[j]; j1 = j; }
                }
            }
            for j in 0..=cols {
                if used[j] { u[p[j]] += delta; v[j] -= delta; }
                else { min[j] -= delta; }
            }
            j0 = j1;
            if p[j0] == 0 { break; }
        }
        loop {
            let j1 = way[j0];
            p[j0] = p[j1];
            j0 = j1;
            if j0 == 0 { break; }
        }
    }
    let mut result = vec![0; rows];
    for j in 1..=cols { if p[j] > 0 { result[p[j] - 1] = j - 1; } }
    result
}

/// Returns global-cluster assignments for each local speaker, or `None` for
/// inactive/unassigned slots. Frame-level reconstruction must still apply the
/// independently aggregated speaker count, as in the upstream pipeline.
pub(super) fn cluster(plda: &Plda, chunks: &[Chunk], frames: usize, flag: &AtomicBool)
    -> Result<Vec<[Option<usize>; LOCAL_SPEAKERS]>, String>
{
    if frames == 0 { return Err("Пустая сегментация Community-1".into()); }
    let mut train = Vec::new();
    for chunk in chunks {
        for s in 0..LOCAL_SPEAKERS {
            if chunk.clean[s] > chunk.active[s] || chunk.active[s] > frames {
                return Err("Некорректное число кадров говорящего".into());
            }
            if chunk.clean[s] * 5 >= frames {
                if let Some(vector) = &chunk.embeddings[s] {
                    if vector.len() != plda.mean1.len() { return Err("Некорректная размерность эмбеддинга".into()); }
                    if vector.iter().all(|v| v.is_finite()) { train.push(vector.as_slice()); }
                }
            }
        }
    }
    if train.len() < 2 {
        return Ok(chunks.iter().map(|c| std::array::from_fn(|s|
            (c.active[s] > 0).then_some(0))).collect());
    }
    let mut normalized = Vec::with_capacity(train.len());
    let mut features = Vec::with_capacity(train.len());
    for embedding in &train {
        cancelled(flag)?;
        let mut vector: Vec<_> = embedding.iter().map(|v| f64::from(*v)).collect();
        normalize_to(&mut vector, 1.0)?;
        normalized.push(vector);
        features.push(plda.project(embedding)?);
    }
    let initial = centroid_ahc(normalized, flag)?;
    let (gamma, kept) = vbx(&features, &plda.phi, &initial, flag)?;
    let mut centroids = vec![vec![0.0; plda.mean1.len()]; kept.len()];
    let mut weights = vec![0.0; kept.len()];
    for (i, embedding) in train.iter().enumerate() {
        for (k, &s) in kept.iter().enumerate() {
            let weight = gamma[i][s];
            weights[k] += weight;
            for (out, value) in centroids[k].iter_mut().zip(*embedding) {
                *out += weight * f64::from(*value);
            }
        }
    }
    for (centroid, weight) in centroids.iter_mut().zip(weights) {
        if weight <= 0.0 { return Err("Пустой кластер VBx".into()); }
        for value in centroid.iter_mut() { *value /= weight; }
        normalize_to(centroid, 1.0)?;
    }
    let mut result = Vec::with_capacity(chunks.len());
    for chunk in chunks {
        cancelled(flag)?;
        let mut scores = vec![vec![f64::NAN; centroids.len()]; LOCAL_SPEAKERS];
        let mut minimum = f64::INFINITY;
        for (s, row) in scores.iter_mut().enumerate() {
            if let Some(vector) = &chunk.embeddings[s] {
                if vector.len() != plda.mean1.len() { return Err("Некорректная размерность эмбеддинга".into()); }
                let norm = vector.iter().map(|x| f64::from(*x).powi(2)).sum::<f64>().sqrt();
                if norm.is_finite() && norm > 0.0 {
                    for (value, centroid) in row.iter_mut().zip(&centroids) {
                        *value = 1.0 + vector.iter().zip(centroid)
                            .map(|(x, c)| f64::from(*x) * c / norm).sum::<f64>();
                        minimum = minimum.min(*value);
                    }
                }
            }
        }
        if !minimum.is_finite() { minimum = -1.0; }
        for (s, row) in scores.iter_mut().enumerate() {
            for score in row {
                if chunk.active[s] == 0 { *score = minimum - 1.0; }
                else if !score.is_finite() { *score = minimum - 1.0; }
            }
        }
        let mut assigned = [None; LOCAL_SPEAKERS];
        if centroids.len() >= LOCAL_SPEAKERS {
            let costs: Vec<Vec<f64>> = scores.iter().map(|row| row.iter().map(|x| -*x).collect()).collect();
            for (s, k) in assignment(&costs).into_iter().enumerate() {
                if chunk.active[s] > 0 { assigned[s] = Some(k); }
            }
        } else {
            let costs: Vec<Vec<f64>> = (0..centroids.len()).map(|k|
                (0..LOCAL_SPEAKERS).map(|s| -scores[s][k]).collect()).collect();
            for (k, s) in assignment(&costs).into_iter().enumerate() {
                if chunk.active[s] > 0 { assigned[s] = Some(k); }
            }
        }
        result.push(assigned);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn centroid_linkage_merges_only_close_vectors() {
        let flag = AtomicBool::new(false);
        let vectors = vec![vec![1.0, 0.0], vec![0.98, 0.1], vec![0.0, 1.0]];
        let labels = centroid_ahc(vectors, &flag).unwrap();
        assert_eq!(labels[0], labels[1]);
        assert_ne!(labels[0], labels[2]);
    }

    #[test]
    fn rectangular_assignment_is_global_not_greedy() {
        let costs = vec![vec![-10.0, -9.0, 0.0], vec![-9.5, 0.0, 0.0]];
        assert_eq!(assignment(&costs), vec![1, 0]);
    }

    #[test]
    fn vbx_posteriors_are_normalized() {
        let flag = AtomicBool::new(false);
        let (gamma, kept) = vbx(&[vec![2.0, 0.0], vec![2.1, 0.0], vec![0.0, 2.0]],
            &[1.0, 1.0], &[0, 0, 1], &flag).unwrap();
        assert!(!kept.is_empty());
        for row in gamma { assert!((row.iter().sum::<f64>() - 1.0).abs() < 1e-10); }
    }

    #[test]
    fn clustering_honours_cancellation() {
        let flag = AtomicBool::new(true);
        assert!(centroid_ahc(vec![vec![1.0]], &flag).is_err());
    }
}
