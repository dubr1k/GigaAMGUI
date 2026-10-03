//! The interaction model: what a key or a click asks for, and where the last
//! frame put each clickable area. State changes (`app::dispatch`) and key
//! handling depend on this module, not on the renderer.

use ratatui::layout::Rect;

use crate::app::Page;

/// Everything the user can do with a click or a key. Keys and mouse clicks both go
/// through `app::dispatch`, so a click can never drift from its keyboard twin.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Action {
    ShowResults(bool),
    SelectResult(usize),
    OpenResult(bool),
    Reconnect,
    ForceStop,
    ConfirmStop(bool),
    Tab(Page),
    SelectFile(usize),
    RemoveFile(usize),
    Run(crate::queue::RunSelection),
    ConfirmRerun(bool),
    UndoRemove,
    AddFiles,
    RetryInputs,
    QueueActions,
    ShowPath(bool),
    OpenMenu(&'static str),
    MenuItem(usize),
    Suggestion(usize),
    ToggleMode(&'static str),
    Button(ButtonId),
    ToggleLang,
    Help,
    Scroll(AreaId, i32),
    FocusInput,
    /// Highlights a row of the Settings page and performs its action.
    SettingsRow(usize),
    /// Flips a boolean setting: `mouse`, `pets`, `subtitle_split` or `llm_tools`.
    ToggleSetting(&'static str),
    /// Highlights a row of the LLM «Транскрипты» table.
    LlmInput(usize),
    /// Drops a `/llm-file` transcript from that table (session results stay).
    RemoveLlmInput(usize),
    /// Pre-fills the command line with `command ` so the value can be typed.
    EditCommand(&'static str),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum ButtonId {
    Start,
    Stop,
    RunLlm,
    CancelLlm,
    ClearQueue,
    ClearLog,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) enum AreaId {
    Results,
    ResultPath,
    Queue,
    LlmOutput,
    Log,
    Settings,
    Help,
    Path,
}

/// The interactive areas of the last drawn frame. `draw` clears it and registers
/// every clickable rect again, so the map always matches what is on screen.
#[derive(Default)]
pub(crate) struct HitMap {
    items: Vec<(Rect, Action)>,
}

impl HitMap {
    pub(crate) fn clear(&mut self) {
        self.items.clear();
    }

    pub(crate) fn add(&mut self, rect: Rect, action: Action) {
        self.items.push((rect, action));
    }

    /// Every registered area, for tests that check what a frame made clickable.
    #[cfg(test)]
    pub(crate) fn items(&self) -> &[(Rect, Action)] {
        &self.items
    }

    /// The action under the cursor; the last registered rect wins because it was
    /// drawn on top of the earlier ones.
    pub(crate) fn hit(&self, x: u16, y: u16) -> Option<Action> {
        self.items
            .iter()
            .rev()
            .find(|(rect, _)| rect.contains((x, y).into()))
            .map(|(_, action)| action.clone())
    }

    /// The scrollable area under the cursor: areas register as `Scroll(area, 0)`.
    pub(crate) fn hit_scroll(&self, x: u16, y: u16) -> Option<AreaId> {
        self.items
            .iter()
            .rev()
            .filter(|(rect, _)| rect.contains((x, y).into()))
            .find_map(|(_, action)| match action {
                Action::Scroll(area, _) => Some(*area),
                _ => None,
            })
    }
}
