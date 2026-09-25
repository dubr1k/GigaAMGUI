//! Общий прогресс партии и текущего файла.
use super::processing::timecode;
use crate::commands::short_name;
use crate::{
    app::App,
    i18n::{t, tf, try_t},
    queue::RunSelection,
    ui::{Action, ButtonId},
};
use ratatui::{
    layout::{Constraint, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Gauge, Paragraph},
};

pub(crate) fn draw(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let p = *app.palette();
    let result_label = format!(
        "[F9: {} ({})]",
        t(app.lang, "results.title"),
        app.saved_results().len()
    );
    let can_start = app.can_start(RunSelection::Pending);
    let reconnect = app.connection.state == crate::lifecycle::ConnectionState::Unavailable;
    let can_stop = app.running() && !app.llm_running();
    let stopping = can_stop && app.activity.is_stopping();
    let start = format!(
        "[{}]",
        t(
            app.lang,
            if reconnect {
                "button.reconnect"
            } else {
                "btn.start"
            }
        )
    );
    let stop = format!(
        "[{}]",
        t(
            app.lang,
            if stopping {
                "btn.force_stop"
            } else {
                "btn.stop"
            }
        )
    );
    let button_style = |active: bool| {
        Style::default()
            .fg(if active { p.accent } else { p.disabled })
            .add_modifier(if active {
                Modifier::BOLD
            } else {
                Modifier::empty()
            })
    };
    let block = Block::bordered()
        .title(Span::styled(
            format!(" {} ", t(app.lang, "progress.title")),
            p.title(),
        ))
        .title_top(
            Line::from(vec![
                Span::styled(start.clone(), button_style(can_start || reconnect)),
                Span::raw(" "),
                Span::styled(stop.clone(), button_style(can_stop)),
            ])
            .right_aligned(),
        )
        .title_bottom(Span::styled(
            result_label.clone(),
            Style::default().fg(p.accent),
        ))
        .border_style(Style::default().fg(p.border));
    let inner = block.inner(area);
    frame.render_widget(block, area);
    app.hits.add(
        Rect::new(
            area.x + 1,
            area.bottom().saturating_sub(1),
            (Line::from(result_label).width() as u16).min(area.width.saturating_sub(2)),
            1,
        ),
        Action::ShowResults(true),
    );
    // Right-aligned titles end one cell before the corner.
    let stop_width = stop.chars().count() as u16;
    let start_width = start.chars().count() as u16;
    let stop_x = area.right().saturating_sub(1 + stop_width);
    let start_x = stop_x.saturating_sub(1 + start_width);
    if can_start || reconnect {
        app.hits.add(
            Rect::new(start_x, area.y, start_width, 1),
            if reconnect {
                Action::Reconnect
            } else {
                Action::Button(ButtonId::Start)
            },
        );
    }
    if can_stop {
        app.hits.add(
            Rect::new(stop_x, area.y, stop_width, 1),
            if stopping {
                Action::ForceStop
            } else {
                Action::Button(ButtonId::Stop)
            },
        );
    }
    if inner.height == 0 {
        return;
    }
    let [gauge_area, text_area] = Layout::horizontal([Constraint::Length(30), Constraint::Min(10)])
        .areas(Rect { height: 1, ..inner });
    let overall = app.overall_progress();
    let percent = (overall * 100.0).round() as u16;
    frame.render_widget(
        Gauge::default()
            .gauge_style(Style::default().fg(p.accent).bg(p.gauge_bg))
            .ratio(overall)
            .label(format!("{} {percent}%", t(app.lang, "progress.overall"))),
        gauge_area,
    );
    let first_line =
        if app.activity == crate::lifecycle::Activity::Starting(crate::lifecycle::JobKind::Asr) {
            format!(" {}", t(app.lang, "status.batch_starting"))
        } else if app.running() && !app.llm_running() {
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
        Paragraph::new(Line::styled(first_line, Style::default().fg(p.text))),
        text_area,
    );
    let mut lines = Vec::<Line>::new();
    if app.running() && !app.llm_running() {
        let current = if app.stage == "preparing" {
            "—".into()
        } else {
            format!("{}%", (app.progress * 100.0).round() as u16)
        };
        if let Some(file) = &app.current_file {
            lines.push(Line::from(format!(
                "{} {}/{}: {current} · {}",
                t(app.lang, "progress.file"),
                app.file_index + 1,
                app.total_files,
                short_name(file)
            )));
        }
    } else if let Some(summary) = &app.batch_summary {
        lines.push(Line::from(tf(
            app.lang,
            "progress.summary",
            &[
                ("ok", &summary.succeeded.to_string()),
                ("failed", &summary.failed.to_string()),
                ("unstarted", &summary.unstarted.to_string()),
                ("interrupted", &summary.interrupted.to_string()),
                ("time", &timecode(summary.elapsed.as_secs_f64())),
            ],
        )));
        if summary.cancelled {
            lines.push(Line::styled(
                t(app.lang, "status.cancelled"),
                Style::default().fg(p.muted),
            ));
        }
    }
    if app.running() {
        lines.push(Line::styled(
            app.status.clone(),
            Style::default().fg(p.muted),
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
