//! The «LLM» page: the transcripts to feed, the modes and provider, and the answer.
//!
//! Top left «Транскрипты» lists what `llm_input_files` would send (session results
//! and `/llm-file` entries); top right «Что сделать» holds the mode checkboxes, the
//! prompt and the provider rows with the run/cancel buttons in its title; the bottom
//! «Ответ» pane shows the stream while the worker runs and the last answer after.

use ratatui::{
    layout::{Constraint, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Cell, Paragraph, Row, Table, TableState, Wrap},
};

use crate::{
    app::{esc_should_soft_cancel, llm_can_run, llm_input_files, App},
    commands::short_name,
    i18n::{t, tf},
    ui::{processing::fit_middle, Action, AreaId, ButtonId, ACCENT, SECONDARY},
    worker::llm_tool_for,
};

/// The mode checkboxes, top to bottom: the worker's mode id and its label key.
pub(crate) const MODES: [(&str, &str); 4] = [
    ("summary", "llm.mode_summary"),
    ("tasks", "llm.mode_tasks"),
    ("terms", "llm.mode_terms"),
    ("custom", "llm.mode_custom"),
];

/// Rows of the «Что сделать» block plus its border: four modes, prompt, provider.
const TASKS_HEIGHT: u16 = MODES.len() as u16 + 2 + 2;

const HIGHLIGHT: Style = Style::new()
    .fg(Color::White)
    .bg(Color::Rgb(40, 60, 100))
    .add_modifier(Modifier::BOLD);

pub(crate) fn draw(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let [top, bottom] =
        Layout::vertical([Constraint::Length(TASKS_HEIGHT), Constraint::Min(3)]).areas(area);
    let [inputs_area, tasks_area] =
        Layout::horizontal([Constraint::Percentage(50), Constraint::Percentage(50)]).areas(top);
    draw_inputs(frame, inputs_area, app);
    draw_tasks(frame, tasks_area, app);
    draw_answer(frame, bottom, app);
}

fn title_style() -> Style {
    Style::default()
        .fg(Color::White)
        .add_modifier(Modifier::BOLD)
}

fn draw_inputs(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let files = llm_input_files(app);
    let title = if files.is_empty() {
        t(app.lang, "llm.inputs").to_owned()
    } else {
        tf(
            app.lang,
            "llm.inputs_count",
            &[("n", &files.len().to_string())],
        )
    };
    let block = Block::bordered()
        .title(Span::styled(format!(" {title} "), title_style()))
        .border_style(Style::default().fg(Color::DarkGray));
    let inner = block.inner(area);
    frame.render_widget(block, area);
    if files.is_empty() {
        let height = inner.height.min(3);
        frame.render_widget(
            Paragraph::new(Line::styled(
                t(app.lang, "llm.inputs_empty"),
                Style::default().fg(SECONDARY),
            ))
            .centered()
            .wrap(Wrap { trim: true }),
            Rect::new(
                inner.x,
                inner.y + inner.height.saturating_sub(height) / 2,
                inner.width,
                height,
            ),
        );
        return;
    }
    let source_width: u16 = 8;
    let name_width = usize::from(inner.width.saturating_sub(4 + source_width + 2));
    let rows: Vec<Row> = files
        .iter()
        .enumerate()
        .map(|(index, file)| {
            let (key, colour) = if app.result_files.contains(file) {
                ("llm.src_session", Color::Green)
            } else {
                ("llm.src_file", SECONDARY)
            };
            Row::new(vec![
                Cell::from(format!("{:>2}.", index + 1))
                    .style(Style::default().fg(Color::DarkGray)),
                Cell::from(fit_middle(&short_name(file), name_width)),
                Cell::from(t(app.lang, key)).style(Style::default().fg(colour)),
            ])
        })
        .collect();
    app.llm_input_cursor = app.llm_input_cursor.min(files.len() - 1);
    // The cursor is either here or on a mode row, never in both places.
    let selected = app
        .llm_mode_cursor
        .is_none()
        .then_some(app.llm_input_cursor);
    let mut state = TableState::default().with_selected(selected);
    let table = Table::new(
        rows,
        [
            Constraint::Length(4),
            Constraint::Min(8),
            Constraint::Length(source_width),
        ],
    )
    .header(
        Row::new(vec![
            t(app.lang, "queue.col_no"),
            t(app.lang, "queue.col_file"),
            t(app.lang, "llm.col_source"),
        ])
        .style(
            Style::default()
                .fg(SECONDARY)
                .add_modifier(Modifier::UNDERLINED),
        ),
    )
    .row_highlight_style(HIGHLIGHT);
    frame.render_stateful_widget(table, inner, &mut state);
    let offset = state.offset();
    let visible = usize::from(inner.height.saturating_sub(1));
    for row in 0..visible.min(files.len().saturating_sub(offset)) {
        app.hits.add(
            Rect::new(inner.x, inner.y + 1 + row as u16, inner.width, 1),
            Action::LlmInput(offset + row),
        );
    }
}

