//! The «Обработка» page: the file queue, the parameter panel and the progress block.

use ratatui::{
    layout::{Constraint, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Cell, Clear, Paragraph, Row, Table, TableState, Wrap},
};

use crate::{
    app::{App, FileState, Focus},
    commands::short_name,
    i18n::{t, tf},
    ui::{Action, AreaId, ButtonId},
};

/// The rows of the parameter panel, top to bottom: the label key and the command
/// whose menu (or pre-filled command line) the row opens.
pub(crate) const PARAM_ROWS: [(&str, &str); 7] = [
    ("params.backend", "/backend"),
    ("params.model", "/model"),
    ("params.formats", "/formats"),
    ("params.diarize", "/diarize"),
    ("params.speakers", "/speakers"),
    ("params.audio", "/audio-mode"),
    ("params.output", "/output"),
];

pub(crate) fn draw(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let [top, bottom] = Layout::vertical([Constraint::Min(6), Constraint::Length(5)]).areas(area);
    let [queue_area, params_area] =
        Layout::horizontal([Constraint::Percentage(60), Constraint::Percentage(40)]).areas(top);
    draw_queue(frame, queue_area, app);
    draw_params(frame, params_area, app);
    super::progress::draw(frame, bottom, app);
}

pub(super) fn timecode(seconds: f64) -> String {
    format!(
        "{:02}:{:02}:{:02}",
        (seconds / 3600.0) as u64,
        ((seconds / 60.0) as u64) % 60,
        seconds as u64 % 60
    )
}

/// Keeps the extension visible: `a-very-long-recording.wav` → `a-very…ing.wav`.
pub(crate) fn fit_middle(text: &str, width: usize) -> String {
    if Line::from(text).width() <= width || width < 5 {
        return text.to_owned();
    }
    let mut tail = Vec::new();
    let mut tail_width = 0;
    for ch in text.chars().rev() {
        let size = Line::from(ch.to_string()).width();
        if tail_width + size > (width - 1) / 2 {
            break;
        }
        tail.push(ch);
        tail_width += size;
    }
    let mut out = String::new();
    let mut head_width = 0;
    for ch in text.chars() {
        let size = Line::from(ch.to_string()).width();
        if head_width + size > width - 1 - tail_width {
            break;
        }
        out.push(ch);
        head_width += size;
    }
    out.push('…');
    out.extend(tail.into_iter().rev());
    out
}

fn state_of(app: &App, _index: usize, file: &str) -> FileState {
    if app.running() && !app.llm_running() && app.current_file.as_deref() == Some(file) {
        FileState::Processing
    } else {
        app.file_state(file)
    }
}

