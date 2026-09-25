//! Прогресс и итог одного неизменяемого снимка очереди.
use crate::{
    app::App,
    i18n::{t, tf},
    lifecycle::{Activity, JobKind},
    queue::FileState,
};
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet},
    time::{Duration, Instant},
};

pub(crate) struct BatchRun {
    pub files: Vec<String>,
    pub started_at: Instant,
    pub started: HashSet<String>,
    pub finished: HashSet<String>,
    pub failures: HashMap<String, String>,
    pub error: Option<String>,
}

pub(crate) struct BatchSummary {
    pub succeeded: usize,
    pub failed: usize,
    pub unstarted: usize,
    pub interrupted: usize,
    pub elapsed: Duration,
    pub cancelled: bool,
    pub error: Option<String>,
    pub progress: f64,
}

impl BatchRun {
    pub fn new(files: Vec<String>) -> Self {
        Self {
            files,
            started_at: Instant::now(),
            started: HashSet::new(),
            finished: HashSet::new(),
            failures: HashMap::new(),
            error: None,
        }
    }

    pub fn overall_progress(&self, file_fraction: f64) -> f64 {
        if self.files.is_empty() {
            return 0.0;
        }
        let fraction = if file_fraction.is_finite() {
            file_fraction.clamp(0.0, 1.0)
        } else {
            0.0
        };
        ((self.finished.len() as f64 + fraction) / self.files.len() as f64).clamp(0.0, 1.0)
    }
}

impl App {
    pub(crate) fn overall_progress(&self) -> f64 {
        self.batch.as_ref().map_or_else(
            || self.batch_summary.as_ref().map_or(0.0, |s| s.progress),
            |batch| {
                batch.overall_progress(if self.current_file.is_some() {
                    self.progress
                } else {
                    0.0
                })
            },
        )
    }

    pub(crate) fn finish_batch(
        &mut self,
        cancelled: bool,
        error: Option<String>,
        elapsed: Option<f64>,
    ) {
        let progress = self.overall_progress();
        if let Some(batch) = self.batch.take() {
            self.batch_summary = Some(BatchSummary {
                succeeded: batch.finished.len().saturating_sub(batch.failures.len()),
                failed: batch.failures.len(),
                unstarted: batch.files.len().saturating_sub(batch.started.len()),
                interrupted: batch.started.len().saturating_sub(batch.finished.len()),
                elapsed: elapsed
                    .and_then(|s| Duration::try_from_secs_f64(s).ok())
                    .unwrap_or_else(|| batch.started_at.elapsed()),
                cancelled,
                error: error.or(batch.error).or_else(|| {
                    batch
                        .files
                        .iter()
                        .find_map(|p| batch.failures.get(p).cloned())
                }),
                progress,
            });
        }
    }

    pub(crate) fn handle_batch_message(&mut self, kind: &str, value: &Value) -> bool {
        if !matches!(
            kind,
            "started" | "file_started" | "progress" | "file_completed" | "cancelling" | "completed"
        ) {
            return false;
        }
        if !self.running() || self.llm_running() {
            return true;
        }
        match kind {
            "started" => {
                if !self.activity.acknowledge(JobKind::Asr) {
                    return true;
                }
                for item in &mut self.queue.items {
                    if self
                        .batch
                        .as_ref()
                        .is_none_or(|b| b.files.contains(&item.path))
                    {
                        item.state = FileState::Pending;
                        item.error = None;
                    }
                }
                self.total_files = self
                    .batch
                    .as_ref()
                    .map_or(value["total_files"].as_u64().unwrap_or(0) as usize, |b| {
                        b.files.len()
                    });
                self.status = tf(
                    self.lang,
                    "status.running_backend",
                    &[("backend", value["backend"].as_str().unwrap_or("auto"))],
                );
            }
            "file_started" | "file_completed" => {
                let Some(file) = value["file"].as_str() else {
                    return true;
                };
                if let Some(batch) = &mut self.batch {
                    if !batch.files.iter().any(|p| p == file) || batch.finished.contains(file) {
                        return true;
                    }
                    batch.started.insert(file.into());
                }
                if kind == "file_started" {
                    self.current_file = Some(file.into());
                    self.file_index = value["file_index"].as_u64().unwrap_or(0) as usize;
                    self.progress = 0.0;
                    self.stage = "preparing".into();
                    self.processed_seconds = None;
                    self.total_seconds = None;
                    if let Some(item) = self.queue.find_mut(file) {
                        item.state = FileState::Processing;
                    }
                    self.status = t(self.lang, "status.running").into();
                } else {
                    let success = value["result"]["success"].as_bool().unwrap_or(false);
                    let error = (!success).then(|| {
                        value["result"]["error"]
                            .as_str()
                            .unwrap_or(t(self.lang, "status.unknown_error"))
                            .to_owned()
                    });
                    let saved: Vec<String> = value["result"]["saved_files"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(|p| p.as_str().map(str::to_owned))
                        .collect();
                    if let Some(batch) = &mut self.batch {
                        batch.finished.insert(file.into());
                        if let Some(error) = &error {
                            batch.failures.insert(file.into(), error.clone());
                        }
                    }
                    if let Some(item) = self.queue.find_mut(file) {
                        item.state = if success {
                            FileState::Done
                        } else {
                            FileState::Failed
                        };
                        item.error = error.clone();
                        item.results = saved.clone();
                    }
                    for path in saved {
                        if !self.result_files.contains(&path) {
                            self.result_files.push(path);
                        }
                    }
                    self.current_file = None;
                    self.progress = 0.0;
                    self.log(format!(
                        "{} {}{}",
                        if success { "✓" } else { "×" },
                        crate::commands::short_name(file),
                        error.map_or(String::new(), |e| format!(": {e}"))
                    ));
                }
            }
            "progress" => {
                if self.current_file.is_none()
                    || value["file"]
                        .as_str()
                        .is_some_and(|p| Some(p) != self.current_file.as_deref())
                {
                    return true;
                }
                self.stage = value["stage"].as_str().unwrap_or("preparing").into();
                self.progress = value["file_progress"]
                    .as_f64()
                    .unwrap_or(0.0)
                    .clamp(0.0, 1.0);
                self.processed_seconds = value["processed_seconds"].as_f64();
                self.total_seconds = value["total_seconds"].as_f64();
                if let Some(message) = value["message"].as_str() {
                    self.status = message.into();
                }
            }
            "cancelling" => {
                self.activity.stop();
                self.cancelled = true;
                self.status = t(self.lang, "status.cancelling").into();
            }
            "completed" => {
                self.cancelled = value["cancelled"].as_bool().unwrap_or(false);
                self.finish_batch(
                    self.cancelled,
                    value["message"].as_str().map(str::to_owned),
                    value["elapsed_seconds"].as_f64(),
                );
                self.activity = Activity::Idle;
                for item in &mut self.queue.items {
                    if item.state == FileState::Processing {
                        item.state = FileState::Cancelled;
                    }
                }
                let key = if self.cancelled {
                    "status.cancelled"
                } else if value["success"].as_bool().unwrap_or(false) {
                    "status.completed"
                } else {
                    "status.completed_with_errors"
                };
                self.status = t(self.lang, key).into();
                if let Some(error) = self.batch_summary.as_ref().and_then(|s| s.error.as_ref()) {
                    self.status.push_str(&format!(": {error}"));
                }
                self.log(self.status.clone());
            }
            _ => unreachable!(),
        }
        true
    }
}
