//! Чистые переходы соединения и активности; запуск процесса не равен готовности.

use std::time::{Duration, Instant};

use serde_json::Value;

pub(crate) const PROTOCOL_VERSION: u64 = 1;
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(15);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ConnectionState {
    Connecting,
    Ready,
    Unavailable,
}

pub(crate) struct Connection {
    pub state: ConnectionState,
    pub generation: u64,
    started_at: Instant,
}

impl Connection {
    pub(crate) fn new(generation: u64, now: Instant) -> Self {
        Self {
            state: ConnectionState::Connecting,
            generation,
            started_at: now,
        }
    }

    pub(crate) fn ready(&mut self, value: &Value) -> Result<(), String> {
        let capabilities = value["capabilities"].as_array();
        let compatible = value["type"] == "ready"
            && value["protocol_version"].as_u64() == Some(PROTOCOL_VERSION)
            && capabilities.is_some_and(|items| {
                ["resolve_inputs", "asr", "llm"]
                    .iter()
                    .all(|name| items.iter().any(|item| item.as_str() == Some(name)))
            });
        if !compatible {
            self.state = ConnectionState::Unavailable;
            return Err("Incompatible worker protocol or capabilities".into());
        }
        if self.state != ConnectionState::Connecting {
            return Err("Unexpected worker readiness response".into());
        }
        self.state = ConnectionState::Ready;
        Ok(())
    }

    pub(crate) fn timed_out(&self, now: Instant) -> bool {
        self.state == ConnectionState::Connecting
            && now.saturating_duration_since(self.started_at) >= HANDSHAKE_TIMEOUT
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum JobKind {
    Asr,
    Llm,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum Activity {
    #[default]
    Idle,
    Starting(JobKind),
    CancellingStart(JobKind),
    Running(JobKind),
    Stopping(JobKind),
}

impl Activity {
    pub(crate) fn is_active(self) -> bool {
        self != Self::Idle
    }

    pub(crate) fn is_llm(self) -> bool {
        self.kind() == Some(JobKind::Llm)
    }

    pub(crate) fn is_starting(self) -> bool {
        matches!(self, Self::Starting(_) | Self::CancellingStart(_))
    }

    pub(crate) fn is_stopping(self) -> bool {
        matches!(self, Self::CancellingStart(_) | Self::Stopping(_))
    }

    fn kind(self) -> Option<JobKind> {
        match self {
            Self::Idle => None,
            Self::Starting(kind)
            | Self::CancellingStart(kind)
            | Self::Running(kind)
            | Self::Stopping(kind) => Some(kind),
        }
    }

    pub(crate) fn start(&mut self, kind: JobKind) -> bool {
        if self.is_active() {
            return false;
        }
        *self = Self::Starting(kind);
        true
    }

    pub(crate) fn acknowledge(&mut self, kind: JobKind) -> bool {
        match *self {
            Self::Starting(current) if current == kind => {
                *self = Self::Running(kind);
                true
            }
            Self::CancellingStart(current) if current == kind => {
                *self = Self::Stopping(kind);
                true
            }
            Self::Stopping(current) if current == kind => true,
            _ => false,
        }
    }

    pub(crate) fn stop(&mut self) -> bool {
        match *self {
            Self::Starting(kind) => {
                // До подтверждения запуска worker может ответить только ошибкой.
                *self = Self::CancellingStart(kind);
                true
            }
            Self::Running(kind) => {
                *self = Self::Stopping(kind);
                true
            }
            _ => false,
        }
    }

    pub(crate) fn finish(&mut self, kind: JobKind) -> bool {
        if self.kind() != Some(kind) {
            return false;
        }
        *self = Self::Idle;
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::time::{Duration, Instant};

    #[test]
    fn starting_locks_duplicate_jobs_and_ack_keeps_requested_stop() {
        let mut activity = Activity::Idle;
        assert!(activity.start(JobKind::Llm));
        assert!(activity.is_active() && activity.is_llm());
        assert!(!activity.start(JobKind::Asr));
        assert!(!activity.start(JobKind::Llm));
        assert!(activity.stop());
        assert!(!activity.stop());
        assert!(activity.acknowledge(JobKind::Llm));
        assert_eq!(activity, Activity::Stopping(JobKind::Llm));
        assert!(!activity.finish(JobKind::Asr));
        assert!(activity.finish(JobKind::Llm));
        assert_eq!(activity, Activity::Idle);
    }

    #[test]
    fn late_wrong_kind_ack_does_not_start_or_replace_work() {
        let mut activity = Activity::Idle;
        assert!(!activity.acknowledge(JobKind::Asr));
        activity.start(JobKind::Asr);
        assert!(!activity.acknowledge(JobKind::Llm));
        assert_eq!(activity, Activity::Starting(JobKind::Asr));
        assert!(activity.acknowledge(JobKind::Asr));
        assert_eq!(activity, Activity::Running(JobKind::Asr));
    }

    #[test]
    fn readiness_requires_compatible_version_and_capabilities() {
        for value in [
            json!({"type":"ready", "protocol_version":2, "capabilities":["resolve_inputs","asr","llm"]}),
            json!({"type":"ready", "protocol_version":1, "capabilities":["asr","llm"]}),
            json!({"type":"ready", "protocol_version":1, "capabilities":null}),
            json!({"type":"pong"}),
        ] {
            let mut connection = Connection::new(4, Instant::now());
            assert!(connection.ready(&value).is_err());
            assert_eq!(connection.state, ConnectionState::Unavailable);
        }
        let mut connection = Connection::new(5, Instant::now());
        connection
            .ready(&json!({"type":"ready", "protocol_version":1,
            "capabilities":["resolve_inputs","asr","llm","future"]}))
            .unwrap();
        assert_eq!(connection.state, ConnectionState::Ready);
        assert_eq!(connection.generation, 5);
    }

    #[test]
    fn handshake_deadline_does_not_apply_to_ready_worker() {
        let now = Instant::now();
        let mut connection = Connection::new(8, now);
        assert!(!connection.timed_out(now + Duration::from_millis(14_999)));
        assert!(connection.timed_out(now + Duration::from_secs(15)));
        connection
            .ready(&json!({"type":"ready", "protocol_version":1,
            "capabilities":["resolve_inputs","asr","llm"]}))
            .unwrap();
        assert!(!connection.timed_out(now + Duration::from_secs(600)));
    }
}