fn draw_queue(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let p = *app.palette();
    let title = if app.queue.items.is_empty() {
        t(app.lang, "queue.title").to_owned()
    } else {
        tf(
            app.lang,
            "queue.count",
            &[("n", &app.queue.items.len().to_string())],
        )
    };
    let block = Block::bordered()
        .title(Span::styled(format!(" {title} "), p.title()))
        .title_bottom(Line::styled(
            if app.running() {
                String::new()
            } else {
                format!(
                    " Delete · [{}] [{}] ",
                    t(app.lang, "queue.actions"),
                    t(app.lang, "queue.undo")
                )
            },
            Style::default().fg(p.muted),
        ))
        .title_top(
            Line::from(vec![
                Span::styled(
                    format!("[{}] ", t(app.lang, "queue.add")),
                    Style::default().fg(if app.running() { p.disabled } else { p.accent }),
                ),
                Span::styled(
                    format!("[{}]", t(app.lang, "btn.clear")),
                    Style::default().fg(
                        if app.running()
                            || (app.queue.items.is_empty() && app.pending_inputs.is_empty())
                        {
                            p.disabled
                        } else {
                            p.muted
                        },
                    ),
                ),
            ])
            .right_aligned(),
        )
        .border_style(p.border_focus(app.focus == Focus::Queue));
    let mut inner = block.inner(area);
    frame.render_widget(block, area);
    app.hits.add(area, Action::Scroll(AreaId::Queue, 0));
    let clear_width = t(app.lang, "btn.clear").chars().count() as u16 + 2;
    let clear_x = area.right().saturating_sub(1 + clear_width);
    app.hits.add(
        Rect::new(clear_x, area.y, clear_width, 1),
        Action::Button(ButtonId::ClearQueue),
    );
    let add_width = Line::from(t(app.lang, "queue.add")).width() as u16 + 2;
    if !app.running() {
        app.hits.add(
            Rect::new(clear_x.saturating_sub(1 + add_width), area.y, add_width, 1),
            Action::AddFiles,
        );
    }
    if !app.running() {
        let actions_width = Line::from(t(app.lang, "queue.actions")).width() as u16 + 2;
        let undo_width = Line::from(t(app.lang, "queue.undo")).width() as u16 + 2;
        let x = area.x + 11;
        app.hits.add(
            Rect::new(x, area.bottom() - 1, actions_width, 1),
            Action::QueueActions,
        );
        app.hits.add(
            Rect::new(x + actions_width + 1, area.bottom() - 1, undo_width, 1),
            Action::UndoRemove,
        );
    }
    if let Some(item) = app
        .queue
        .selected_index()
        .and_then(|index| app.queue.items.get(index))
    {
        if inner.height >= 7 {
            let path_area = Rect::new(inner.x, inner.bottom() - 3, inner.width, 3);
            inner.height -= 3;
            frame.render_widget(
                Paragraph::new(item.path.as_str())
                    .wrap(Wrap { trim: false })
                    .block(
                        Block::default()
                            .borders(ratatui::widgets::Borders::TOP)
                            .title(format!(" [{}] ", t(app.lang, "queue.full_path")))
                            .border_style(Style::default().fg(p.muted)),
                    ),
                path_area,
            );
            app.hits.add(path_area, Action::ShowPath(true));
        }
    }
    if app.queue.items.is_empty() {
        let height = inner.height.min(3);
        frame.render_widget(
            Paragraph::new(Line::styled(
                t(app.lang, "queue.empty"),
                Style::default().fg(p.muted),
            ))
            .centered()
            .wrap(Wrap { trim: true }),
            Rect::new(
                inner.x,
                inner.y + (inner.height.saturating_sub(height)) / 2,
                inner.width,
                height,
            ),
        );
        return;
    }
    let name_width = usize::from(inner.width.saturating_sub(4 + 14 + 3 + 3));
    let rows: Vec<Row> = app
        .queue
        .items
        .iter()
        .enumerate()
        .map(|(index, item)| {
            let file = &item.path;
            let state = state_of(app, index, file);
            let (key, colour) = match state {
                FileState::Pending => ("state.pending", p.muted),
                FileState::Processing => ("state.processing", p.accent),
                FileState::Done => ("state.done", p.success),
                FileState::Failed => ("state.failed", p.error),
                FileState::Cancelled => ("state.cancelled", p.warning),
            };
            Row::new(vec![
                Cell::from(format!("{:>2}.", index + 1)).style(Style::default().fg(p.disabled)),
                Cell::from(vec![
                    Line::styled(
                        fit_middle(&short_name(file), name_width),
                        Style::default().fg(p.text).add_modifier(Modifier::BOLD),
                    ),
                    Line::styled(
                        fit_middle(
                            file.rsplit_once('/').map_or("", |(parent, _)| parent),
                            name_width,
                        ),
                        Style::default().fg(p.muted),
                    ),
                ]),
                Cell::from(t(app.lang, key)).style(Style::default().fg(colour)),
                Cell::from("[×]").style(Style::default().fg(if app.running() {
                    p.disabled
                } else {
                    p.error
                })),
            ])
            .height(2)
        })
        .collect();
    let visible = usize::from(inner.height.saturating_sub(1) / 2);
    let max_offset = app.queue.items.len().saturating_sub(visible);
    let offset = usize::from(*app.scroll.entry(AreaId::Queue).or_default()).min(max_offset);
    let selected = app.queue.selected_index();
    let mut state = TableState::default()
        .with_offset(offset)
        .with_selected(selected);
    let highlight = if app.focus == Focus::Queue {
        p.emphasis()
    } else {
        Style::default().fg(p.accent).add_modifier(Modifier::BOLD)
    };
    let table = Table::new(
        rows,
        [
            Constraint::Length(4),
            Constraint::Min(8),
            Constraint::Length(14),
            Constraint::Length(3),
        ],
    )
    .header(
        Row::new(vec![
            t(app.lang, "queue.col_no"),
            t(app.lang, "queue.col_file"),
            t(app.lang, "queue.col_state"),
            "",
        ])
        .style(
            Style::default()
                .fg(p.muted)
                .add_modifier(Modifier::UNDERLINED),
        ),
    )
    .row_highlight_style(highlight);
    frame.render_stateful_widget(table, inner, &mut state);
    // The table may have moved the window to keep the selection visible; the
    // wheel continues from where the frame actually is.
    let offset = state.offset();
    app.scroll.insert(AreaId::Queue, offset as u16);
    for row in 0..visible.min(app.queue.items.len().saturating_sub(offset)) {
        app.hits.add(
            Rect::new(inner.x, inner.y + 1 + row as u16 * 2, inner.width, 2),
            Action::SelectFile(offset + row),
        );
        if !app.running() && inner.width >= 3 {
            app.hits.add(
                Rect::new(inner.right() - 3, inner.y + 1 + row as u16 * 2, 3, 1),
                Action::RemoveFile(offset + row),
            );
        }
    }
}

