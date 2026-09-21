//! The «Обработка» page: the file queue, the parameter panel and the progress block.

use ratatui::{
    layout::{Constraint, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Cell, Gauge, Paragraph, Row, Table, TableState, Wrap},
};

use crate::{
    app::{App, FileState, Focus},
    commands::short_name,
    i18n::{t, tf, try_t},
    ui::{Action, AreaId, ButtonId, ACCENT, SECONDARY},
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
    draw_progress(frame, bottom, app);
}

fn timecode(seconds: f64) -> String {
    format!(
        "{:02}:{:02}:{:02}",
        (seconds / 3600.0) as u64,
        ((seconds / 60.0) as u64) % 60,
        seconds as u64 % 60
    )
}

/// Keeps the extension visible: `a-very-long-recording.wav` → `a-very…ing.wav`.
fn fit_middle(text: &str, width: usize) -> String {
    let chars: Vec<char> = text.chars().collect();
    if chars.len() <= width || width < 5 {
        return text.to_owned();
    }
    let tail = (width - 1) / 2;
    let head = width - 1 - tail;
    let mut out: String = chars[..head].iter().collect();
    out.push('…');
    out.extend(chars[chars.len() - tail..].iter());
    out
}

fn state_of(app: &App, index: usize, file: &str) -> FileState {
    if app.running
        && !app.llm_running
        && app.file_index == index
        && app.current_file.as_deref() == Some(file)
    {
        FileState::Processing
    } else {
        app.file_state(file)
    }
}

