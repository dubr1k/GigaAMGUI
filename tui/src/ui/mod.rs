//! Rendering of the terminal UI.

pub(crate) mod llm;
pub(crate) mod menu;
pub(crate) mod processing;

use ratatui::{
    layout::{Constraint, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Paragraph, Tabs},
};
use ratatui_image::StatefulImage;

use crate::{
    app::{llm_can_run, next_step, App, Page},
    i18n::t,
};

pub(crate) const ACCENT: Color = Color::Rgb(92, 155, 255);
pub(crate) const SECONDARY: Color = Color::Rgb(180, 195, 220);

/// Everything the user can do with a click or a key. Keys and mouse clicks both go
/// through `app::dispatch`, so a click can never drift from its keyboard twin.
// `SettingsRow` is registered by the Settings page (Task 7).
#[allow(dead_code)]
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Action {
    Tab(Page),
    SelectFile(usize),
    RemoveFile(usize),
    OpenMenu(&'static str),
    MenuItem(usize),
    Suggestion(usize),
    ToggleMode(&'static str),
    Button(ButtonId),
    ToggleLang,
    Help,
    Scroll(AreaId, i32),
    FocusInput,
    SettingsRow(usize),
    /// Highlights a row of the LLM «Транскрипты» table.
    LlmInput(usize),
    /// Drops a `/llm-file` transcript from that table (session results stay).
    RemoveLlmInput(usize),
    /// Pre-fills the command line with `command ` so the value can be typed.
    EditCommand(&'static str),
}

// `ClearLog` is wired by the Log page (Task 7).
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum ButtonId {
    Start,
    Stop,
    RunLlm,
    CancelLlm,
    ClearQueue,
    ClearLog,
}

// The other areas belong to the pages of Tasks 6–7.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) enum AreaId {
    Queue,
    LlmOutput,
    Log,
    Settings,
    Help,
}

/// The interactive areas of the last drawn frame. `draw` clears it and registers
/// every clickable rect again, so the map always matches what is on screen.
#[derive(Default)]
pub(crate) struct HitMap {
    items: Vec<(Rect, Action)>,
}

impl HitMap {
    pub(crate) fn clear(&mut self) {
        self.items.clear();
    }

    pub(crate) fn add(&mut self, rect: Rect, action: Action) {
        self.items.push((rect, action));
    }

    /// Every registered area, for tests that check what a frame made clickable.
    #[cfg(test)]
    pub(crate) fn items(&self) -> &[(Rect, Action)] {
        &self.items
    }

    /// The action under the cursor; the last registered rect wins because it was
    /// drawn on top of the earlier ones.
    pub(crate) fn hit(&self, x: u16, y: u16) -> Option<Action> {
        self.items
            .iter()
            .rev()
            .find(|(rect, _)| rect.contains((x, y).into()))
            .map(|(_, action)| action.clone())
    }

    /// The scrollable area under the cursor: areas register as `Scroll(area, 0)`.
    pub(crate) fn hit_scroll(&self, x: u16, y: u16) -> Option<AreaId> {
        self.items
            .iter()
            .rev()
            .filter(|(rect, _)| rect.contains((x, y).into()))
            .find_map(|(_, action)| match action {
                Action::Scroll(area, _) => Some(*area),
                _ => None,
            })
    }
}

/// Width of the column on the right of the main area that the pet image occupies.
const PET_COLUMNS: u16 = 18;

pub(crate) fn draw(frame: &mut ratatui::Frame, app: &mut App) {
    app.hits.clear();
    let area = frame.area();
    let rows = menu::MenuRows::of(app);
    // Rows above the input line, capped so that the main area keeps its 11 lines
    // (queue and panel 6, progress 5) and the input line is always visible.
    let menu_height = (rows.len() as u16).min(area.height.saturating_sub(17));
    let [header, tabs, main, hint, input, footer] = Layout::vertical([
        Constraint::Length(1),
        Constraint::Length(1),
        Constraint::Min(11),
        Constraint::Length(1),
        Constraint::Length(menu_height + 2),
        Constraint::Length(1),
    ])
    .areas(area);
    draw_header(frame, header, app);
    draw_tabs(frame, tabs, app);
    let mut page_area = main;
    if app.pet_enabled && main.width > PET_COLUMNS + 20 {
        page_area.width -= PET_COLUMNS;
    }
    match app.page {
        Page::Processing => processing::draw(frame, page_area, app),
        Page::Llm => llm::draw(frame, page_area, app),
        Page::Settings | Page::Log => draw_placeholder(frame, page_area, app),
    }
    draw_pet(frame, main, app);
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled(
                format!(" ▶ {}: ", t(app.lang, "hint.prefix")),
                Style::default().fg(ACCENT).add_modifier(Modifier::BOLD),
            ),
            Span::styled(t(app.lang, next_step(app)), Style::default().fg(ACCENT)),
        ])),
        hint,
    );
    menu::draw(frame, input, app, &rows);
    draw_footer(frame, footer, app);
}

