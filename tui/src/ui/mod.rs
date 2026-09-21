//! Rendering of the terminal UI.

pub(crate) mod help;
pub(crate) mod llm;
pub(crate) mod log;
pub(crate) mod menu;
pub(crate) mod processing;
pub(crate) mod settings;

use ratatui::{
    layout::{Constraint, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Paragraph, Tabs},
};
use ratatui_image::StatefulImage;

use crate::{
    app::{llm_can_run, next_step, App, Page},
    i18n::t,
};

/// Everything the user can do with a click or a key. Keys and mouse clicks both go
/// through `app::dispatch`, so a click can never drift from its keyboard twin.
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
    /// Highlights a row of the Settings page and performs its action.
    SettingsRow(usize),
    /// Flips a boolean setting: `mouse`, `pets`, `subtitle_split` or `llm_tools`.
    ToggleSetting(&'static str),
    /// Highlights a row of the LLM «Транскрипты» table.
    LlmInput(usize),
    /// Drops a `/llm-file` transcript from that table (session results stay).
    RemoveLlmInput(usize),
    /// Pre-fills the command line with `command ` so the value can be typed.
    EditCommand(&'static str),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum ButtonId {
    Start,
    Stop,
    RunLlm,
    CancelLlm,
    ClearQueue,
    ClearLog,
}

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
    let p = *app.palette();
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
        Page::Settings => settings::draw(frame, page_area, app),
        Page::Log => log::draw(frame, page_area, app),
    }
    draw_pet(frame, main, app);
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled(
                format!(" ▶ {}: ", t(app.lang, "hint.prefix")),
                Style::default().fg(p.accent).add_modifier(Modifier::BOLD),
            ),
            Span::styled(t(app.lang, next_step(app)), Style::default().fg(p.accent)),
        ])),
        hint,
    );
    menu::draw(frame, input, app, &rows);
    draw_footer(frame, footer, app);
    // Last, so that it covers the page and its hit areas sit on top of theirs.
    if app.help_open {
        help::draw(frame, area, app);
    }
}

fn draw_header(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let p = *app.palette();
    let (status_key, colour) = if app.worker_down {
        ("status.worker_down", p.error)
    } else if app.llm_running {
        ("status.llm_running", p.success)
    } else if app.running {
        ("status.running", p.success)
    } else {
        ("status.ready", p.muted)
    };
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled(" GigaAM", p.title()),
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
            Style::default().fg(p.accent).add_modifier(Modifier::BOLD),
        ),
        Span::styled("  ", Style::default()),
        Span::styled(help, Style::default().fg(p.muted)),
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
    let p = *app.palette();
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
        .style(Style::default().fg(p.muted))
        .highlight_style(p.emphasis());
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
    let p = *app.palette();
    let llm_active = llm_can_run(app);
    let dim = Style::default().fg(p.dim);
    let key = Style::default().fg(p.muted).add_modifier(Modifier::BOLD);
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
            .fg(if llm_active { p.accent } else { p.disabled })
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
    use crate::{app::Page, theme::Theme};
    use ratatui::{buffer::Buffer, style::Color};

    fn render(theme: &str) -> Buffer {
        let mut app = App::default();
        app.theme = Theme::by_name(theme).expect(theme);
        app.files = vec!["/a/one.wav".into()];
        let backend = ratatui::backend::TestBackend::new(100, 40);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        terminal.backend().buffer().clone()
    }

    #[test]
    fn active_tab_cell_follows_the_theme() {
        // Row 1 is the tab bar, column 1 the "F" of the active «F1 …» tab.
        let default = render("default")[(1, 1)].clone();
        let monokai = render("dark-monokai")[(1, 1)].clone();
        assert_eq!(default.symbol(), "F");
        assert_eq!(default.fg, Color::White);
        assert_eq!(default.bg, Color::Rgb(40, 60, 100));
        assert!(default.modifier.contains(Modifier::BOLD));
        assert_eq!(monokai.symbol(), "F");
        assert_eq!(monokai.bg, Color::Rgb(0x49, 0x48, 0x3e));
        assert_ne!(default.bg, monokai.bg);
        // An inactive tab takes the theme's muted colour.
        let inactive = render("dark-monokai")[(20, 1)].clone();
        assert_eq!(inactive.fg, Color::Rgb(0x99, 0x99, 0x99));
    }

    #[test]
    fn mono_uses_only_terminal_colours_and_bold_for_the_active_tab() {
        let buffer = render("mono");
        for cell in buffer.content() {
            assert_eq!(cell.fg, Color::Reset, "{cell:?}");
            assert_eq!(cell.bg, Color::Reset, "{cell:?}");
        }
        let active = &buffer[(1, 1)];
        assert_eq!(active.symbol(), "F");
        assert!(active.modifier.contains(Modifier::BOLD), "{active:?}");
        assert!(active.modifier.contains(Modifier::REVERSED), "{active:?}");
        assert!(!buffer[(20, 1)].modifier.contains(Modifier::BOLD));
    }

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