fn param_value(app: &App, command: &str) -> String {
    match command {
        "/backend" => app.backend.clone(),
        "/model" => app.model.clone(),
        "/formats" => app.formats.join(", "),
        "/diarize" => {
            if app.diarization {
                format!("{} · {}", t(app.lang, "value.on"), app.diarization_backend)
            } else {
                t(app.lang, "value.off").to_owned()
            }
        }
        "/speakers" => app
            .num_speakers
            .map_or_else(|| t(app.lang, "value.auto").to_owned(), |n| n.to_string()),
        "/audio-mode" => app.audio_preprocessing_mode.clone(),
        _ => app
            .output_dir
            .clone()
            .unwrap_or_else(|| t(app.lang, "value.next_to_file").to_owned()),
    }
}

fn draw_params(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let p = *app.palette();
    let block = Block::bordered()
        .title(Span::styled(
            format!(" {} ", t(app.lang, "params.title")),
            p.title(),
        ))
        .border_style(p.border_focus(app.focus == Focus::Params));
    let inner = block.inner(area);
    frame.render_widget(block, area);
    let rows: Vec<Row> = PARAM_ROWS
        .iter()
        .map(|(key, command)| {
            Row::new(vec![
                Cell::from(t(app.lang, key)).style(Style::default().fg(p.muted)),
                Cell::from(param_value(app, command)).style(Style::default().fg(p.text)),
            ])
        })
        .collect();
    let mut state = TableState::default().with_selected(
        (app.focus == Focus::Params).then_some(app.params_cursor.min(PARAM_ROWS.len() - 1)),
    );
    let table = Table::new(rows, [Constraint::Length(12), Constraint::Min(6)])
        .row_highlight_style(p.emphasis());
    frame.render_stateful_widget(table, inner, &mut state);
    for (row, (_, command)) in PARAM_ROWS.iter().enumerate() {
        if row as u16 >= inner.height {
            break;
        }
        app.hits.add(
            Rect::new(inner.x, inner.y + row as u16, inner.width, 1),
            Action::OpenMenu(command),
        );
    }
}

pub(crate) fn draw_path_overlay(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let Some(item) = app
        .queue
        .selected_index()
        .and_then(|i| app.queue.items.get(i))
    else {
        return;
    };
    let rect = Rect::new(
        area.x + 2,
        area.y + 3,
        area.width.saturating_sub(4),
        area.height.saturating_sub(6),
    );
    let block = Block::bordered()
        .title(t(app.lang, "queue.full_path"))
        .title_bottom(t(app.lang, "queue.path_close"));
    let inner = block.inner(rect);
    let mut text = item.path.clone();
    if let Some(error) = &item.error {
        text.push_str(&format!("\n\n{error}"));
    }
    if !item.results.is_empty() {
        text.push_str(&format!("\n\n{}", item.results.join("\n")));
    }
    let paragraph = Paragraph::new(text).wrap(Wrap { trim: false }).block(block);
    let max_scroll = paragraph
        .line_count(inner.width)
        .saturating_sub(inner.height as usize) as u16;
    let offset = app.scroll.entry(AreaId::Path).or_default();
    *offset = (*offset).min(max_scroll);
    frame.render_widget(Clear, rect);
    frame.render_widget(paragraph.scroll((*offset, 0)), rect);
    app.hits.clear();
    app.hits.add(area, Action::ShowPath(false));
    app.hits.add(rect, Action::Scroll(AreaId::Path, 0));
}

