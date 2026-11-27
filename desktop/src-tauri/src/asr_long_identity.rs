//! Bounded, session-local matching of *already extracted* speaker embeddings.
//!
//! This is not a diarizer: it does not detect speech, overlap, segment quality,
//! or extract embeddings. The caller must establish that `SingleSpeaker` really
//! contains one sufficiently clear speaker; otherwise it must submit `Overlap`
//! or `Ambiguous`. In particular, an embedding alone cannot prove that two
//! acoustically similar people are the same person. IDs refer to centroids, not
//! verified real-world identities.
//!
//! The input shape follows polyvoice's public `Embedder` contract: `dim()` is
//! constant and `embed()` returns an L2-normalized `Vec<f32>`:
//! <https://docs.rs/polyvoice/latest/polyvoice/embedder/trait.Embedder.html>.
//! This module needs only the returned slice and does not invoke a model.
//!
//! Thresholds are supplied by the caller and must be calibrated for its model
//! and data. The unit-test values and the fixed centroid update weight are not
//! validated diarization quality. The caller must also check cancellation before
//! and after each synchronous call and discard the session and partial output if
//! cancelled; this tracker does not implement cancellation or publish results.

const MAX_SPEAKERS: usize = 256;
const MAX_EMBEDDING_DIM: usize = 4096;
// Permit ordinary floating-point normalization error, not arbitrary magnitudes.
const UNIT_NORM_TOLERANCE: f64 = 1e-3;
const OBSERVATION_WEIGHT: f64 = 0.1;

/// All cosine thresholds are strictly applied: equality is unresolved.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct TrackerConfig {
    pub(crate) max_speakers: usize,
    pub(crate) embedding_dim: usize,
    /// Above this similarity, a match is possible if its lead is sufficient.
    pub(crate) match_threshold: f32,
    /// Below this similarity to every centroid, a new ID may be created.
    pub(crate) new_speaker_threshold: f32,
    /// The best score must lead the runner-up by strictly more than this.
    pub(crate) ambiguity_margin: f32,
}

