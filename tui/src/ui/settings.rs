//! The «Настройки» page: one list of every setting with its current value.
//!
//! `Enter` or a click on a row performs its action: a choice menu for the
//! enumerable settings, a pre-filled command line for the typed ones and a
//! direct flip for the booleans. Values are read from [`App`] on every frame,
//! so a change made through a command shows at once.

use ratatui::{
    layout::Rect,
    style::Style,
    text::{Line, Span},
    widgets::{Block, List, ListItem, ListState},
};

use crate::{
    app::App,
    i18n::t,
    ui::{Action, AreaId},
    worker::provider_prefix,
};

/// One row of the list: the label key, the value to show and what `Enter` does.
pub(crate) struct SettingRow {
    pub(crate) key: &'static str,
    pub(crate) value: String,
    pub(crate) action: Action,
}

fn row(key: &'static str, value: impl Into<String>, action: Action) -> SettingRow {
    SettingRow {
        key,
        value: value.into(),
        action,
    }
}

fn on_off(app: &App, on: bool) -> &'static str {
    t(app.lang, if on { "value.on" } else { "value.off" })
}

/// A per-provider value (`/llm-path`, `/llm-args`, …) or its placeholder.
fn provider_value<'a>(
    app: &'a App,
    map: &'a std::collections::HashMap<String, String>,
    placeholder: &'static str,
) -> &'a str {
    map.get(provider_prefix(&app.llm_provider))
        .map_or_else(|| t(app.lang, placeholder), String::as_str)
}

/// The rows in the order of the spec: interface, engine, subtitles, LLM.
pub(crate) fn rows(app: &App) -> Vec<SettingRow> {
    let text_or = |value: &str, placeholder: &'static str| {
        if value.is_empty() {
            t(app.lang, placeholder).to_owned()
        } else {
            value.to_owned()
        }
    };
    vec![
        row(
            "settings.language",
            t(app.lang, "lang.name"),
            Action::OpenMenu("/lang"),
        ),
        row(
            "settings.mouse",
            on_off(app, app.mouse_enabled),
            Action::ToggleSetting("mouse"),
        ),
        row("settings.theme", app.theme.name, Action::OpenMenu("/theme")),
        row(
            "settings.pets",
            on_off(app, app.pet_enabled),
            Action::ToggleSetting("pets"),
        ),
        row(
            "settings.backend",
            app.backend.as_str(),
            Action::OpenMenu("/backend"),
        ),
        row(
            "settings.onnx_provider",
            app.onnx_provider.as_str(),
            Action::OpenMenu("/onnx-provider"),
        ),
        row(
            "settings.model",
            app.model.as_str(),
            Action::OpenMenu("/model"),
        ),
        row(
            "settings.diarization_backend",
            app.diarization_backend.as_str(),
            Action::OpenMenu("/diarization-backend"),
        ),
        row(
            "settings.audio",
            app.audio_preprocessing_mode.as_str(),
            Action::OpenMenu("/audio-mode"),
        ),
        row(
            "settings.output",
            app.output_dir
                .clone()
                .unwrap_or_else(|| t(app.lang, "value.next_to_file").to_owned()),
            Action::EditCommand("/output"),
        ),
        row(
            "settings.subtitle_split",
            on_off(app, app.subtitle_sentence_split),
            Action::ToggleSetting("subtitle_split"),
        ),
        row(
            "settings.subtitle_lines",
            app.subtitle_max_lines.to_string(),
            Action::EditCommand("/subtitle-lines"),
        ),
        row(
            "settings.subtitle_width",
            app.subtitle_max_width.to_string(),
            Action::EditCommand("/subtitle-width"),
        ),
        row(
            "settings.llm_provider",
            app.llm_provider.as_str(),
            Action::OpenMenu("/settings-provider"),
        ),
        row(
            "settings.llm_model",
            text_or(&app.llm_model, "value.default"),
            Action::OpenMenu("/settings-model"),
        ),
        row(
            "settings.llm_api_url",
            text_or(&app.llm_api_url, "value.not_set"),
            Action::EditCommand("/llm-api-url"),
        ),
        row(
            "settings.llm_api_key",
            if app.llm_api_key.is_empty() {
                t(app.lang, "value.not_set")
            } else {
                "••••"
            },
            Action::EditCommand("/llm-api-key"),
        ),
        row(
            "settings.llm_temperature",
            app.llm_temperature.to_string(),
            Action::EditCommand("/llm-temperature"),
        ),
        row(
            "settings.llm_path",
            provider_value(app, &app.llm_tool_paths, "value.auto"),
            Action::EditCommand("/llm-path"),
        ),
        row(
            "settings.llm_provider_name",
            provider_value(app, &app.llm_internal_providers, "value.default"),
            Action::EditCommand("/llm-provider-name"),
        ),
        row(
            "settings.llm_args",
            provider_value(app, &app.llm_extra_args, "value.none"),
            Action::EditCommand("/llm-args"),
        ),
        row(
            "settings.llm_tools",
            on_off(app, app.llm_allow_tools),
            Action::ToggleSetting("llm_tools"),
        ),
    ]
}