fn provider_line(app: &App) -> String {
    let provider = app.llm_provider.as_str();
    let detail = match llm_tool_for(app, provider) {
        Some(tool) if tool.status == "found" => tool.version.clone().unwrap_or_default(),
        Some(tool) if tool.status == "broken" => t(app.lang, "llm.broken").to_owned(),
        Some(tool) if tool.status == "missing" => t(app.lang, "llm.not_installed").to_owned(),
        _ if provider == "API" && !app.llm_model.is_empty() => app.llm_model.clone(),
        _ => String::new(),
    };
    if detail.is_empty() {
        provider.to_owned()
    } else {
        format!("{provider} · {detail}")
    }
}

fn draw_tasks(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let can_run = !app.running && llm_can_run(app);
    let can_cancel = esc_should_soft_cancel(app);
    let run = format!("[{}]", t(app.lang, "btn.run_llm"));
    let cancel = format!("[{}]", t(app.lang, "btn.cancel_llm"));
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
            format!(" {} ", t(app.lang, "llm.tasks")),
            title_style(),
        ))
        .title_top(
            Line::from(vec![
                Span::styled(run.clone(), button_style(can_run)),
                Span::raw(" "),
                Span::styled(cancel.clone(), button_style(can_cancel)),
            ])
            .right_aligned(),
        )
        .border_style(Style::default().fg(Color::DarkGray));
    let inner = block.inner(area);
    frame.render_widget(block, area);
    // Right-aligned titles end one cell before the corner.
    let cancel_width = cancel.chars().count() as u16;
    let run_width = run.chars().count() as u16;
    let cancel_x = area.right().saturating_sub(1 + cancel_width);
    let run_x = cancel_x.saturating_sub(1 + run_width);
    app.hits.add(
        Rect::new(run_x, area.y, run_width, 1),
        Action::Button(ButtonId::RunLlm),
    );
    app.hits.add(
        Rect::new(cancel_x, area.y, cancel_width, 1),
        Action::Button(ButtonId::CancelLlm),
    );

    let label = Style::default().fg(SECONDARY);
    let value = Style::default().fg(Color::White);
    let mut lines: Vec<(Line, Action)> = MODES
        .iter()
        .enumerate()
        .map(|(index, (mode, key))| {
            let on = app.llm_modes.iter().any(|item| item == mode);
            let mut line = Line::from(vec![
                Span::styled(
                    if on { "[x] " } else { "[ ] " },
                    Style::default().fg(if on { ACCENT } else { SECONDARY }),
                ),
                Span::styled(t(app.lang, key), if on { value } else { label }),
            ]);
            if app.llm_mode_cursor == Some(index) {
                line = line.style(HIGHLIGHT);
            }
            (line, Action::ToggleMode(mode))
        })
        .collect();
    let prompt = if app.llm_prompt.is_empty() {
        Span::styled(t(app.lang, "llm.prompt_empty"), label)
    } else {
        Span::styled(app.llm_prompt.replace('\n', " "), value)
    };
    lines.push((
        Line::from(vec![
            Span::styled(format!("{}: ", t(app.lang, "llm.prompt")), label),
            prompt,
        ]),
        Action::EditCommand("/llm-prompt"),
    ));
    lines.push((
        Line::from(vec![
            Span::styled(format!("{}: ", t(app.lang, "llm.provider")), label),
            Span::styled(provider_line(app), value),
        ]),
        Action::OpenMenu("/settings-provider"),
    ));
    for (row, (line, action)) in lines.into_iter().enumerate() {
        if row as u16 >= inner.height {
            break;
        }
        let rect = Rect::new(inner.x, inner.y + row as u16, inner.width, 1);
        frame.render_widget(Paragraph::new(line), rect);
        app.hits.add(rect, action);
    }
}