/// Classification supplied by a separate, reliable audio segmentation stage.
#[derive(Clone, Copy, Debug)]
pub(crate) enum SpeakerEvidence<'a> {
    SingleSpeaker(&'a [f32]),
    Ambiguous,
    Overlap,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum SpeakerAssignment {
    /// IDs start at zero and are never evicted, reused, or renumbered in a session.
    Known(u32),
    Unknown,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum TrackerError {
    InvalidConfiguration,
    InvalidEmbedding,
}

#[cfg_attr(test, derive(Clone, Debug, PartialEq))]
struct Centroid {
    values: Vec<f32>,
    // Cached norm makes scoring a true cosine even after f32 rounding.
    norm: f64,
}

/// Stores at most `max_speakers * embedding_dim` floats for a single session.
/// Each observation costs O(max_speakers * embedding_dim); no history is kept.
#[cfg_attr(test, derive(Clone, Debug, PartialEq))]
pub(crate) struct SpeakerTracker {
    config: TrackerConfig,
    centroids: Vec<Centroid>,
}

impl SpeakerTracker {
    pub(crate) fn new(config: TrackerConfig) -> Result<Self, TrackerError> {
        if config.max_speakers == 0
            || config.max_speakers > MAX_SPEAKERS
            || config.embedding_dim == 0
            || config.embedding_dim > MAX_EMBEDDING_DIM
            || !(-1.0..=1.0).contains(&config.new_speaker_threshold)
            || !(-1.0..=1.0).contains(&config.match_threshold)
            || config.new_speaker_threshold >= config.match_threshold
            || !(0.0..=2.0).contains(&config.ambiguity_margin)
            || config.ambiguity_margin == 0.0
        {
            return Err(TrackerError::InvalidConfiguration);
        }
        Ok(Self {
            config,
            centroids: Vec::with_capacity(config.max_speakers),
        })
    }

    /// Assign a clean embedding or return Unknown without modifying the tracker.
    ///
    /// A best score below `new_speaker_threshold` creates a speaker only while
    /// space remains. A score above `match_threshold` matches only when its
    /// lead over the second-best score exceeds `ambiguity_margin`. Everything
    /// between those boundaries, including a tie, is Unknown. At capacity an
    /// unmatched embedding is Unknown, never forced into the nearest centroid.
    /// Malformed embeddings return an error without changing any state.
    pub(crate) fn assign(
        &mut self,
        evidence: SpeakerEvidence<'_>,
    ) -> Result<SpeakerAssignment, TrackerError> {
        let embedding = match evidence {
            SpeakerEvidence::SingleSpeaker(embedding) => embedding,
            SpeakerEvidence::Ambiguous | SpeakerEvidence::Overlap => {
                return Ok(SpeakerAssignment::Unknown);
            }
        };
        if embedding.len() != self.config.embedding_dim {
            return Err(TrackerError::InvalidEmbedding);
        }
        let mut squared_norm = 0.0_f64;
        for &value in embedding {
            if !value.is_finite() {
                return Err(TrackerError::InvalidEmbedding);
            }
            let value = f64::from(value);
            squared_norm += value * value;
        }
        let norm = squared_norm.sqrt();
        if (norm - 1.0).abs() > UNIT_NORM_TOLERANCE {
            return Err(TrackerError::InvalidEmbedding);
        }

        if self.centroids.is_empty() {
            return Ok(self.insert(embedding, norm));
        }

        let mut best_index = 0;
        let mut best = f64::NEG_INFINITY;
        let mut second = f64::NEG_INFINITY;
        for (index, centroid) in self.centroids.iter().enumerate() {
            let dot: f64 = embedding
                .iter()
                .zip(&centroid.values)
                .map(|(&a, &b)| f64::from(a) * f64::from(b))
                .sum();
            let similarity = (dot / (norm * centroid.norm)).clamp(-1.0, 1.0);
            if similarity > best {
                second = best;
                best = similarity;
                best_index = index;
            } else if similarity > second {
                second = similarity;
            }
        }

        if best < f64::from(self.config.new_speaker_threshold) {
            return if self.centroids.len() < self.config.max_speakers {
                Ok(self.insert(embedding, norm))
            } else {
                Ok(SpeakerAssignment::Unknown)
            };
        }
        if best <= f64::from(self.config.match_threshold)
            || best - second <= f64::from(self.config.ambiguity_margin)
        {
            return Ok(SpeakerAssignment::Unknown);
        }

        // Directional exponential mean of clean, unambiguous observations.
        // Compute the norm from the stored f32 values, not pre-rounding values.
        let centroid = &mut self.centroids[best_index];
        let old_weight = (1.0 - OBSERVATION_WEIGHT) / centroid.norm;
        let new_weight = OBSERVATION_WEIGHT / norm;
        let mut updated_squared_norm = 0.0_f64;
        for (stored, &incoming) in centroid.values.iter_mut().zip(embedding) {
            *stored = (f64::from(*stored) * old_weight + f64::from(incoming) * new_weight) as f32;
            let value = f64::from(*stored);
            updated_squared_norm += value * value;
        }
        centroid.norm = updated_squared_norm.sqrt();
        Ok(SpeakerAssignment::Known(best_index as u32))
    }

    fn insert(&mut self, embedding: &[f32], norm: f64) -> SpeakerAssignment {
        let id = self.centroids.len() as u32;
        self.centroids.push(Centroid {
            values: embedding.to_vec(),
            norm,
        });
        SpeakerAssignment::Known(id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const A: [f32; 3] = [1.0, 0.0, 0.0];
    const B: [f32; 3] = [0.0, 1.0, 0.0];
    const C: [f32; 3] = [0.0, 0.0, 1.0];
    const MIDPOINT_AB: [f32; 3] = [std::f32::consts::FRAC_1_SQRT_2, std::f32::consts::FRAC_1_SQRT_2, 0.0];

    // These are deterministic test inputs, not calibrated model thresholds.
    fn config(max_speakers: usize) -> TrackerConfig {
        TrackerConfig {
            max_speakers,
            embedding_dim: 3,
            match_threshold: 0.7,
            new_speaker_threshold: 0.3,
            ambiguity_margin: 0.2,
        }
    }

    fn clean(tracker: &mut SpeakerTracker, embedding: &[f32]) -> SpeakerAssignment {
        tracker.assign(SpeakerEvidence::SingleSpeaker(embedding)).unwrap()
    }

    #[test]
    fn returning_speakers_keep_their_session_ids() {
        let mut tracker = SpeakerTracker::new(config(3)).unwrap();
        assert_eq!(clean(&mut tracker, &A), SpeakerAssignment::Known(0));
        assert_eq!(clean(&mut tracker, &B), SpeakerAssignment::Known(1));
        assert_eq!(clean(&mut tracker, &C), SpeakerAssignment::Known(2));
        assert_eq!(clean(&mut tracker, &A), SpeakerAssignment::Known(0));
        assert_eq!(clean(&mut tracker, &C), SpeakerAssignment::Known(2));
        assert_eq!(clean(&mut tracker, &B), SpeakerAssignment::Known(1));
    }

    #[test]
    fn cosine_conflict_is_unknown_and_clear_winner_uses_the_closest_centroid() {
        let mut tracker = SpeakerTracker::new(config(2)).unwrap();
        assert_eq!(clean(&mut tracker, &A), SpeakerAssignment::Known(0));
        assert_eq!(clean(&mut tracker, &B), SpeakerAssignment::Known(1));
        let before = tracker.clone();
        // Both scores exceed the match threshold, but neither leads.
        assert_eq!(clean(&mut tracker, &MIDPOINT_AB), SpeakerAssignment::Unknown);
        assert_eq!(tracker, before);
        // This vector is closer to B even though A was inserted first.
        assert_eq!(clean(&mut tracker, &[0.28, 0.96, 0.0]), SpeakerAssignment::Known(1));
        assert_ne!(tracker, before);
    }

    #[test]
    fn similar_voice_in_the_decision_band_is_unknown_without_creating_or_updating() {
        let mut cfg = config(3);
        cfg.match_threshold = 0.9;
        cfg.new_speaker_threshold = 0.6;
        let mut tracker = SpeakerTracker::new(cfg).unwrap();
        assert_eq!(clean(&mut tracker, &A), SpeakerAssignment::Known(0));
        let before = tracker.clone();
        assert_eq!(clean(&mut tracker, &[0.8, 0.6, 0.0]), SpeakerAssignment::Unknown);
        assert_eq!(tracker, before);
        assert_eq!(clean(&mut tracker, &B), SpeakerAssignment::Known(1));
    }

    #[test]
    fn capacity_never_forces_a_new_voice_into_an_existing_centroid() {
        let mut tracker = SpeakerTracker::new(config(1)).unwrap();
        assert_eq!(clean(&mut tracker, &A), SpeakerAssignment::Known(0));
        let before = tracker.clone();
        assert_eq!(clean(&mut tracker, &B), SpeakerAssignment::Unknown);
        assert_eq!(clean(&mut tracker, &C), SpeakerAssignment::Unknown);
        assert_eq!(tracker, before);
        assert_eq!(clean(&mut tracker, &A), SpeakerAssignment::Known(0));
    }

    #[test]
    fn overlap_and_ambiguous_evidence_never_create_or_change_a_centroid() {
        let mut tracker = SpeakerTracker::new(config(2)).unwrap();
        let empty = tracker.clone();
        assert_eq!(tracker.assign(SpeakerEvidence::Overlap), Ok(SpeakerAssignment::Unknown));
        assert_eq!(tracker.assign(SpeakerEvidence::Ambiguous), Ok(SpeakerAssignment::Unknown));
        assert_eq!(tracker, empty);
        assert_eq!(clean(&mut tracker, &A), SpeakerAssignment::Known(0));
        let before = tracker.clone();
        assert_eq!(tracker.assign(SpeakerEvidence::Overlap), Ok(SpeakerAssignment::Unknown));
        assert_eq!(tracker.assign(SpeakerEvidence::Ambiguous), Ok(SpeakerAssignment::Unknown));
        assert_eq!(tracker, before);
        assert_eq!(clean(&mut tracker, &B), SpeakerAssignment::Known(1));
    }

    #[test]
    fn invalid_embeddings_are_errors_and_cannot_change_state() {
        let mut tracker = SpeakerTracker::new(config(2)).unwrap();
        assert_eq!(clean(&mut tracker, &A), SpeakerAssignment::Known(0));
        let before = tracker.clone();
        for invalid in [
            &[1.0, 0.0][..],
            &[0.0, 0.0, 0.0],
            &[2.0, 0.0, 0.0],
            &[f32::NAN, 0.0, 0.0],
            &[f32::INFINITY, 0.0, 0.0],
        ] {
            assert_eq!(
                tracker.assign(SpeakerEvidence::SingleSpeaker(invalid)),
                Err(TrackerError::InvalidEmbedding),
            );
            assert_eq!(tracker, before);
        }
    }

    #[test]
    fn threshold_equality_is_unknown_and_does_not_modify_state() {
        let mut cfg = config(2);
        cfg.new_speaker_threshold = 0.0;
        let mut tracker = SpeakerTracker::new(cfg).unwrap();
        assert_eq!(clean(&mut tracker, &A), SpeakerAssignment::Known(0));
        let before = tracker.clone();
        assert_eq!(clean(&mut tracker, &B), SpeakerAssignment::Unknown);
        assert_eq!(tracker, before);

        let mut cfg = config(2);
        cfg.new_speaker_threshold = -0.5;
        cfg.match_threshold = 0.0;
        let mut tracker = SpeakerTracker::new(cfg).unwrap();
        assert_eq!(clean(&mut tracker, &A), SpeakerAssignment::Known(0));
        let before = tracker.clone();
        assert_eq!(clean(&mut tracker, &B), SpeakerAssignment::Unknown);
        assert_eq!(tracker, before);
    }

    #[test]
    fn rejects_unbounded_or_invalid_configuration() {
        let mut cases = Vec::new();
        let mut cfg = config(2);
        cfg.max_speakers = 0;
        cases.push(cfg);
        cfg.max_speakers = MAX_SPEAKERS + 1;
        cases.push(cfg);
        cfg = config(2);
        cfg.embedding_dim = 0;
        cases.push(cfg);
        cfg.embedding_dim = MAX_EMBEDDING_DIM + 1;
        cases.push(cfg);
        cfg = config(2);
        cfg.match_threshold = cfg.new_speaker_threshold;
        cases.push(cfg);
        cfg = config(2);
        cfg.match_threshold = f32::NAN;
        cases.push(cfg);
        cfg = config(2);
        cfg.new_speaker_threshold = f32::INFINITY;
        cases.push(cfg);
        cfg = config(2);
        cfg.ambiguity_margin = 0.0;
        cases.push(cfg);
        cfg.ambiguity_margin = f32::NAN;
        cases.push(cfg);
        for cfg in cases {
            assert!(matches!(SpeakerTracker::new(cfg), Err(TrackerError::InvalidConfiguration)));
        }
    }
}
