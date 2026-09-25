//! The block above the input line: either the options of the open selection menu
//! (`/backend`, `/model`, …) or the command suggestions for what is being typed.

use ratatui::{
    layout::Rect,
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Paragraph},
};

use crate::{
    app::App,
    commands::{
        command_menu_options, command_suggestions, BACK_MENU_OPTION, ENTER_MANUALLY_OPTION,
    },
    i18n::t,
    input::InputMode,
    ui::Action,
};

/// What the block shows this frame; computed once so that the layout and the
/// rendering agree on the row count.
pub(crate) struct MenuRows {
    pub(crate) menu: Vec<String>,
    pub(crate) suggestions: Vec<(&'static str, &'static str)>,
}

impl MenuRows {
    pub(crate) fn of(app: &App) -> Self {
        let menu = if app.running() {
            Vec::new()
        } else {
            command_menu_options(app)
        };
        let suggestions = if app.running() || !menu.is_empty() {
            Vec::new()
        } else {
            command_suggestions(&app.input)
        };
        Self { menu, suggestions }
    }

    pub(crate) fn len(&self) -> usize {
        self.menu.len().max(self.suggestions.len())
    }
}

/// Draws the rows and the input line into `area`, whose top row is a border. Every
/// row that is drawn is registered as clickable; when there are more rows than
/// fit, the window slides so that the selected row stays visible.
pub(crate) fn draw(frame: &mut ratatui::Frame, area: Rect, app: &mut App, rows: &MenuRows) {
    if area.height < 2 || area.width == 0 {
        return;
    }
    let p = *app.palette();
    let visible = usize::from(area.height.saturating_sub(2));
    let (count, selected) = if rows.menu.is_empty() {
        (rows.suggestions.len(), app.selected_command)
    } else {
        (rows.menu.len(), app.command_menu_index)
    };
    let selected = selected.min(count.saturating_sub(1));
    let first = selected
        .saturating_sub(visible.saturating_sub(1))
        .min(count.saturating_sub(visible));
    let shown = first..(first + visible).min(count);
    let mut lines: Vec<Line> = shown
        .clone()
        .map(|index| {
            let is_selected = index == selected;
            let line = if rows.menu.is_empty() {
                let (command, _) = rows.suggestions[index];
                Line::from(vec![
                    Span::styled(
                        format!("  {command:<19}"),
                        Style::default().fg(p.accent).add_modifier(if is_selected {
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
                        Style::default().fg(if is_selected { p.text } else { p.dim }),
                    ),
                ])
            } else {
                let option = &rows.menu[index];
                let label = if option == BACK_MENU_OPTION {
                    format!("← {}", t(app.lang, "menu.back"))
                } else if option == ENTER_MANUALLY_OPTION {
                    t(app.lang, "menu.enter_manually").to_owned()
                } else {
                    option.clone()
                };
                Line::from(vec![
                    Span::styled(
                        if is_selected { "  › " } else { "    " },
                        Style::default().fg(p.accent),
                    ),
                    Span::styled(
                        if option == BACK_MENU_OPTION {
                            "0. ".to_owned()
                        } else {
                            format!("{}. ", index + 1)
                        },
                        Style::default().fg(p.disabled),
                    ),
                    Span::styled(
                        label,
                        Style::default()
                            .fg(if is_selected { p.text } else { p.dim })
                            .add_modifier(if is_selected {
                                Modifier::BOLD
                            } else {
                                Modifier::empty()
                            }),
                    ),
                ])
            };
            // The selected row is emphasised like every other list's selection,
            // so it stays visible in a theme without colours.
            if is_selected {
                line.style(p.emphasis())
            } else {
                line
            }
        })
        .collect();
    let label = format!(
        "{}: ",
        t(
            app.lang,
            match app.input.mode {
                InputMode::Paths => "input.files",
                InputMode::Argument(_) => "input.argument",
                _ => "input.command",
            }
        )
    );
    let label_width = Line::from(label.as_str()).width();
    let available = (area.width as usize).saturating_sub(label_width);
    let mut start = app.input.cursor();
    let mut cursor_width = 0;
    for (index, ch) in app.input.text()[..app.input.cursor()].char_indices().rev() {
        let width = Line::from(ch.to_string()).width();
        if cursor_width + width >= available {
            break;
        }
        start = index;
        cursor_width += width;
    }
    let input_y = area.y + 1 + lines.len() as u16;
    lines.push(Line::from(vec![
        Span::styled(
            label,
            Style::default().fg(p.accent).add_modifier(Modifier::BOLD),
        ),
        Span::raw(&app.input.text()[start..]),
    ]));
    frame.render_widget(
        Paragraph::new(lines).block(
            Block::default()
                .borders(Borders::TOP)
                .border_style(Style::default().fg(p.border_muted)),
        ),
        area,
    );
    // The border takes row 0; the row for option `index` is `index - first + 1`.
    for (row, index) in shown.enumerate() {
        let rect = Rect::new(area.x, area.y + 1 + row as u16, area.width, 1);
        app.hits.add(
            rect,
            if rows.menu.is_empty() {
                Action::Suggestion(index)
            } else {
                Action::MenuItem(index)
            },
        );
    }
    let input_row = Rect::new(area.x, input_y, area.width, 1);
    app.hits.add(input_row, Action::FocusInput);
    if available > 0
        && app.input.mode != InputMode::Hidden
        && app.command_menu.is_none()
        && !app.help_open
    {
        frame.set_cursor_position((area.x + (label_width + cursor_width) as u16, input_y));
    }
}