/// The file `llm_completed` saved for `mode`, if the last run wrote one.
fn saved_file_for<'a>(app: &'a App, mode: &str) -> Option<&'a str> {
    let suffix = format!("llm_{mode}.txt");
    app.llm_saved_files
        .iter()
        .find(|path| short_name(path).ends_with(&suffix))
        .map(String::as_str)
}

fn draw_answer(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let (mode, body): (Option<&str>, &str) = if app.llm_running {
        (Some(app.llm_stream_mode.as_str()), app.llm_stream.as_str())
    } else if let Some((mode, text)) = app.llm_results.last() {
        (Some(mode.as_str()), text.as_str())
    } else {
        (None, "")
    };
    let mut title = format!(" {}", t(app.lang, "llm.answer"));
    if let Some(mode) = mode {
        title.push_str(&format!(" · {mode}"));
    }
    if app.llm_running {
        title.push_str(&format!(" · {}", t(app.lang, "llm.streaming")));
    } else if let Some(path) = mode.and_then(|mode| saved_file_for(app, mode)) {
        title.push_str(&format!(
            " · {}",
            tf(app.lang, "llm.saved", &[("name", &short_name(path))])
        ));
    }
    title.push(' ');
    let block = Block::bordered()
        .title(Span::styled(title, title_style()))
        .border_style(Style::default().fg(if app.llm_running {
            Color::Green
        } else {
            Color::DarkGray
        }));
    let inner = block.inner(area);
    frame.render_widget(block, area);
    app.hits.add(area, Action::Scroll(AreaId::LlmOutput, 0));
    if inner.height == 0 || inner.width == 0 {
        return;
    }
    if body.is_empty() && !app.llm_running {
        frame.render_widget(
            Paragraph::new(Line::styled(
                t(app.lang, "llm.answer_empty"),
                Style::default().fg(SECONDARY),
            ))
            .centered()
            .wrap(Wrap { trim: true }),
            Rect::new(inner.x, inner.y + inner.height / 2, inner.width, 1),
        );
        return;
    }
    let paragraph = Paragraph::new(body).wrap(Wrap { trim: false });
    let lines = paragraph.line_count(inner.width).min(usize::from(u16::MAX)) as u16;
    let max_offset = lines.saturating_sub(inner.height);
    let offset = app.scroll.entry(AreaId::LlmOutput).or_default();
    // The stream follows its tail; a finished answer keeps where the wheel left it.
    *offset = if app.llm_running {
        max_offset
    } else {
        (*offset).min(max_offset)
    };
    let offset = *offset;
    frame.render_widget(paragraph.scroll((offset, 0)), inner);
}

#[cfg(test)]
mod tests {
    use crate::{
        app::{dispatch, App, Page},
        settings::isolated_config_dir,
        ui::{draw as draw_all, Action, AreaId, ButtonId},
    };

