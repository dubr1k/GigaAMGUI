//! The `?` overlay: the key legend on the left, every `/command` on the right.
//!
//! It is drawn last, over the page, and registers `Action::Help` on the whole
//! screen, so a click anywhere closes it (as `Esc` and `?` do). Both columns
//! scroll together with the wheel or `Up`/`Down`/`PgUp`/`PgDn` when the box is
//! shorter than its longest column.

use ratatui::{
    layout::{Constraint, Flex, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Clear, Paragraph},
};

use crate::{
    app::App,
    commands::COMMANDS,
    i18n::t,
    ui::{Action, AreaId, ACCENT, SECONDARY},
};

/// The key legend, top to bottom: the key as shown and the description key.
const KEYS: [(&str, &str); 19] = [
    ("Tab/Shift+Tab", "help.key_tab"),
    ("F1–F4", "help.key_fn"),
    ("s", "help.key_s"),
    ("L", "help.key_l"),
    ("r", "help.key_r"),
    ("d", "help.key_d"),
    ("f", "help.key_f"),
    ("?", "help.key_help"),
    ("Esc", "help.key_esc"),
    ("q", "help.key_q"),
    ("Ctrl+C", "help.key_ctrl_c"),
    ("↑ ↓", "help.key_arrows"),
    ("← →", "help.key_left_right"),
    ("Enter", "help.key_enter"),
    ("Space", "help.key_space"),
    ("Delete", "help.key_delete"),
    ("Ctrl+↑/↓", "help.key_ctrl_arrows"),
    ("PgUp/PgDn/End", "help.key_page"),
    ("Ctrl+L", "help.key_ctrl_l"),
];

/// Breaks `text` into lines of at most `width` characters at spaces, so that a
/// wrapped description can be indented under its key (ratatui's `Wrap` cannot).
/// A single word longer than `width` (a path, a long option list) is cut at the
/// width rather than overflowing the column.
fn wrap_words(text: &str, width: usize) -> Vec<String> {
    let width = width.max(1);
    let mut lines = Vec::new();
    let mut line = String::new();
    for word in text.split_whitespace() {
        let joined = line.chars().count() + usize::from(!line.is_empty()) + word.chars().count();
        if !line.is_empty() && joined > width {
            lines.push(std::mem::take(&mut line));
        }
        if !line.is_empty() {
            line.push(' ');
        }
        let mut rest: Vec<char> = word.chars().collect();
        while line.chars().count() + rest.len() > width {
            let room = width.saturating_sub(line.chars().count());
            if room == 0 {
                lines.push(std::mem::take(&mut line));
                continue;
            }
            line.extend(rest.drain(..room));
            lines.push(std::mem::take(&mut line));
        }
        line.extend(rest);
    }
    if !line.is_empty() || lines.is_empty() {
        lines.push(line);
    }
    lines
}

/// `key` in the accent colour, then `description` wrapped with a hanging indent.
fn entry<'a>(key: &str, description: &str, pad: usize, width: usize) -> Vec<Line<'a>> {
    let key_style = Style::default().fg(ACCENT).add_modifier(Modifier::BOLD);
    let text_style = Style::default().fg(Color::White);
    wrap_words(description, width.saturating_sub(pad + 2))
        .into_iter()
        .enumerate()
        .map(|(index, chunk)| {
            let head = if index == 0 { key } else { "" };
            Line::from(vec![
                Span::styled(format!(" {head:<pad$} "), key_style),
                Span::styled(chunk, text_style),
            ])
        })
        .collect()
}

/// Below this inner width the commands go under the keys instead of beside them.
const TWO_COLUMNS_MIN: u16 = 96;

/// The box: 80 % of the screen, centred, but never narrower than the two
/// columns need while the screen allows it (a small terminal gets nearly all of it).
fn overlay_rect(area: Rect) -> Rect {
    let width = (area.width * 4 / 5)
        .max(TWO_COLUMNS_MIN + 2)
        .min(area.width.saturating_sub(2));
    let height = (area.height * 4 / 5)
        .max(30)
        .min(area.height.saturating_sub(2));
    let [vertical] = Layout::vertical([Constraint::Length(height)])
        .flex(Flex::Center)
        .areas(area);
    let [rect] = Layout::horizontal([Constraint::Length(width)])
        .flex(Flex::Center)
        .areas(vertical);
    rect
}

