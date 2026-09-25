//! Очередь: идентичность файлов, порядок, состояния и отмена удаления.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum FileState {
    Pending,
    Processing,
    Done,
    Failed,
    Cancelled,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RunSelection {
    Pending,
    Failed,
    Selected,
}

#[derive(Clone, Debug)]
pub(crate) struct QueueItem {
    pub(crate) id: u64,
    pub(crate) path: String,
    pub(crate) state: FileState,
    pub(crate) error: Option<String>,
    pub(crate) results: Vec<String>,
}

#[derive(Default)]
pub(crate) struct QueueState {
    pub(crate) items: Vec<QueueItem>,
    pub(crate) selected: Option<u64>,
    next_id: u64,
    removed: Option<(usize, QueueItem)>,
}

impl QueueState {
    pub(crate) fn add(&mut self, path: String) -> bool {
        if let Some(item) = self.items.iter().find(|item| item.path == path) {
            self.selected = Some(item.id);
            return false;
        }
        self.next_id += 1;
        let id = self.next_id;
        self.items.push(QueueItem {
            id,
            path,
            state: FileState::Pending,
            error: None,
            results: Vec::new(),
        });
        self.selected = Some(id);
        true
    }

    pub(crate) fn selected_index(&self) -> Option<usize> {
        self.items
            .iter()
            .position(|item| Some(item.id) == self.selected)
    }

    pub(crate) fn select(&mut self, index: usize) {
        if let Some(item) = self.items.get(index) {
            self.selected = Some(item.id);
        }
    }

    pub(crate) fn move_selected(&mut self, delta: i32) {
        if let Some(index) = self.selected_index() {
            let target = index
                .saturating_add_signed(delta as isize)
                .min(self.items.len() - 1);
            let item = self.items.remove(index);
            self.items.insert(target, item);
        }
    }

    pub(crate) fn remove_selected(&mut self) -> Option<String> {
        let index = self.selected_index()?;
        let item = self.items.remove(index);
        let path = item.path.clone();
        self.removed = Some((index, item));
        self.selected = self
            .items
            .get(index)
            .or_else(|| self.items.last())
            .map(|item| item.id);
        Some(path)
    }

    pub(crate) fn undo_remove(&mut self) -> bool {
        let Some((index, item)) = self.removed.take() else {
            return false;
        };
        if self.items.iter().any(|current| current.path == item.path) {
            return false;
        }
        self.selected = Some(item.id);
        self.items.insert(index.min(self.items.len()), item);
        true
    }

    pub(crate) fn clear(&mut self) {
        self.items.clear();
        self.selected = None;
        self.removed = None;
    }

    pub(crate) fn paths(&self, selection: RunSelection) -> Vec<String> {
        self.items
            .iter()
            .filter(|item| match selection {
                RunSelection::Pending => item.state == FileState::Pending,
                RunSelection::Failed => item.state == FileState::Failed,
                RunSelection::Selected => Some(item.id) == self.selected,
            })
            .map(|item| item.path.clone())
            .collect()
    }

    pub(crate) fn find_mut(&mut self, path: &str) -> Option<&mut QueueItem> {
        self.items.iter_mut().find(|item| item.path == path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duplicate_preserves_state_and_selects_original() {
        let mut queue = QueueState::default();
        assert!(queue.add("/a.wav".into()));
        let id = queue.selected;
        queue.find_mut("/a.wav").unwrap().state = FileState::Done;
        assert!(queue.add("/b.wav".into()));
        assert!(!queue.add("/a.wav".into()));
        assert_eq!(queue.selected, id);
        assert_eq!(queue.items.len(), 2);
        assert_eq!(queue.paths(RunSelection::Pending), vec!["/b.wav"]);
        assert_eq!(queue.paths(RunSelection::Selected), vec!["/a.wav"]);
    }

    #[test]
    fn undo_restores_position_but_never_duplicates_a_readded_path() {
        let mut queue = QueueState::default();
        queue.add("/a.wav".into());
        queue.add("/b.wav".into());
        queue.select(0);
        queue.find_mut("/a.wav").unwrap().error = Some("read failed".into());
        queue.remove_selected();
        assert!(queue.undo_remove());
        assert_eq!(queue.selected_index(), Some(0));
        assert_eq!(queue.items[0].error.as_deref(), Some("read failed"));
        queue.remove_selected();
        queue.add("/a.wav".into());
        assert!(!queue.undo_remove());
        assert_eq!(queue.items.len(), 2);
    }

    #[test]
    fn moving_keeps_selected_identity_and_deleting_selects_a_neighbour() {
        let mut queue = QueueState::default();
        for path in ["/a.wav", "/b.wav", "/c.wav"] {
            queue.add(path.into());
        }
        queue.select(1);
        let selected = queue.selected;
        queue.move_selected(-1);
        assert_eq!(queue.selected, selected);
        assert_eq!(queue.selected_index(), Some(0));
        queue.move_selected(-1);
        assert_eq!(queue.selected_index(), Some(0));
        assert_eq!(queue.remove_selected().as_deref(), Some("/b.wav"));
        assert_eq!(queue.paths(RunSelection::Selected), vec!["/a.wav"]);
        queue.select(1);
        queue.remove_selected();
        assert_eq!(queue.selected_index(), Some(0));
        queue.remove_selected();
        assert_eq!(queue.selected, None);
        assert_eq!(queue.remove_selected(), None);
    }

    #[test]
    fn failed_selection_does_not_include_done_or_cancelled_files() {
        let mut queue = QueueState::default();
        for (path, state) in [
            ("/done.wav", FileState::Done),
            ("/failed.wav", FileState::Failed),
            ("/cancelled.wav", FileState::Cancelled),
            ("/active.wav", FileState::Processing),
        ] {
            queue.add(path.into());
            queue.find_mut(path).unwrap().state = state;
        }
        assert_eq!(queue.paths(RunSelection::Failed), vec!["/failed.wav"]);
        assert!(queue.paths(RunSelection::Pending).is_empty());
    }

    #[test]
    fn clear_discards_undo_and_never_reuses_an_item_id() {
        let mut queue = QueueState::default();
        queue.add("/a.wav".into());
        let old = queue.selected;
        queue.remove_selected();
        queue.clear();
        assert!(!queue.undo_remove());
        queue.add("/a.wav".into());
        assert_ne!(queue.selected, old);
        queue.select(200);
        assert_eq!(queue.selected_index(), Some(0));
    }
}