    fn render(app: &mut App) -> String {
        let backend = ratatui::backend::TestBackend::new(100, 30);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| draw_all(f, app)).unwrap();
        terminal.backend().to_string()
    }

    #[test]
    fn llm_page_renders_headings_result_and_registers_modes_and_run() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.page = Page::Llm;
        app.result_files = vec!["/tmp/a.txt".into()];
        app.llm_results = vec![("summary".into(), "Итог".into())];
        app.llm_saved_files = vec!["/tmp/session_llm_summary.txt".into()];
        let text = render(&mut app);
        for needle in [
            "Транскрипты (1)",
            "Что сделать",
            "[x] Выжимка",
            "[ ] Задачи",
            "Провайдер: API",
            "Ответ · summary · сохранено: session_llm_summary.txt",
            "a.txt",
            "сессия",
            "Итог",
            "[Запустить LLM]",
        ] {
            assert!(text.contains(needle), "{needle}\n{text}");
        }
        let has = |action: Action| app.hits.items().iter().any(|(_, a)| *a == action);
        assert!(has(Action::ToggleMode("tasks")));
        assert!(has(Action::Button(ButtonId::RunLlm)));
        assert!(has(Action::Button(ButtonId::CancelLlm)));
        assert!(has(Action::LlmInput(0)));
        assert!(has(Action::OpenMenu("/settings-provider")));
        assert!(has(Action::EditCommand("/llm-prompt")));
        assert!(has(Action::Scroll(AreaId::LlmOutput, 0)));
        assert!(dispatch(&mut app, Action::ToggleMode("tasks")).is_empty());
        assert_eq!(app.llm_modes, vec!["summary", "tasks"]);
        let commands = dispatch(&mut app, Action::Button(ButtonId::RunLlm));
        assert_eq!(commands.len(), 1);
        assert_eq!(commands[0]["type"], "llm_start");
        assert_eq!(commands[0]["files"], serde_json::json!(["/tmp/a.txt"]));
    }

    #[test]
    fn answer_scroll_is_clamped_to_the_text_height() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.page = Page::Llm;
        app.llm_results = vec![("summary".into(), "строка 1\nстрока 2".into())];
        dispatch(&mut app, Action::Scroll(AreaId::LlmOutput, 3));
        assert_eq!(app.scroll[&AreaId::LlmOutput], 3);
        render(&mut app);
        assert_eq!(app.scroll[&AreaId::LlmOutput], 0);

        // A long answer scrolls up to its last screen and no further.
        app.llm_results = vec![("summary".into(), "x\n".repeat(200))];
        dispatch(&mut app, Action::Scroll(AreaId::LlmOutput, 500));
        let text = render(&mut app);
        let offset = app.scroll[&AreaId::LlmOutput];
        assert!(offset > 0 && offset < 200, "{offset}");
        assert!(text.contains("Ответ · summary "), "{text}");
    }

    #[test]
    fn stream_follows_its_tail_while_running() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.page = Page::Llm;
        app.llm_running = true;
        app.llm_stream_mode = "tasks".into();
        app.llm_stream = (1..=100).map(|n| format!("line {n}\n")).collect();
        let text = render(&mut app);
        assert!(text.contains("Ответ · tasks · стрим…"), "{text}");
        assert!(text.contains("line 100"), "{text}");
        assert!(!text.contains("line 1\n"), "{text}");
        assert!(text.contains("[Отменить]"));
    }

    #[test]
    fn delete_removes_only_extra_files_and_edit_command_prefills_the_input() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.page = Page::Llm;
        app.result_files = vec!["/tmp/a.txt".into()];
        app.llm_extra_files = vec!["/tmp/b.md".into()];
        dispatch(&mut app, Action::RemoveLlmInput(0));
        assert_eq!(app.result_files, vec!["/tmp/a.txt"], "session results stay");
        dispatch(&mut app, Action::LlmInput(1));
        assert_eq!(app.llm_input_cursor, 1);
        dispatch(&mut app, Action::RemoveLlmInput(1));
        assert!(app.llm_extra_files.is_empty());
        assert_eq!(app.llm_input_cursor, 0);
        dispatch(&mut app, Action::EditCommand("/llm-prompt"));
        assert_eq!(app.input, "/llm-prompt ");
    }
}