pub(crate) fn draw_confirmation(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let force = app.stop_confirmation;
    let width = area.width.saturating_sub(4).min(72);
    let height = area.height.saturating_sub(2).min(if force { 9 } else { 7 });
    let rect = Rect::new(
        area.x + (area.width - width) / 2,
        area.y + (area.height - height) / 2,
        width,
        height,
    );
    let block = Block::bordered().title(t(
        app.lang,
        if force {
            "button.force_stop"
        } else {
            "queue.run_selected"
        },
    ));
    let inner = block.inner(rect);
    frame.render_widget(Clear, rect);
    frame.render_widget(
        Paragraph::new(t(
            app.lang,
            if force {
                "confirm.force_stop"
            } else {
                "status.confirm_rerun"
            },
        ))
        .wrap(Wrap { trim: false })
        .block(block),
        rect,
    );
    app.hits.clear();
    let yes = format!("[Y · {}]", t(app.lang, "confirm.yes"));
    let no = format!("[N / Esc · {}]", t(app.lang, "confirm.no"));
    let y = inner.bottom().saturating_sub(1);
    let yes_width = Line::from(yes.as_str()).width() as u16;
    let no_width = Line::from(no.as_str()).width() as u16;
    frame.render_widget(
        Paragraph::new(format!("{yes}  {no}")),
        Rect::new(inner.x, y, inner.width, 1),
    );
    app.hits.add(
        Rect::new(inner.x, y, yes_width.min(inner.width), 1),
        if force {
            Action::ConfirmStop(true)
        } else {
            Action::ConfirmRerun(true)
        },
    );
    app.hits.add(
        Rect::new(
            inner.x + yes_width + 2,
            y,
            no_width.min(inner.width.saturating_sub(yes_width + 2)),
            1,
        ),
        if force {
            Action::ConfirmStop(false)
        } else {
            Action::ConfirmRerun(false)
        },
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::queue::RunSelection;
    use crate::{settings::isolated_config_dir, ui::draw as draw_all};

    #[test]
    fn queue_actions_and_full_path_remain_visible_at_supported_sizes() {
        use crate::i18n::Lang;
        for (width, height) in [(80, 24), (100, 30), (160, 40)] {
            for lang in [Lang::Ru, Lang::En] {
                for pet_enabled in [false, true] {
                    let mut app = crate::test_support::ready_app();
                    app.lang = lang;
                    app.pet_enabled = pet_enabled;
                    app.queue.add("/Users/test/meeting.wav".into());
                    let mut terminal =
                        ratatui::Terminal::new(ratatui::backend::TestBackend::new(width, height))
                            .unwrap();
                    terminal.draw(|f| draw_all(f, &mut app)).unwrap();
                    let text = terminal.backend().to_string();
                    for key in ["queue.add", "queue.actions", "queue.full_path"] {
                        assert!(
                            text.contains(t(lang, key)),
                            "{width}x{height}: {key}\n{text}"
                        );
                    }
                    assert!(text.contains("meeting.wav"), "{text}");
                    for (rect, _) in app.hits.items() {
                        assert!(rect.right() <= width && rect.bottom() <= height, "{rect:?}");
                    }
                    assert!(!text.contains("› "), "{text}");
                    crate::app::dispatch(&mut app, Action::AddFiles);
                    terminal.draw(|f| draw_all(f, &mut app)).unwrap();
                    assert!(terminal
                        .backend()
                        .to_string()
                        .contains(t(lang, "input.files")));
                }
            }
        }
    }

    #[test]
    fn default_start_does_not_advertise_reprocessing_completed_files() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/a.wav".into());
        app.queue.items[0].state = FileState::Done;
        let mut terminal =
            ratatui::Terminal::new(ratatui::backend::TestBackend::new(100, 30)).unwrap();
        terminal.draw(|f| draw_all(f, &mut app)).unwrap();
        assert!(!app
            .hits
            .items()
            .iter()
            .any(|(_, a)| *a == Action::Button(ButtonId::Start)));
        assert_ne!(crate::app::next_step(&app), "hint.start");
    }

    #[test]
    fn progress_distinguishes_current_file_and_whole_queue() {
        use crate::i18n::Lang;
        for (width, height) in [(80, 24), (100, 30), (160, 40)] {
            for lang in [Lang::Ru, Lang::En] {
                for pet_enabled in [false, true] {
                    let mut app = crate::test_support::ready_app();
                    app.lang = lang;
                    app.pet_enabled = pet_enabled;
                    app.queue.add("/a.wav".into());
                    app.queue.add("/b.wav".into());
                    app.begin_batch(RunSelection::Pending, false);
                    app.handle_message(serde_json::json!({"type":"started","total_files":2}));
                    app.handle_message(serde_json::json!({"type":"file_started","file":"/a.wav"}));
                    let mut terminal =
                        ratatui::Terminal::new(ratatui::backend::TestBackend::new(width, height))
                            .unwrap();
                    terminal.draw(|f| draw_all(f, &mut app)).unwrap();
                    assert!(terminal.backend().to_string().contains("1/2: —"));
                    app.handle_message(serde_json::json!({"type":"progress","file":"/a.wav","file_progress":0.5,"stage":"transcription"}));
                    terminal.draw(|f| draw_all(f, &mut app)).unwrap();
                    let text = terminal.backend().to_string();
                    assert!(
                        text.contains(&format!("{} 25%", t(lang, "progress.overall"))),
                        "{text}"
                    );
                    assert!(
                        text.contains(&format!("{} 1/2: 50%", t(lang, "progress.file"))),
                        "{text}"
                    );
                    app.handle_message(
                        serde_json::json!({"type":"completed","success":false,"cancelled":true}),
                    );
                    terminal.draw(|f| draw_all(f, &mut app)).unwrap();
                    let text = terminal.backend().to_string();
                    assert!(text.contains("00:00:00"), "{text}");
                    assert!(text.contains(t(lang, "status.cancelled")), "{text}");
                    for (rect, _) in app.hits.items() {
                        assert!(rect.right() <= width && rect.bottom() <= height, "{rect:?}");
                    }
                }
            }
        }
    }

    #[test]
    fn stop_buttons_explain_soft_stop_and_offer_confirmed_termination() {
        use crate::{
            app::{dispatch, Page},
            i18n::Lang,
            lifecycle::{Activity, JobKind},
        };
        for (width, height) in [(80, 24), (100, 30), (160, 40)] {
            for lang in [Lang::Ru, Lang::En] {
                for (page, kind, button, key) in [
                    (Page::Processing, JobKind::Asr, ButtonId::Stop, "btn.stop"),
                    (
                        Page::Llm,
                        JobKind::Llm,
                        ButtonId::CancelLlm,
                        "btn.cancel_llm",
                    ),
                ] {
                    let mut app = crate::test_support::ready_app();
                    app.page = page;
                    app.lang = lang;
                    app.activity = Activity::Running(kind);
                    let mut terminal =
                        ratatui::Terminal::new(ratatui::backend::TestBackend::new(width, height))
                            .unwrap();
                    terminal.draw(|f| draw_all(f, &mut app)).unwrap();
                    let text = terminal.backend().to_string();
                    let label = match (kind, lang) {
                        (JobKind::Asr, Lang::Ru) => "После файла",
                        (JobKind::Asr, Lang::En) => "After file",
                        (JobKind::Llm, Lang::Ru) => "Отменить запрос",
                        (JobKind::Llm, Lang::En) => "Cancel request",
                    };
                    assert_eq!(t(lang, key), label);
                    assert!(text.contains(label), "{width}×{height}\n{text}");
                    assert_eq!(dispatch(&mut app, Action::Button(button)).len(), 1);
                    terminal.draw(|f| draw_all(f, &mut app)).unwrap();
                    let text = terminal.backend().to_string();
                    assert!(text.contains(t(lang, "btn.force_stop")), "{text}");
                    let rect = app
                        .hits
                        .items()
                        .iter()
                        .find(|(_, action)| *action == Action::ForceStop)
                        .unwrap()
                        .0;
                    assert_eq!(app.hits.hit(rect.x, rect.y), Some(Action::ForceStop));
                    dispatch(&mut app, Action::ForceStop);
                    assert!(app.stop_confirmation);
                    dispatch(&mut app, Action::ConfirmStop(false));
                    assert_eq!(app.activity, Activity::Stopping(kind));
                    assert!(!app.worker_stop_requested);
                }
            }
        }
    }

    #[test]
    fn progress_bar_is_visible_without_colours() {
        // ratatui's Gauge paints the filled part as `█` in the gauge fg, so a theme
        // with every colour Reset still shows the bar in the terminal foreground.
        let mut app = crate::test_support::ready_app();
        app.progress = 0.5;
        app.batch = Some(crate::batch::BatchRun::new(vec!["/a.wav".into()]));
        app.current_file = Some("/a.wav".into());
        app.activity = crate::lifecycle::Activity::Running(crate::lifecycle::JobKind::Asr);
        let render = |app: &mut App| {
            let backend = ratatui::backend::TestBackend::new(100, 40);
            let mut terminal = ratatui::Terminal::new(backend).unwrap();
            terminal.draw(|f| draw_all(f, app)).unwrap();
            terminal.backend().buffer().clone()
        };
        let filled = |buffer: &ratatui::buffer::Buffer| {
            buffer
                .content()
                .iter()
                .filter(|cell| cell.symbol() == "█")
                .map(|cell| cell.fg)
                .collect::<Vec<_>>()
        };
        let default = filled(&render(&mut app));
        assert!(default.len() >= 8, "{} filled cells", default.len());
        assert!(default.iter().all(|fg| *fg == app.palette().accent));
        app.theme = crate::theme::Theme::by_name("mono").unwrap();
        let mono = render(&mut app);
        let blocks = filled(&mono);
        assert_eq!(blocks.len(), default.len());
        assert!(blocks.iter().all(|fg| *fg == ratatui::style::Color::Reset));
        assert!(mono
            .content()
            .iter()
            .all(|cell| cell.fg == ratatui::style::Color::Reset));
    }

    #[test]
    fn fit_middle_keeps_both_ends() {
        assert_eq!(fit_middle("short.wav", 20), "short.wav");
        assert_eq!(fit_middle("a-very-long-recording.wav", 12), "a-very…g.wav");
        assert_eq!(fit_middle("abcdef", 3), "abcdef", "too narrow to cut");
    }

    #[test]
    fn shortened_names_obey_display_cell_width_with_wide_characters() {
        let name = "🦄🦄🦄🦄🦄запись.wav";
        let shortened = fit_middle(name, 12);
        assert!(Line::from(shortened.as_str()).width() <= 12, "{shortened}");
        assert!(shortened.ends_with(".wav"));
    }

    #[test]
    fn queue_shows_parent_paths_and_removes_only_the_clicked_file() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        app.queue.add("/meetings/first/запись.wav".into());
        app.queue.add("/meetings/second/запись.wav".into());
        let backend = ratatui::backend::TestBackend::new(140, 30);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| draw_all(f, &mut app)).unwrap();
        let text = terminal.backend().to_string();
        assert!(text.contains("/meetings/first"), "{text}");
        assert!(text.contains("/meetings/second"), "{text}");
        assert!(text.contains("Delete"), "{text}");
        if std::env::var_os("GIGAAM_TEST_PREVIEW").is_some() {
            println!("{text}");
        }
        let rect = app
            .hits
            .items()
            .iter()
            .find(|(_, action)| *action == Action::RemoveFile(0))
            .unwrap()
            .0;
        let action = app.hits.hit(rect.x, rect.y).unwrap();
        assert_eq!(action, Action::RemoveFile(0));
        crate::app::dispatch(&mut app, action);
        assert_eq!(app.queue.items.len(), 1);
        assert_eq!(app.queue.items[0].path, "/meetings/second/запись.wav");
        assert_eq!(app.queue.selected_index(), Some(0));
    }

    #[test]
    fn scrolled_queue_delete_hits_match_visible_rows() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        for i in 0..30 {
            app.queue.add(format!("/recordings/meeting-{i}.wav"));
        }
        app.queue.select(29);
        let backend = ratatui::backend::TestBackend::new(100, 24);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| draw_all(f, &mut app)).unwrap();
        let offset = app.scroll[&AreaId::Queue] as usize;
        assert!(offset > 0);
        let rect = app
            .hits
            .items()
            .iter()
            .find(|(_, action)| *action == Action::RemoveFile(offset))
            .unwrap()
            .0;
        let action = app.hits.hit(rect.x, rect.y).unwrap();
        crate::app::dispatch(&mut app, action);
        assert!(!app
            .queue
            .items
            .iter()
            .any(|item| item.path == format!("/recordings/meeting-{offset}.wav")));
        assert_eq!(app.queue.items.len(), 29);
        app.activity = crate::lifecycle::Activity::Running(crate::lifecycle::JobKind::Asr);
        terminal.draw(|f| draw_all(f, &mut app)).unwrap();
        assert!(!app
            .hits
            .items()
            .iter()
            .any(|(_, action)| matches!(action, Action::RemoveFile(_))));
    }

    #[test]
    fn processing_page_renders_russian_headings_and_registers_param_rows() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        app.queue.add("/tmp/запись.wav".into());
        let backend = ratatui::backend::TestBackend::new(100, 30);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| draw_all(f, &mut app)).unwrap();
        let text = terminal.backend().to_string();
        for needle in [
            concat!("GigaAM TUI ", env!("CARGO_PKG_VERSION")),
            "Обработка",
            "LLM",
            "Настройки",
            "Журнал",
            "Очередь",
            "Параметры",
            "Движок",
            "Далее",
            "запись.wav",
            "ожидает",
            "Запустить",
        ] {
            assert!(text.contains(needle), "{needle}\n{text}");
        }
        assert!(app.hits.hit(2, 1).is_some(), "tab bar is clickable");
        assert_eq!(
            app.hits.hit(2, 1),
            Some(Action::Tab(crate::app::Page::Processing))
        );
        for (_, command) in PARAM_ROWS {
            assert!(
                app.hits
                    .items()
                    .iter()
                    .any(|(_, a)| *a == Action::OpenMenu(command)),
                "{command} row is clickable"
            );
        }
        assert!(app
            .hits
            .items()
            .iter()
            .any(|(_, a)| *a == Action::Button(ButtonId::Start)));
    }

    /// The sorted rows of every registered rect that `predicate` accepts.
    fn rows(app: &App, predicate: &dyn Fn(&Action) -> bool) -> Vec<u16> {
        let mut ys: Vec<u16> = app
            .hits
            .items()
            .iter()
            .filter(|(_, a)| predicate(a))
            .map(|(r, _)| r.y)
            .collect();
        ys.sort_unstable();
        ys
    }

    #[test]
    fn queue_and_menu_rows_have_distinct_consecutive_hit_rows() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        for path in ["/tmp/a.wav", "/tmp/b.wav", "/tmp/c.wav"] {
            app.queue.add(path.into());
        }
        app.activity = crate::lifecycle::Activity::Running(crate::lifecycle::JobKind::Asr);
        app.file_index = 0;
        app.current_file = Some("/tmp/a.wav".into());
        app.stage = "transcription".into();
        app.processed_seconds = Some(190.0);
        app.total_seconds = Some(450.0);
        let backend = ratatui::backend::TestBackend::new(100, 30);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| draw_all(f, &mut app)).unwrap();
        let text = terminal.backend().to_string();
        assert!(text.contains("в обработке"), "{text}");
        assert!(text.contains("00:03:10/00:07:30"), "{text}");
        let files = rows(&app, &|a| matches!(a, Action::SelectFile(_)));
        assert_eq!(files.len(), 3);
        assert!(files.windows(2).all(|w| w[1] == w[0] + 2), "{files:?}");
        assert_eq!(app.hits.hit(5, files[1]), Some(Action::SelectFile(1)));

        // A menu above the input line, drawn while idle.
        app.activity = crate::lifecycle::Activity::Idle;
        app.command_menu = Some("/backend".into());
        terminal.draw(|f| draw_all(f, &mut app)).unwrap();
        let menu = rows(&app, &|a| matches!(a, Action::MenuItem(_)));
        assert!(menu.len() >= 2, "{menu:?}");
        assert!(menu.windows(2).all(|w| w[1] == w[0] + 1), "{menu:?}");
        let input = rows(&app, &|a| *a == Action::FocusInput);
        assert_eq!(input.len(), 1);
        assert!(
            !menu.contains(&input[0]),
            "menu rows never cover the input line"
        );
        assert_eq!(*menu.last().unwrap() + 1, input[0]);
        assert_eq!(app.hits.hit(10, menu[1]), Some(Action::MenuItem(1)));
    }
}