pub(crate) fn draw(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    // Any click closes the overlay; the wheel anywhere scrolls it (the page
    // underneath is covered, so its scroll areas must not catch the wheel).
    app.hits.add(area, Action::Scroll(AreaId::Help, 0));
    app.hits.add(area, Action::Help);
    let rect = overlay_rect(area);
    app.hits.add(rect, Action::Help);
    frame.render_widget(Clear, rect);
    let block = Block::bordered()
        .title(Span::styled(
            format!(" {} ", t(app.lang, "help.title")),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ))
        .title_bottom(
            Line::from(Span::styled(
                format!(" {} ", t(app.lang, "help.close")),
                Style::default().fg(SECONDARY),
            ))
            .right_aligned(),
        )
        .border_style(Style::default().fg(ACCENT))
        .style(Style::default().bg(Color::Rgb(20, 24, 34)));
    let inner = block.inner(rect);
    frame.render_widget(block, rect);
    if inner.height == 0 || inner.width < 10 {
        return;
    }
    let two_columns = inner.width >= TWO_COLUMNS_MIN;
    let [keys_area, commands_area] = if two_columns {
        Layout::horizontal([Constraint::Percentage(42), Constraint::Percentage(58)]).areas(inner)
    } else {
        [inner, inner]
    };
    let heading = |key: &str| {
        Line::from(Span::styled(
            t(app.lang, key).to_owned(),
            Style::default()
                .fg(SECONDARY)
                .add_modifier(Modifier::UNDERLINED),
        ))
    };
    let key_width = usize::from(keys_area.width);
    let mut keys: Vec<Line> = vec![heading("help.keys")];
    for (key, description) in KEYS {
        keys.extend(entry(key, t(app.lang, description), 13, key_width));
    }
    keys.push(Line::raw(""));
    keys.extend(
        wrap_words(t(app.lang, "help.mouse_note"), key_width.saturating_sub(2))
            .into_iter()
            .map(|chunk| {
                Line::from(Span::styled(
                    format!(" {chunk}"),
                    Style::default().fg(SECONDARY),
                ))
            }),
    );
    let command_width = usize::from(commands_area.width);
    let mut commands: Vec<Line> = vec![heading("help.commands")];
    for (name, _) in COMMANDS {
        let description = t(app.lang, &format!("cmd.{}", name.trim_start_matches('/')));
        commands.extend(entry(name, description, 20, command_width));
    }
    if !two_columns {
        keys.push(Line::raw(""));
        keys.append(&mut commands);
    }
    let lines = keys.len().max(commands.len()).min(usize::from(u16::MAX)) as u16;
    let offset = app.scroll.entry(AreaId::Help).or_default();
    *offset = (*offset).min(lines.saturating_sub(inner.height));
    let offset = *offset;
    frame.render_widget(Paragraph::new(keys).scroll((offset, 0)), keys_area);
    if two_columns {
        frame.render_widget(Paragraph::new(commands).scroll((offset, 0)), commands_area);
    }
}

#[cfg(test)]
mod tests {
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

    use super::wrap_words;
    use crate::{
        app::{dispatch, App, Page},
        commands::run_command,
        keys::handle_key,
        settings::isolated_config_dir,
        ui::{draw as draw_all, Action, AreaId},
    };

    fn render(app: &mut App) -> String {
        let backend = ratatui::backend::TestBackend::new(120, 40);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| draw_all(f, app)).unwrap();
        terminal.backend().to_string()
    }

    fn press(app: &mut App, code: KeyCode) {
        handle_key(app, KeyEvent::new(code, KeyModifiers::NONE));
    }

    #[test]
    fn help_toggles_from_the_action_the_key_and_the_command() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        dispatch(&mut app, Action::Help);
        assert!(app.help_open);
        dispatch(&mut app, Action::Help);
        assert!(!app.help_open);
        press(&mut app, KeyCode::Char('?'));
        assert!(app.help_open);
        // While it is open, other keys do nothing and `Esc` closes it.
        press(&mut app, KeyCode::Char('q'));
        assert!(!app.exit_requested);
        assert!(app.input.is_empty());
        press(&mut app, KeyCode::Esc);
        assert!(!app.help_open);
        assert!(!app.exit_requested);
        app.input = "/help".into();
        run_command(&mut app);
        assert!(app.help_open);
        app.input = "/settings".into();
        run_command(&mut app);
        assert_eq!(app.page, Page::Settings);
        assert_eq!(app.command_menu, None);
    }

    #[test]
    fn help_overlay_lists_keys_and_commands_and_closes_on_click() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.help_open = true;
        let text = render(&mut app);
        for needle in [
            "Справка",
            "Клавиши",
            "Команды",
            "/backend",
            "выбрать ASR-рантайм",
            "/help",
            "Tab/Shift+Tab",
            "Shift",
        ] {
            assert!(text.contains(needle), "{needle}\n{text}");
        }
        // The overlay is on top: a click anywhere closes it.
        assert_eq!(app.hits.hit(0, 0), Some(Action::Help));
        assert_eq!(app.hits.hit(60, 20), Some(Action::Help));
        assert_eq!(app.hits.hit_scroll(60, 20), Some(AreaId::Help));
        assert_eq!(
            app.hits.hit_scroll(0, 0),
            Some(AreaId::Help),
            "the wheel outside the box must not reach the page underneath"
        );
    }

    #[test]
    fn wrap_words_breaks_at_spaces_and_cuts_over_long_words() {
        assert_eq!(wrap_words("один два три", 8), vec!["один два", "три"]);
        assert_eq!(
            wrap_words("a /очень-длинный-путь/x", 6),
            vec!["a", "/очень", "-длинн", "ый-пут", "ь/x"],
            "a long word starts on its own line, then is cut at the width"
        );
        assert_eq!(wrap_words("", 10), vec![""]);
        for line in wrap_words("слово ещё_одно_очень_длинное слово", 7)
        {
            assert!(line.chars().count() <= 7, "{line}");
        }
    }

    #[test]
    fn help_scrolls_when_the_box_is_short_and_clamps() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.help_open = true;
        dispatch(&mut app, Action::Scroll(AreaId::Help, 500));
        let backend = ratatui::backend::TestBackend::new(80, 24);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| draw_all(f, &mut app)).unwrap();
        let offset = app.scroll[&AreaId::Help];
        assert!(offset > 0 && offset < 60, "{offset}");
        let text = terminal.backend().to_string();
        assert!(text.contains("/exit"), "{text}");
        press(&mut app, KeyCode::Up);
        assert_eq!(app.scroll[&AreaId::Help], offset - 1);
    }
}
