//! Application state, the actions that change it and the handling of worker
//! events.

mod dispatch;
mod inbound;
mod state;

pub(crate) use dispatch::*;
pub(crate) use state::App;

/// The tabs of the interface, in tab-bar order.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Page {
    Processing,
    Llm,
    Settings,
    Log,
}

impl Page {
    pub(crate) const ALL: [Page; 4] = [Page::Processing, Page::Llm, Page::Settings, Page::Log];

    pub(crate) fn index(self) -> usize {
        Page::ALL.iter().position(|page| *page == self).unwrap_or(0)
    }

    pub(crate) fn next(self) -> Page {
        Page::ALL[(self.index() + 1) % Page::ALL.len()]
    }

    pub(crate) fn previous(self) -> Page {
        Page::ALL[(self.index() + Page::ALL.len() - 1) % Page::ALL.len()]
    }
}

/// Which part of the Processing page the arrow keys and Enter act on.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub(crate) enum Focus {
    #[default]
    Input,
    Queue,
    Params,
}

pub(crate) use crate::queue::FileState;

#[cfg(test)]
mod tests;