fn draw_header(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let (status_key, colour) = if app.worker_down {
        ("status.worker_down", Color::Red)
    } else if app.llm_running {
        ("status.llm_running", Color::Green)
    } else if app.running {
        ("status.running", Color::Green)
    } else {
        ("status.ready", SECONDARY)
    };
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled(
                " GigaAM",
                Style::default()
                    .fg(Color::White)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!("  ● {}", t(app.lang, status_key)),
                Style::default().fg(colour),
            ),
        ])),
        area,
    );
    let lang = app.lang.code().to_ascii_uppercase();
    let help = format!("? {}", t(app.lang, "header.help"));
    let help_width = help.chars().count() as u16;
    let right = Line::from(vec![
        Span::styled(
            lang.clone(),
            Style::default().fg(ACCENT).add_modifier(Modifier::BOLD),
        ),
        Span::styled("  ", Style::default()),
        Span::styled(help, Style::default().fg(SECONDARY)),
        Span::raw(" "),
    ]);
    let right_width = right.width() as u16;
    if right_width < area.width {
        let x = area.right() - right_width;
        frame.render_widget(Paragraph::new(right), Rect::new(x, area.y, right_width, 1));
        app.hits.add(Rect::new(x, area.y, 2, 1), Action::ToggleLang);
        app.hits
            .add(Rect::new(x + 4, area.y, help_width, 1), Action::Help);
    }
}

fn draw_tabs(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let titles: Vec<String> = Page::ALL
        .iter()
        .enumerate()
        .map(|(index, page)| {
            let key = match page {
                Page::Processing => "tab.processing",
                Page::Llm => "tab.llm",
                Page::Settings => "tab.settings",
                Page::Log => "tab.log",
            };
            format!("F{} {}", index + 1, t(app.lang, key))
        })
        .collect();
    // Padding " " on both sides and a one-cell divider, mirrored below for the hit map.
    let tabs = Tabs::new(titles.iter().map(|title| Line::from(title.as_str())))
        .select(app.page.index())
        .padding(" ", " ")
        .divider("│")
        .style(Style::default().fg(SECONDARY))
        .highlight_style(
            Style::default()
                .fg(Color::White)
                .bg(Color::Rgb(40, 60, 100))
                .add_modifier(Modifier::BOLD),
        );
    frame.render_widget(tabs, area);
    let mut x = area.x;
    for (title, page) in titles.iter().zip(Page::ALL) {
        let width = title.chars().count() as u16 + 2;
        if x + width > area.right() {
            break;
        }
        app.hits
            .add(Rect::new(x, area.y, width, 1), Action::Tab(page));
        x += width + 1;
    }
}

fn draw_placeholder(frame: &mut ratatui::Frame, area: Rect, app: &App) {
    if area.height == 0 {
        return;
    }
    frame.render_widget(
        Paragraph::new(Line::styled(
            t(app.lang, "page.coming"),
            Style::default().fg(SECONDARY),
        ))
        .centered(),
        Rect::new(area.x, area.y + area.height / 2, area.width, 1),
    );
}

fn draw_pet(frame: &mut ratatui::Frame, main: Rect, app: &mut App) {
    if !app.pet_enabled {
        return;
    }
    let Some(image) = app.pet_image.as_mut() else {
        return;
    };
    let pet_area = Rect::new(
        main.right().saturating_sub(PET_COLUMNS),
        main.y.saturating_add(1),
        16.min(main.width.saturating_sub(2)),
        8.min(main.height.saturating_sub(2)),
    );
    if pet_area.width >= 10 && pet_area.height >= 6 {
        frame.render_stateful_widget(StatefulImage::default(), pet_area, image);
    }
}

fn draw_footer(frame: &mut ratatui::Frame, area: Rect, app: &App) {
    let llm_active = llm_can_run(app);
    let dim = Style::default().fg(Color::Gray);
    let key = Style::default().fg(SECONDARY).add_modifier(Modifier::BOLD);
    let item = |k: &str, text: &str| {
        vec![
            Span::styled(k.to_owned(), key),
            Span::styled(format!(" {text} · "), dim),
        ]
    };
    let mut spans = vec![Span::raw(" ")];
    spans.extend(item("s", t(app.lang, "footer.start")));
    spans.push(Span::styled(
        "L",
        Style::default()
            .fg(if llm_active { ACCENT } else { Color::DarkGray })
            .add_modifier(Modifier::BOLD),
    ));
    spans.push(Span::styled(
        format!(" {} · ", t(app.lang, "footer.llm")),
        dim,
    ));
    spans.extend(item("d", t(app.lang, "footer.diar")));
    spans.extend(item("f", t(app.lang, "footer.formats")));
    spans.extend(item("?", t(app.lang, "footer.help")));
    spans.push(Span::styled("q", key));
    spans.push(Span::styled(
        format!(" {}", t(app.lang, "footer.quit")),
        dim,
    ));
    frame.render_widget(Paragraph::new(Line::from(spans)), area);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::Page;

    #[test]
    fn hit_map_returns_the_topmost_action() {
        let mut map = HitMap::default();
        map.add(Rect::new(0, 0, 10, 10), Action::Tab(Page::Llm));
        map.add(Rect::new(2, 2, 3, 3), Action::Help);
        assert_eq!(map.hit(3, 3), Some(Action::Help));
        assert_eq!(map.hit(9, 9), Some(Action::Tab(Page::Llm)));
        assert_eq!(map.hit(20, 20), None);
    }

    #[test]
    fn hit_scroll_finds_the_scrollable_area_under_the_cursor() {
        let mut map = HitMap::default();
        map.add(Rect::new(0, 0, 10, 10), Action::Scroll(AreaId::Queue, 0));
        map.add(Rect::new(2, 2, 3, 3), Action::Help);
        assert_eq!(map.hit_scroll(3, 3), Some(AreaId::Queue));
        assert_eq!(map.hit_scroll(20, 20), None);
        map.clear();
        assert_eq!(map.hit_scroll(3, 3), None);
        assert!(map.items().is_empty());
    }
}
