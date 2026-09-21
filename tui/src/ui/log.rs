//! The «Журнал» page: the whole log with scrolling.
//!
//! The view follows new lines while the user is at the bottom; scrolling up
//! (wheel, `Up`, `PgUp`) pins it, and reaching the end again or `End` releases it.
//! `Ctrl+L` and the `[Очистить]` button clear the log.

use ratatui::{
    layout::Rect,
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Paragraph, Wrap},
};

use crate::{
    app::App,
    i18n::t,
    ui::{Action, AreaId, ButtonId, SECONDARY},
};

pub(crate) fn draw(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let clear = format!("[{}]", t(app.lang, "btn.clear"));
    let block = Block::bordered()
        .title(Span::styled(
            format!(" {} ", t(app.lang, "log.title")),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ))
        .title_top(
            Line::from(Span::styled(
                clear.clone(),
                Style::default().fg(if app.logs.is_empty() {
                    Color::DarkGray
                } else {
                    SECONDARY
                }),
            ))
            .right_aligned(),
        )
        .title_bottom(
            Line::from(Span::styled(
                format!(" {} ", t(app.lang, "log.hint")),
                Style::default().fg(SECONDARY),
            ))
            .right_aligned(),
        )
        .border_style(Style::default().fg(Color::DarkGray));
    let inner = block.inner(area);
    frame.render_widget(block, area);
    app.hits.add(area, Action::Scroll(AreaId::Log, 0));
    let clear_width = clear.chars().count() as u16;
    app.hits.add(
        Rect::new(
            area.right().saturating_sub(1 + clear_width),
            area.y,
            clear_width,
            1,
        ),
        Action::Button(ButtonId::ClearLog),
    );
    if inner.height == 0 || inner.width == 0 {
        return;
    }
    if app.logs.is_empty() {
        frame.render_widget(
            Paragraph::new(Line::styled(
                t(app.lang, "log.empty"),
                Style::default().fg(SECONDARY),
            ))
            .centered(),
            Rect::new(inner.x, inner.y + inner.height / 2, inner.width, 1),
        );
        return;
    }
    let paragraph = Paragraph::new(app.logs.join("\n")).wrap(Wrap { trim: false });
    let lines = paragraph.line_count(inner.width).min(usize::from(u16::MAX)) as u16;
    let max_offset = lines.saturating_sub(inner.height);
    let offset = app.scroll.entry(AreaId::Log).or_default();
    *offset = if app.log_follow {
        max_offset
    } else {
        (*offset).min(max_offset)
    };
    // Scrolling back down to the end re-arms the follow.
    app.log_follow = *offset >= max_offset;
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
    fn log_page_follows_the_tail_until_the_user_scrolls_up() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.page = Page::Log;
        app.logs.clear();
        for n in 1..=100 {
            app.log(format!("line {n}"));
        }
        let text = render(&mut app);
        assert!(text.contains("Журнал"), "{text}");
        assert!(text.contains("line 100"), "{text}");
        let bottom = app.scroll[&AreaId::Log];
        assert!(bottom > 0);
        assert!(app.log_follow);

        dispatch(&mut app, Action::Scroll(AreaId::Log, -3));
        assert!(!app.log_follow);
        render(&mut app);
        assert_eq!(app.scroll[&AreaId::Log], bottom - 3);
        for n in 101..=110 {
            app.log(format!("line {n}"));
        }
        let text = render(&mut app);
        assert_eq!(app.scroll[&AreaId::Log], bottom - 3, "pinned while reading");
        assert!(!text.contains("line 110"), "{text}");

        dispatch(&mut app, Action::Scroll(AreaId::Log, 500));
        let text = render(&mut app);
        assert!(app.log_follow, "reaching the end re-arms the follow");
        assert!(text.contains("line 110"), "{text}");
    }

    #[test]
    fn clear_button_empties_the_log() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.page = Page::Log;
        render(&mut app);
        let has = |action: Action| app.hits.items().iter().any(|(_, a)| *a == action);
        assert!(has(Action::Button(ButtonId::ClearLog)));
        assert!(has(Action::Scroll(AreaId::Log, 0)));
        assert!(!app.logs.is_empty());
        dispatch(&mut app, Action::Button(ButtonId::ClearLog));
        assert!(app.logs.is_empty());
        let text = render(&mut app);
        assert!(text.contains("Журнал пуст"), "{text}");
    }
}