pub(crate) fn draw(frame: &mut ratatui::Frame, area: Rect, app: &mut App) {
    let p = *app.palette();
    let block = Block::bordered()
        .title(Span::styled(
            format!(" {} ", t(app.lang, "settings.title")),
            p.title(),
        ))
        .title_bottom(
            Line::from(Span::styled(
                format!(" {} ", t(app.lang, "settings.hint")),
                Style::default().fg(p.muted),
            ))
            .right_aligned(),
        )
        .border_style(Style::default().fg(p.border));
    let inner = block.inner(area);
    frame.render_widget(block, area);
    app.hits.add(area, Action::Scroll(AreaId::Settings, 0));
    if inner.height == 0 || inner.width == 0 {
        return;
    }
    let rows = rows(app);
    let label_width = rows
        .iter()
        .map(|row| t(app.lang, row.key).chars().count())
        .max()
        .unwrap_or(0)
        + 2;
    let items: Vec<ListItem> = rows
        .iter()
        .map(|row| {
            ListItem::new(Line::from(vec![
                Span::styled(
                    format!(" {:<label_width$}", t(app.lang, row.key)),
                    Style::default().fg(p.muted),
                ),
                Span::styled(row.value.clone(), Style::default().fg(p.text)),
            ]))
        })
        .collect();
    app.settings_cursor = app.settings_cursor.min(rows.len().saturating_sub(1));
    let visible = usize::from(inner.height);
    let max_offset = rows.len().saturating_sub(visible);
    let offset = usize::from(*app.scroll.entry(AreaId::Settings).or_default()).min(max_offset);
    let mut state = ListState::default()
        .with_offset(offset)
        .with_selected(Some(app.settings_cursor));
    let list = List::new(items).highlight_style(p.emphasis());
    frame.render_stateful_widget(list, inner, &mut state);
    // The wheel moves the cursor (`dispatch`), and the list slides to keep it
    // visible; the window it chose is what the next frame starts from.
    let offset = state.offset();
    app.scroll
        .insert(AreaId::Settings, offset.min(usize::from(u16::MAX)) as u16);
    for row in 0..visible.min(rows.len().saturating_sub(offset)) {
        app.hits.add(
            Rect::new(inner.x, inner.y + row as u16, inner.width, 1),
            Action::SettingsRow(offset + row),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        app::{dispatch, Page},
        i18n::Lang,
        settings::isolated_config_dir,
        ui::draw as draw_all,
    };

    fn render(app: &mut App) -> String {
        let backend = ratatui::backend::TestBackend::new(100, 40);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| draw_all(f, app)).unwrap();
        terminal.backend().to_string()
    }

    #[test]
    fn rows_show_the_language_and_hide_the_api_key() {
        let mut app = App::default();
        let list = rows(&app);
        let language = list
            .iter()
            .find(|row| row.key == "settings.language")
            .unwrap();
        assert_eq!(language.value, "Русский");
        assert_eq!(language.action, Action::OpenMenu("/lang"));
        let key = list
            .iter()
            .find(|row| row.key == "settings.llm_api_key")
            .unwrap();
        assert_eq!(key.value, "не задан");
        app.llm_api_key = "sk-secret".into();
        app.lang = Lang::En;
        let list = rows(&app);
        let key = list
            .iter()
            .find(|row| row.key == "settings.llm_api_key")
            .unwrap();
        assert_eq!(key.value, "••••");
        assert_eq!(key.action, Action::EditCommand("/llm-api-key"));
        assert_eq!(list[0].value, "English");
    }

    #[test]
    fn theme_row_follows_the_mouse_row_and_opens_the_theme_menu() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.theme = crate::theme::Theme::by_name("dark-monokai").unwrap();
        let list = rows(&app);
        let mouse = list
            .iter()
            .position(|row| row.key == "settings.mouse")
            .unwrap();
        assert_eq!(list[mouse + 1].key, "settings.theme");
        assert_eq!(list[mouse + 1].value, "dark-monokai");
        dispatch(&mut app, Action::SettingsRow(mouse + 1));
        assert_eq!(app.command_menu.as_deref(), Some("/theme"));
        assert_eq!(app.settings_cursor, mouse + 1);
        assert_eq!(
            crate::commands::command_menu_options(&app)[app.command_menu_index],
            "dark-monokai"
        );
        assert!(render(&mut app).contains("dark-monokai"));
    }

    #[test]
    fn settings_row_selects_and_performs_the_row_action() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        let mouse = rows(&app)
            .iter()
            .position(|row| row.key == "settings.mouse")
            .unwrap();
        assert!(app.mouse_enabled);
        dispatch(&mut app, Action::SettingsRow(mouse));
        assert!(!app.mouse_enabled);
        assert_eq!(app.settings_cursor, mouse);
        dispatch(&mut app, Action::ToggleSetting("mouse"));
        assert!(app.mouse_enabled);

        let backend = rows(&app)
            .iter()
            .position(|row| row.key == "settings.backend")
            .unwrap();
        dispatch(&mut app, Action::SettingsRow(backend));
        assert_eq!(app.command_menu.as_deref(), Some("/backend"));
        app.command_menu = None;

        let url = rows(&app)
            .iter()
            .position(|row| row.key == "settings.llm_api_url")
            .unwrap();
        dispatch(&mut app, Action::SettingsRow(url));
        assert_eq!(app.input, "/llm-api-url ");
        let output = rows(&app)
            .iter()
            .position(|row| row.key == "settings.output")
            .unwrap();
        dispatch(&mut app, Action::SettingsRow(output));
        assert_eq!(app.input, "/output ");
        app.output_dir = Some("/tmp/out".into());
        assert_eq!(rows(&app)[output].value, "/tmp/out");

        dispatch(&mut app, Action::ToggleSetting("subtitle_split"));
        assert!(!app.subtitle_sentence_split);
        dispatch(&mut app, Action::ToggleSetting("llm_tools"));
        assert!(app.llm_allow_tools);
        dispatch(&mut app, Action::SettingsRow(999));
        assert_eq!(app.settings_cursor, output, "an unknown row is ignored");
    }

    #[test]
    fn language_menu_switches_the_language() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        dispatch(&mut app, Action::OpenMenu("/lang"));
        assert_eq!(app.command_menu.as_deref(), Some("/lang"));
        assert_eq!(
            app.command_menu_index, 0,
            "the current language is selected"
        );
        dispatch(&mut app, Action::MenuItem(1));
        assert_eq!(app.lang, Lang::En);
        assert_eq!(app.command_menu, None);
        dispatch(&mut app, Action::OpenMenu("/lang"));
        assert_eq!(app.command_menu_index, 1);
    }

    #[test]
    fn settings_page_renders_russian_rows_and_registers_them() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.page = Page::Settings;
        app.llm_api_key = "secret".into();
        let text = render(&mut app);
        for needle in [
            "Настройки",
            "Язык",
            "Русский",
            "Мышь",
            "Движок диаризации",
            "Папка результатов",
            "рядом с файлом",
            "Субтитры: разбиение",
            "LLM: ключ API",
            "••••",
            "инструменты агента",
        ] {
            assert!(text.contains(needle), "{needle}\n{text}");
        }
        assert!(!text.contains("secret"));
        let count = rows(&app).len();
        let has = |action: Action| app.hits.items().iter().any(|(_, a)| *a == action);
        assert!(has(Action::SettingsRow(0)));
        assert!(has(Action::SettingsRow(count - 1)));
        assert!(has(Action::Scroll(AreaId::Settings, 0)));
    }

    #[test]
    fn settings_list_scrolls_with_the_wheel_and_clamps() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.page = Page::Settings;
        let last = rows(&app).len() - 1;
        // A wheel tick is `Scroll(_, ±3)` (main.rs): it moves one row, never three.
        dispatch(&mut app, Action::Scroll(AreaId::Settings, 3));
        assert_eq!(app.settings_cursor, 1, "one wheel tick is one row");
        for _ in 0..last + 5 {
            dispatch(&mut app, Action::Scroll(AreaId::Settings, 3));
        }
        assert_eq!(
            app.settings_cursor, last,
            "the cursor clamps at the last row"
        );
        let backend = ratatui::backend::TestBackend::new(60, 20);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| draw_all(f, &mut app)).unwrap();
        let offset = app.scroll[&AreaId::Settings];
        assert!(offset > 0 && usize::from(offset) < last, "{offset}");
        dispatch(&mut app, Action::Scroll(AreaId::Settings, -3));
        assert_eq!(app.settings_cursor, last - 1);
        for _ in 0..last + 5 {
            dispatch(&mut app, Action::Scroll(AreaId::Settings, -3));
        }
        assert_eq!(app.settings_cursor, 0);
        terminal.draw(|f| draw_all(f, &mut app)).unwrap();
        assert_eq!(app.scroll[&AreaId::Settings], 0);
    }
}
