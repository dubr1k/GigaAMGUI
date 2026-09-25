use super::processing::fit_middle;
use crate::{
    app::App,
    commands::short_name,
    i18n::t,
    ui::{Action, AreaId},
};
use ratatui::{
    layout::{Constraint, Layout, Rect},
    style::Style,
    text::Line,
    widgets::{Block, Cell, Clear, Paragraph, Row, Table, TableState, Wrap},
};

pub(crate) fn draw(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let p = *app.palette();
    let rect = Rect::new(
        area.x + 2,
        area.y + 2,
        area.width.saturating_sub(4),
        area.height.saturating_sub(4),
    );
    let files = app.saved_results();
    app.result_cursor = app.result_cursor.min(files.len().saturating_sub(1));
    let block = Block::bordered()
        .title(format!(
            " {} ({}) ",
            t(app.lang, "results.title"),
            files.len()
        ))
        .border_style(Style::default().fg(p.accent));
    let inner = block.inner(rect);
    frame.render_widget(Clear, rect);
    frame.render_widget(block, rect);
    app.hits.clear();
    let [list, detail, notice, buttons] = Layout::vertical([
        Constraint::Min(2),
        Constraint::Length((inner.height / 3).max(3)),
        Constraint::Length(2),
        Constraint::Length(1),
    ])
    .areas(inner);
    if files.is_empty() {
        frame.render_widget(
            Paragraph::new(t(app.lang, "results.empty")).wrap(Wrap { trim: false }),
            list,
        );
    } else {
        let rows = files.iter().enumerate().map(|(i, path)| {
            let name = short_name(path);
            let parent = std::path::Path::new(path)
                .parent()
                .unwrap_or_else(|| std::path::Path::new(""))
                .to_string_lossy();
            Row::new(vec![
                Cell::from(format!("{}", i + 1)),
                Cell::from(vec![
                    Line::from(fit_middle(&name, list.width.saturating_sub(6) as usize)),
                    Line::styled(
                        fit_middle(&parent, list.width.saturating_sub(6) as usize),
                        Style::default().fg(p.muted),
                    ),
                ]),
            ])
            .height(2)
        });
        let mut state = TableState::default().with_selected(Some(app.result_cursor));
        let table = Table::new(rows, [Constraint::Length(4), Constraint::Min(1)])
            .row_highlight_style(p.emphasis());
        frame.render_stateful_widget(table, list, &mut state);
        app.hits.add(list, Action::Scroll(AreaId::Results, 0));
        for row in 0..usize::from(list.height / 2).min(files.len().saturating_sub(state.offset())) {
            app.hits.add(
                Rect::new(list.x, list.y + row as u16 * 2, list.width, 2),
                Action::SelectResult(state.offset() + row),
            );
        }
    }
    let path = files.get(app.result_cursor).map_or("", String::as_str);
    let paragraph = Paragraph::new(path)
        .wrap(Wrap { trim: false })
        .block(Block::bordered().title(t(app.lang, "results.path")));
    let offset = app.scroll.entry(AreaId::ResultPath).or_default();
    let max_scroll = paragraph
        .line_count(detail.width.saturating_sub(2))
        .saturating_sub(detail.height.saturating_sub(2) as usize)
        .min(u16::MAX as usize) as u16;
    *offset = (*offset).min(max_scroll);
    frame.render_widget(paragraph.scroll((*offset, 0)), detail);
    app.hits.add(detail, Action::Scroll(AreaId::ResultPath, 0));
    frame.render_widget(
        Paragraph::new(app.result_notice.as_str())
            .wrap(Wrap { trim: false })
            .style(Style::default().fg(p.muted)),
        notice,
    );
    let mut x = buttons.x;
    for (key, action) in [
        ("results.open", Action::OpenResult(false)),
        ("results.folder", Action::OpenResult(true)),
        ("results.close", Action::ShowResults(false)),
    ] {
        let label = format!("[{}]", t(app.lang, key));
        let width =
            (Line::from(label.as_str()).width() as u16).min(buttons.right().saturating_sub(x));
        let rect = Rect::new(x, buttons.y, width, buttons.height);
        frame.render_widget(
            Paragraph::new(label).style(Style::default().fg(p.accent)),
            rect,
        );
        app.hits.add(rect, action);
        x = x.saturating_add(width + 1);
    }
}

#[cfg(test)]
mod tests {
    use crate::{
        app::dispatch,
        i18n::Lang,
        ui::{self, Action},
    };

    #[test]
    fn result_picker_renders_and_scrolls_at_supported_sizes_in_both_languages() {
        for (width, height) in [(80, 24), (100, 30), (160, 40)] {
            for lang in [Lang::Ru, Lang::En] {
                for pet in [false, true] {
                    let mut app = crate::test_support::ready_app();
                    app.lang = lang;
                    app.pet_enabled = pet;
                    app.result_files = (0..40)
                        .map(|i| format!("/meetings/папка {i}/запись {i}.txt"))
                        .collect();
                    dispatch(&mut app, Action::ShowResults(true));
                    dispatch(&mut app, Action::SelectResult(39));
                    let mut terminal =
                        ratatui::Terminal::new(ratatui::backend::TestBackend::new(width, height))
                            .unwrap();
                    terminal.draw(|f| ui::draw(f, &mut app)).unwrap();
                    let text = terminal.backend().to_string();
                    assert!(
                        text.contains(if lang == Lang::Ru {
                            "Результаты"
                        } else {
                            "Results"
                        }),
                        "{text}"
                    );
                    assert!(text.contains("/meetings/папка 39/запись 39.txt"), "{text}");
                    assert!(!text.contains("запись 0.txt"), "{text}");
                    assert!(text.contains("Esc"), "{text}");
                    assert!(app
                        .hits
                        .items()
                        .iter()
                        .any(|(_, a)| *a == Action::SelectResult(39)));
                    assert!(!app
                        .hits
                        .items()
                        .iter()
                        .any(|(_, a)| matches!(a, Action::RemoveFile(_) | Action::Tab(_))));
                }
            }
        }
    }
}
