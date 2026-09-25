use crate::{app::App, lifecycle::ConnectionState};

/// Unit scenarios after handshake. Connection tests use App::default explicitly.
pub(crate) fn ready_app() -> App {
    let mut app = App::default();
    app.connection.state = ConnectionState::Ready;
    app.worker_stopped = false;
    app
}
