//! Rendering of the terminal UI.

use ratatui::{
    layout::{Constraint, Direction, Layout, Margin, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Gauge, Paragraph, Wrap},
};
use ratatui_image::StatefulImage;

use crate::{
    app::{llm_can_run, App},
    commands::{command_menu_options, command_suggestions, short_name, BACK_MENU_OPTION},
    i18n::t,
};

fn timecode(seconds: f64) -> String {
    format!(
        "{:02}:{:02}:{:02}",
        (seconds / 3600.0) as u64,
        ((seconds / 60.0) as u64) % 60,
        seconds as u64 % 60
    )
}

pub(crate) fn draw(frame: &mut ratatui::Frame, app: &mut App) {
    let area = frame.area();
    let menu_options = if app.running {
        Vec::new()
    } else {
        command_menu_options(app)
    };
    let suggestions = if app.running || !menu_options.is_empty() {
        Vec::new()
    } else {
        command_suggestions(&app.input)
    };
    let prompt_height = 3 + menu_options.len().max(suggestions.len()).min(8) as u16;
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(2),
            Constraint::Min(10),
            Constraint::Length(prompt_height),
            Constraint::Length(1),
        ])
        .split(area);
    let accent = Color::Rgb(92, 155, 255);
    let secondary = Color::Rgb(180, 195, 220);
    let header = Line::from(vec![
        Span::styled(
            " GigaAM",
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled("  terminal transcriber", Style::default().fg(secondary)),
        Span::raw(" "),
        Span::styled(
            if app.running {
                "● running"
            } else {
                "● idle"
            },
            Style::default().fg(if app.running { Color::Green } else { secondary }),
        ),
        Span::styled(
            format!(
                "   {} · {} · audio {}",
                app.backend,
                app.formats.join(","),
                app.audio_preprocessing_mode
            ),
            Style::default().fg(secondary),
        ),
    ]);
    frame.render_widget(Paragraph::new(header), chunks[0]);

    let mut body = Vec::<Line>::new();
    if app.files.is_empty() {
        body.push(Line::styled(
            "  Drop files here or type a path",
            Style::default().fg(secondary),
        ));
    } else {
        body.push(Line::styled(
            format!(
                "  {} file{} queued",
                app.files.len(),
                if app.files.len() == 1 { "" } else { "s" }
            ),
            Style::default().fg(Color::Gray),
        ));
        for (index, file) in app.files.iter().enumerate() {
            let current = app.running && app.file_index == index;
            let selected = !app.running && app.selected_file == Some(index);
            let symbol = if current {
                "●"
            } else if selected {
                "›"
            } else if app.running && index < app.file_index {
                "✓"
            } else {
                "○"
            };
            body.push(Line::from(vec![
                Span::styled(
                    format!("  {symbol} "),
                    Style::default().fg(if current || selected {
                        accent
                    } else {
                        secondary
                    }),
                ),
                Span::styled(
                    short_name(file),
                    Style::default()
                        .fg(if selected { accent } else { Color::White })
                        .add_modifier(if current || selected {
                            Modifier::BOLD
                        } else {
                            Modifier::empty()
                        }),
                ),
            ]));
            if current {
                let detail = match (app.processed_seconds, app.total_seconds) {
                    (Some(done), Some(total)) => {
                        format!("{} / {}", timecode(done), timecode(total))
                    }
                    _ => format!("{:>3}%", (app.progress * 100.0) as u16),
                };
                body.push(Line::styled(
                    format!(
                        "    {:<16} {:>3}%   {}",
                        app.stage,
                        (app.progress * 100.0) as u16,
                        detail
                    ),
                    Style::default().fg(Color::Gray),
                ));
            }
        }
    }
    body.push(Line::raw(""));
    body.push(Line::styled(
        format!("  {}", app.status),
        Style::default().fg(if app.running { accent } else { Color::Gray }),
    ));
    if !app.result_files.is_empty() {
        body.push(Line::raw(""));
        body.push(Line::styled("  Saved", Style::default().fg(Color::Green)));
        for file in app.result_files.iter().take(4) {
            body.push(Line::styled(
                format!("  {}", file),
                Style::default().fg(Color::Gray),
            ));
        }
    }
    if app.llm_running && !app.llm_stream.is_empty() {
        body.push(Line::raw(""));
        body.push(Line::styled(
            "  LLM · streaming",
            Style::default().fg(accent),
        ));
        for line in app
            .llm_stream
            .lines()
            .rev()
            .take(6)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
        {
            body.push(Line::styled(
                format!("  {line}"),
                Style::default().fg(Color::White),
            ));
        }
    } else if app.show_llm_result {
        for (mode, text) in &app.llm_results {
            body.push(Line::raw(""));
            body.push(Line::styled(
                format!("  LLM · {mode}"),
                Style::default().fg(Color::Green),
            ));
            for line in text.lines().take(12) {
                body.push(Line::styled(
                    format!("  {line}"),
                    Style::default().fg(Color::White),
                ));
            }
            if text.lines().count() > 12 {
                body.push(Line::styled(
                    "  … (full text in the saved file)",
                    Style::default().fg(Color::Gray),
                ));
            }
        }
    }
    if app.show_logs {
        body.push(Line::raw(""));
        body.push(Line::styled(
            "  ── activity ─────────────────────────",
            Style::default().fg(secondary),
        ));
        for line in app.logs.iter().rev().take(5).rev() {
            body.push(Line::styled(
                format!("  {}", line),
                Style::default().fg(secondary),
            ));
        }
    }
    let mut body_area = chunks[1].inner(Margin {
        horizontal: 1,
        vertical: 0,
    });
    // Keep text clear of the pet image instead of rendering under it.
    if app.pet_enabled && body_area.width > 20 {
        body_area.width = body_area.width.saturating_sub(18);
    }
    frame.render_widget(Paragraph::new(body).wrap(Wrap { trim: true }), body_area);
    if app.pet_enabled {
        if let Some(image) = app.pet_image.as_mut() {
            let pet_area = Rect::new(
                chunks[1].right().saturating_sub(18),
                chunks[1].y.saturating_add(1),
                16.min(chunks[1].width.saturating_sub(2)),
                8.min(chunks[1].height.saturating_sub(2)),
            );
            if pet_area.width >= 10 && pet_area.height >= 6 {
                frame.render_stateful_widget(StatefulImage::default(), pet_area, image);
            }
        }
    }
    if app.running {
        frame.render_widget(
            Gauge::default()
                .gauge_style(Style::default().fg(accent))
                .ratio(app.progress)
                .label(format!(" {}", app.stage)),
            chunks[2].inner(Margin {
                horizontal: 2,
                vertical: 1,
            }),
        );
    } else {
        let mut prompt_lines = if !menu_options.is_empty() {
            menu_options
                .iter()
                .enumerate()
                .map(|(index, option)| {
                    let selected = index
                        == app
                            .command_menu_index
                            .min(menu_options.len().saturating_sub(1));
                    Line::from(vec![
                        Span::styled(
                            if selected { "  › " } else { "    " },
                            Style::default().fg(accent),
                        ),
                        Span::styled(
                            if option == BACK_MENU_OPTION {
                                "0. ".into()
                            } else {
                                format!("{}. ", index + 1)
                            },
                            Style::default().fg(Color::DarkGray),
                        ),
                        Span::styled(
                            option,
                            Style::default()
                                .fg(if selected { Color::White } else { Color::Gray })
                                .add_modifier(if selected {
                                    Modifier::BOLD
                                } else {
                                    Modifier::empty()
                                }),
                        ),
                    ])
                })
                .collect::<Vec<_>>()
        } else {
            suggestions
                .iter()
                .enumerate()
                .map(|(index, (command, _))| {
                    let selected = index
                        == app
                            .selected_command
                            .min(suggestions.len().saturating_sub(1));
                    Line::from(vec![
                        Span::styled(
                            format!("  {command:<12}"),
                            Style::default().fg(accent).add_modifier(if selected {
                                Modifier::BOLD
                            } else {
                                Modifier::empty()
                            }),
                        ),
                        Span::styled(
                            t(
                                app.lang,
                                &format!("cmd.{}", command.trim_start_matches('/')),
                            ),
                            Style::default().fg(if selected { Color::White } else { Color::Gray }),
                        ),
                    ])
                })
                .collect::<Vec<_>>()
        };
        prompt_lines.push(Line::from(vec![
            Span::styled(
                "› ",
                Style::default().fg(accent).add_modifier(Modifier::BOLD),
            ),
            Span::raw(&app.input),
        ]));
        frame.render_widget(
            Paragraph::new(prompt_lines).block(
                Block::default()
                    .borders(Borders::TOP)
                    .border_style(Style::default().fg(Color::DarkGray)),
            ),
            chunks[2],
        );
    }
    let llm_active = llm_can_run(app);
    let footer = Line::from(vec![
        Span::styled(
            "Enter add path · Tab complete · type / for commands · s start · ",
            Style::default().fg(Color::Gray),
        ),
        Span::styled(
            "[L] Run LLM",
            Style::default()
                .fg(if llm_active { accent } else { Color::DarkGray })
                .add_modifier(if llm_active {
                    Modifier::BOLD
                } else {
                    Modifier::empty()
                }),
        ),
        Span::styled(
            " · Esc cancel · Esc×2 / Ctrl+C×2 exit · l logs · r LLM result",
            Style::default().fg(Color::Gray),
        ),
    ]);
    frame.render_widget(Paragraph::new(footer), chunks[3]);
}