fn draw_queue(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let title = if app.files.is_empty() {
        t(app.lang, "queue.title").to_owned()
    } else {
        tf(
            app.lang,
            "queue.count",
            &[("n", &app.files.len().to_string())],
        )
    };
    let block = Block::bordered()
        .title(Span::styled(
            format!(" {title} "),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ))
        .title_top(
            Line::from(Span::styled(
                format!("[{}]", t(app.lang, "btn.clear")),
                Style::default().fg(if app.files.is_empty() || app.running {
                    Color::DarkGray
                } else {
                    SECONDARY
                }),
            ))
            .right_aligned(),
        )
        .border_style(Style::default().fg(if app.focus == Focus::Queue {
            ACCENT
        } else {
            Color::DarkGray
        }));
    let inner = block.inner(area);
    frame.render_widget(block, area);
    app.hits.add(area, Action::Scroll(AreaId::Queue, 0));
    let clear_width = t(app.lang, "btn.clear").chars().count() as u16 + 2;
    let clear_x = area.right().saturating_sub(1 + clear_width);
    app.hits.add(
        Rect::new(clear_x, area.y, clear_width, 1),
        Action::Button(ButtonId::ClearQueue),
    );
    if app.files.is_empty() {
        let height = inner.height.min(3);
        frame.render_widget(
            Paragraph::new(Line::styled(
                t(app.lang, "queue.empty"),
                Style::default().fg(SECONDARY),
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
    let name_width = usize::from(inner.width.saturating_sub(4 + 14 + 2));
    let rows: Vec<Row> = app
        .files
        .iter()
        .enumerate()
        .map(|(index, file)| {
            let state = state_of(app, index, file);
            let (key, colour) = match state {
                FileState::Pending => ("state.pending", SECONDARY),
                FileState::Processing => ("state.processing", ACCENT),
                FileState::Done => ("state.done", Color::Green),
                FileState::Failed => ("state.failed", Color::Red),
                FileState::Cancelled => ("state.cancelled", Color::Yellow),
            };
            Row::new(vec![
                Cell::from(format!("{:>2}.", index + 1))
                    .style(Style::default().fg(Color::DarkGray)),
                Cell::from(fit_middle(&short_name(file), name_width)),
                Cell::from(t(app.lang, key)).style(Style::default().fg(colour)),
            ])
        })
        .collect();
    let visible = usize::from(inner.height.saturating_sub(1));
    let max_offset = app.files.len().saturating_sub(visible);
    let offset = usize::from(*app.scroll.entry(AreaId::Queue).or_default()).min(max_offset);
    let selected = if app.running && !app.llm_running {
        Some(app.file_index).filter(|index| *index < app.files.len())
    } else {
        app.selected_file
    };
    let mut state = TableState::default()
        .with_offset(offset)
        .with_selected(selected);
    let highlight = if app.focus == Focus::Queue {
        Style::default()
            .fg(Color::White)
            .bg(Color::Rgb(40, 60, 100))
            .add_modifier(Modifier::BOLD)
    } else {
        Style::default().fg(ACCENT).add_modifier(Modifier::BOLD)
    };
    let table = Table::new(
        rows,
        [
            Constraint::Length(4),
            Constraint::Min(8),
            Constraint::Length(14),
        ],
    )
    .header(
        Row::new(vec![
            t(app.lang, "queue.col_no"),
            t(app.lang, "queue.col_file"),
            t(app.lang, "queue.col_state"),
        ])
        .style(
            Style::default()
                .fg(SECONDARY)
                .add_modifier(Modifier::UNDERLINED),
        ),
    )
    .row_highlight_style(highlight);
    frame.render_stateful_widget(table, inner, &mut state);
    // The table may have moved the window to keep the selection visible; the
    // wheel continues from where the frame actually is.
    let offset = state.offset();
    app.scroll.insert(AreaId::Queue, offset as u16);
    for row in 0..visible.min(app.files.len().saturating_sub(offset)) {
        app.hits.add(
            Rect::new(inner.x, inner.y + 1 + row as u16, inner.width, 1),
            Action::SelectFile(offset + row),
        );
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
    let block = Block::bordered()
        .title(Span::styled(
            format!(" {} ", t(app.lang, "params.title")),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ))
        .border_style(Style::default().fg(if app.focus == Focus::Params {
            ACCENT
        } else {
            Color::DarkGray
        }));
    let inner = block.inner(area);
    frame.render_widget(block, area);
    let rows: Vec<Row> = PARAM_ROWS
        .iter()
        .map(|(key, command)| {
            Row::new(vec![
                Cell::from(t(app.lang, key)).style(Style::default().fg(SECONDARY)),
                Cell::from(param_value(app, command)).style(Style::default().fg(Color::White)),
            ])
        })
        .collect();
    let mut state = TableState::default().with_selected(
        (app.focus == Focus::Params).then_some(app.params_cursor.min(PARAM_ROWS.len() - 1)),
    );
    let table = Table::new(rows, [Constraint::Length(12), Constraint::Min(6)]).row_highlight_style(
        Style::default()
            .fg(Color::White)
            .bg(Color::Rgb(40, 60, 100))
            .add_modifier(Modifier::BOLD),
    );
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

fn draw_progress(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let can_start = !app.running && !app.files.is_empty();
    let can_stop = app.running && !app.llm_running;
    let start = format!("[{}]", t(app.lang, "btn.start"));
    let stop = format!("[{}]", t(app.lang, "btn.stop"));
    let button_style = |active: bool| {
        Style::default()
            .fg(if active { ACCENT } else { Color::DarkGray })
            .add_modifier(if active {
                Modifier::BOLD
            } else {
                Modifier::empty()
            })
    };
    let block = Block::bordered()
        .title(Span::styled(
            format!(" {} ", t(app.lang, "progress.title")),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ))
        .title_top(
            Line::from(vec![
                Span::styled(start.clone(), button_style(can_start)),
                Span::raw(" "),
                Span::styled(stop.clone(), button_style(can_stop)),
            ])
            .right_aligned(),
        )
        .border_style(Style::default().fg(Color::DarkGray));
    let inner = block.inner(area);
    frame.render_widget(block, area);
    // Right-aligned titles end one cell before the corner.
    let stop_width = stop.chars().count() as u16;
    let start_width = start.chars().count() as u16;
    let stop_x = area.right().saturating_sub(1 + stop_width);
    let start_x = stop_x.saturating_sub(1 + start_width);
    app.hits.add(
        Rect::new(start_x, area.y, start_width, 1),
        Action::Button(ButtonId::Start),
    );
    app.hits.add(
        Rect::new(stop_x, area.y, stop_width, 1),
        Action::Button(ButtonId::Stop),
    );
    if inner.height == 0 {
        return;
    }
    let [gauge_area, text_area] = Layout::horizontal([Constraint::Length(22), Constraint::Min(10)])
        .areas(Rect { height: 1, ..inner });
    let percent = (app.progress * 100.0).round() as u16;
    frame.render_widget(
        Gauge::default()
            .gauge_style(Style::default().fg(ACCENT).bg(Color::Rgb(40, 40, 50)))
            .ratio(app.progress.clamp(0.0, 1.0))
            .label(format!("{percent}%")),
        gauge_area,
    );
    let first_line = if app.running && !app.llm_running {
        let stage = try_t(app.lang, &format!("stage.{}", app.stage))
            .map_or_else(|| app.stage.clone(), str::to_owned);
        match (app.processed_seconds, app.total_seconds) {
            (Some(done), Some(total)) => {
                format!(" {stage}  {}/{}", timecode(done), timecode(total))
            }
            _ => format!(" {stage}"),
        }
    } else {
        format!(" {}", app.status)
    };
    frame.render_widget(
        Paragraph::new(Line::styled(first_line, Style::default().fg(Color::White))),
        text_area,
    );
    let mut lines = Vec::<Line>::new();
    if !app.result_files.is_empty() {
        let saved = app
            .result_files
            .iter()
            .rev()
            .take(3)
            .rev()
            .map(|path| short_name(path))
            .collect::<Vec<_>>()
            .join(", ");
        lines.push(Line::from(vec![
            Span::styled(
                format!("{}: ", t(app.lang, "progress.saved")),
                Style::default().fg(Color::Green),
            ),
            Span::styled(saved, Style::default().fg(Color::Gray)),
        ]));
    }
    if app.running {
        lines.push(Line::styled(
            app.status.clone(),
            Style::default().fg(SECONDARY),
        ));
    }
    if inner.height > 1 && !lines.is_empty() {
        frame.render_widget(
            Paragraph::new(lines),
            Rect {
                y: inner.y + 1,
                height: inner.height - 1,
                ..inner
            },
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{settings::isolated_config_dir, ui::draw as draw_all};

    #[test]
    fn fit_middle_keeps_both_ends() {
        assert_eq!(fit_middle("short.wav", 20), "short.wav");
        assert_eq!(fit_middle("a-very-long-recording.wav", 12), "a-very…g.wav");
        assert_eq!(fit_middle("abcdef", 3), "abcdef", "too narrow to cut");
    }

    #[test]
    fn processing_page_renders_russian_headings_and_registers_param_rows() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.files = vec!["/tmp/запись.wav".into()];
        let backend = ratatui::backend::TestBackend::new(100, 30);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| draw_all(f, &mut app)).unwrap();
        let text = terminal.backend().to_string();
        for needle in [
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
        let mut app = App::default();
        app.files = vec![
            "/tmp/a.wav".into(),
            "/tmp/b.wav".into(),
            "/tmp/c.wav".into(),
        ];
        app.running = true;
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
        assert!(files.windows(2).all(|w| w[1] == w[0] + 1), "{files:?}");
        assert_eq!(app.hits.hit(5, files[1]), Some(Action::SelectFile(1)));

        // A menu above the input line, drawn while idle.
        app.running = false;
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
