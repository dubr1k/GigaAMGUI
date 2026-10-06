//! Цветовые схемы TUI: палитра из 14 именованных цветов ratatui.
//!
//! Источник — JSON-файлы oh-my-pi в `tui/themes/` (плюс наш `dark-hermes-pink`),
//! вшитые через `theme_catalog::THEMES`; сверх каталога встроены `default`
//! (сегодняшние цвета, ничего не меняется для существующих пользователей) и
//! `mono` (всё `Color::Reset`, акценты — модификаторами).

use ratatui::style::{Color, Modifier, Style};
use serde_json::{Map, Value};

use crate::theme_catalog::THEMES;

/// Имя встроенной темы с сегодняшними цветами.
pub(crate) const DEFAULT_THEME: &str = "default";
/// Имя встроенной монохромной темы: только цвета терминала.
pub(crate) const MONO_THEME: &str = "mono";

/// Цвета, которыми рисуется весь интерфейс.
///
/// Поле ↔ ключ `colors` в JSON oh-my-pi: `accent`, `border`, `borderAccent`,
/// `borderMuted`, `text`, `muted`, `dim`, `success`, `warning`, `error`,
/// `selectedBg`, `statusLineBg`. Двух полей в схеме oh-my-pi нет: `gauge_bg`
/// (фон незаполненной части прогресса) берётся из `selectedBg`, `disabled`
/// (недоступные кнопки, номера строк) — из `dim`: `borderMuted` в 90 темах из
/// 100 почти сливается с фоном (контраст ≈1.0–1.3) и текстом быть не может.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Palette {
    /// Акцент: активная вкладка, подсказка «▶», клавиши, кнопки, заливка прогресса.
    pub accent: Color,
    /// Рамка блока без фокуса.
    pub border: Color,
    /// Рамка блока в фокусе и рамка окна справки.
    pub border_accent: Color,
    /// Едва заметная линия: разделитель над строкой ввода.
    pub border_muted: Color,
    /// Основной текст: заголовки блоков, значения, выбранная строка.
    pub text: Color,
    /// Вторичный текст: подписи, подсказки, заголовки таблиц.
    pub muted: Color,
    /// Самый тусклый текст: описания в подвале, невыбранные пункты меню.
    pub dim: Color,
    /// «Готово», «идёт распознавание», сохранённые файлы.
    pub success: Color,
    /// «Отменено».
    pub warning: Color,
    /// «Ошибка», «воркер недоступен».
    pub error: Color,
    /// Фон выбранной строки и активной вкладки.
    pub selected_bg: Color,
    /// Фон окна справки (в oh-my-pi — фон строки статуса).
    pub status_bg: Color,
    /// Фон незаполненной части индикатора прогресса.
    pub gauge_bg: Color,
    /// Недоступные кнопки, номера строк, неактивная клавиша в подвале.
    pub disabled: Color,
}

impl Palette {
    /// Сегодняшние цвета TUI — константы из `ui/*.rs` до появления тем.
    const DEFAULT: Palette = Palette {
        accent: Color::Rgb(92, 155, 255),
        border: Color::DarkGray,
        border_accent: Color::Rgb(92, 155, 255),
        border_muted: Color::DarkGray,
        text: Color::White,
        muted: Color::Rgb(180, 195, 220),
        dim: Color::Gray,
        success: Color::Green,
        warning: Color::Yellow,
        error: Color::Red,
        selected_bg: Color::Rgb(40, 60, 100),
        status_bg: Color::Rgb(20, 24, 34),
        gauge_bg: Color::Rgb(40, 40, 50),
        disabled: Color::DarkGray,
    };

    /// Ни одного цвета: терминал рисует своими; акценты — модификаторами.
    const MONO: Palette = Palette {
        accent: Color::Reset,
        border: Color::Reset,
        border_accent: Color::Reset,
        border_muted: Color::Reset,
        text: Color::Reset,
        muted: Color::Reset,
        dim: Color::Reset,
        success: Color::Reset,
        warning: Color::Reset,
        error: Color::Reset,
        selected_bg: Color::Reset,
        status_bg: Color::Reset,
        gauge_bg: Color::Reset,
        disabled: Color::Reset,
    };

    /// Заголовок блока: основной цвет, жирный.
    pub(crate) fn title(&self) -> Style {
        Style::new().fg(self.text).add_modifier(Modifier::BOLD)
    }

    /// Рамка блока: акцентная в фокусе. Тема, где акцентная рамка не отличается
    /// от обычной (`mono`), показывает фокус жирной линией.
    pub(crate) fn border_focus(&self, focused: bool) -> Style {
        let style = Style::new().fg(if focused {
            self.border_accent
        } else {
            self.border
        });
        if focused && self.border_accent == self.border {
            style.add_modifier(Modifier::BOLD)
        } else {
            style
        }
    }

    /// Выбранная строка или активная вкладка. Тема без фона выделения (`mono`)
    /// показывает выбор инверсией, иначе его было бы не отличить.
    pub(crate) fn emphasis(&self) -> Style {
        if self.selected_bg == Color::Reset {
            Style::new().add_modifier(Modifier::BOLD | Modifier::REVERSED)
        } else {
            Style::new()
                .fg(self.text)
                .bg(self.selected_bg)
                .add_modifier(Modifier::BOLD)
        }
    }
}

/// Именованная палитра: встроенная (`default`, `mono`) или из каталога.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Theme {
    pub name: &'static str,
    pub palette: Palette,
}

impl Theme {
    /// Тема по точному имени; `None` для неизвестного.
    pub(crate) fn by_name(name: &str) -> Option<Theme> {
        if name == DEFAULT_THEME {
            return Some(Theme::default_theme());
        }
        if name == MONO_THEME {
            return Some(Theme {
                name: MONO_THEME,
                palette: Palette::MONO,
            });
        }
        let index = THEMES
            .binary_search_by(|(candidate, _)| candidate.cmp(&name))
            .ok()?;
        let (name, json) = THEMES[index];
        // Каталог проверяется тестом целиком; здесь битый JSON — неизвестная тема.
        let palette = parse_palette(json).ok()?;
        Some(Theme { name, palette })
    }

    /// `default`, `mono`, затем каталог по алфавиту — порядок меню и автодополнения.
    pub(crate) fn names() -> Vec<&'static str> {
        let mut names = vec![DEFAULT_THEME, MONO_THEME];
        names.extend(THEMES.iter().map(|(name, _)| *name));
        names
    }

    pub(crate) fn default_theme() -> Theme {
        Theme {
            name: DEFAULT_THEME,
            palette: Palette::DEFAULT,
        }
    }
}

/// Убирает `--theme X` / `--theme=X` из аргументов и возвращает тему на этот
/// запуск (в настройки не пишется), как `i18n::strip_lang`. Неизвестное имя —
/// ошибка, а не тихий откат к `default`.
pub(crate) fn strip_theme(args: Vec<String>) -> Result<(Vec<String>, Option<Theme>), String> {
    let mut result = Vec::with_capacity(args.len());
    let mut theme = None;
    let mut expect_value = false;
    let parse = |value: &str| {
        Theme::by_name(value)
            .ok_or_else(|| format!("--theme: unknown theme `{value}` (see /theme for the list)"))
    };
    for arg in args {
        if expect_value {
            expect_value = false;
            theme = Some(parse(&arg)?);
        } else if arg == "--theme" {
            expect_value = true;
        } else if let Some(value) = arg.strip_prefix("--theme=") {
            theme = Some(parse(value)?);
        } else {
            result.push(arg);
        }
    }
    if expect_value {
        return Err("--theme expects a theme name".into());
    }
    Ok((result, theme))
}

/// Палитра из JSON oh-my-pi.
///
/// Значение ключа в `colors` — имя из `vars` (с необязательным `$` впереди),
/// литерал `#rrggbb`/`#rrggbbaa` (альфа отбрасывается) или `""` — цвет
/// терминала (`Color::Reset`). Отсутствующий ключ: `borderAccent` → `accent`,
/// `borderMuted` → `border`, `dim` → `muted`, остальные → `Reset`. Неизвестное
/// имя переменной или кривой литерал — ошибка, а не тихая подмена.
pub(crate) fn parse_palette(json: &str) -> Result<Palette, String> {
    let value: Value = serde_json::from_str(json).map_err(|error| error.to_string())?;
    let object = value.as_object().ok_or("theme is not a JSON object")?;
    let empty = Map::new();
    let vars = object
        .get("vars")
        .and_then(Value::as_object)
        .unwrap_or(&empty);
    let colors = object
        .get("colors")
        .and_then(Value::as_object)
        .ok_or("theme has no `colors` object")?;
    let lookup = |key: &str| -> Result<Option<Color>, String> {
        let Some(raw) = colors.get(key) else {
            return Ok(None);
        };
        let raw = raw
            .as_str()
            .ok_or_else(|| format!("colors.{key} is not a string"))?;
        resolve_colour(vars, raw)
            .map(Some)
            .map_err(|error| format!("colors.{key}: {error}"))
    };
    let accent = lookup("accent")?.unwrap_or(Color::Reset);
    let border = lookup("border")?.unwrap_or(Color::Reset);
    let muted = lookup("muted")?.unwrap_or(Color::Reset);
    let dim = lookup("dim")?.unwrap_or(muted);
    let selected_bg = lookup("selectedBg")?.unwrap_or(Color::Reset);
    Ok(Palette {
        accent,
        border,
        border_accent: lookup("borderAccent")?.unwrap_or(accent),
        border_muted: lookup("borderMuted")?.unwrap_or(border),
        text: lookup("text")?.unwrap_or(Color::Reset),
        muted,
        dim,
        success: lookup("success")?.unwrap_or(Color::Reset),
        warning: lookup("warning")?.unwrap_or(Color::Reset),
        error: lookup("error")?.unwrap_or(Color::Reset),
        selected_bg,
        status_bg: lookup("statusLineBg")?.unwrap_or(Color::Reset),
        gauge_bg: selected_bg,
        disabled: dim,
    })
}

/// Одно значение из `colors`: пустая строка, ссылка на `vars` или hex-литерал.
fn resolve_colour(vars: &Map<String, Value>, raw: &str) -> Result<Color, String> {
    if raw.is_empty() {
        return Ok(Color::Reset);
    }
    if let Some(colour) = hex_colour(raw) {
        return Ok(colour);
    }
    let name = raw.strip_prefix('$').unwrap_or(raw);
    let value = vars
        .get(name)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("unknown var `{raw}`"))?;
    hex_colour(value).ok_or_else(|| format!("var `{name}` = `{value}` is not #rrggbb"))
}

/// `#rrggbb` или `#rrggbbaa` (альфа отбрасывается) в любом регистре.
fn hex_colour(text: &str) -> Option<Color> {
    let digits = text.strip_prefix('#')?;
    if !(digits.len() == 6 || digits.len() == 8) || !digits.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    // Только hex-цифры, так что from_str_radix не может отказать (и не примет `+`).
    let channel = |index: usize| u8::from_str_radix(&digits[index..index + 2], 16).unwrap_or(0);
    Some(Color::Rgb(channel(0), channel(2), channel(4)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::theme_catalog::THEMES;
    use ratatui::style::Color;

    fn palette(name: &str) -> Palette {
        Theme::by_name(name).expect(name).palette
    }

    #[test]
    fn catalogue_has_every_bundled_json_sorted() {
        assert_eq!(THEMES.len(), 100);
        let names: Vec<&str> = THEMES.iter().map(|(name, _)| *name).collect();
        let mut sorted = names.clone();
        sorted.sort_unstable();
        assert_eq!(names, sorted);
        assert!(names.contains(&"dark-monokai"));
        assert!(names.contains(&"dark-hermes-pink"));
    }

    #[test]
    fn every_catalogue_theme_parses_and_names_match() {
        for (name, json) in THEMES {
            let palette = parse_palette(json).unwrap_or_else(|error| panic!("{name}: {error}"));
            let value: serde_json::Value = serde_json::from_str(json).expect(name);
            assert_eq!(value["name"].as_str(), Some(*name), "{name}: JSON name");
            assert_eq!(Theme::by_name(name).expect(name).palette, palette);
        }
    }

    #[test]
    fn monokai_resolves_vars_literals_and_empty() {
        let palette = palette("dark-monokai");
        assert_eq!(palette.accent, Color::Rgb(0xfd, 0x97, 0x1f));
        assert_eq!(palette.text, Color::Reset);
        assert_eq!(palette.status_bg, Color::Rgb(0x1a, 0x1b, 0x17));
        assert_eq!(palette.border_accent, Color::Rgb(0xf9, 0x26, 0x72));
        assert_eq!(palette.selected_bg, Color::Rgb(0x49, 0x48, 0x3e));
        assert_eq!(palette.gauge_bg, palette.selected_bg);
        assert_eq!(palette.disabled, palette.dim, "disabled -> dim");
        assert_ne!(palette.disabled, palette.border_muted);
    }

    #[test]
    fn hermes_pink_theme() {
        let palette = palette("dark-hermes-pink");
        assert_eq!(palette.accent, Color::Rgb(0xf0, 0x55, 0x9a));
        assert_eq!(palette.success, Color::Rgb(0x4a, 0xde, 0x80));
        assert_eq!(
            palette.text,
            Color::Reset,
            "colors.text is \"\" in the JSON"
        );
        assert_eq!(palette.selected_bg, Color::Rgb(0x3a, 0x12, 0x30));
    }

    #[test]
    fn dollar_prefixed_var_references_resolve() {
        assert_eq!(palette("onyx").accent, Color::Rgb(0xd4, 0xa8, 0x53));
    }

    #[test]
    fn hex_alpha_is_ignored_and_case_insensitive() {
        assert_eq!(hex_colour("#717cb425"), Some(Color::Rgb(0x71, 0x7c, 0xb4)));
        assert_eq!(hex_colour("#D5D8DA"), Some(Color::Rgb(0xd5, 0xd8, 0xda)));
        assert_eq!(hex_colour("#fff"), None);
        assert_eq!(hex_colour("d5d8da"), None);
        assert_eq!(hex_colour("#+1+2+3"), None, "from_str_radix accepts `+`");
        assert_eq!(hex_colour("#ffffffff0"), None);
    }

    #[test]
    fn missing_keys_fall_back_explicitly() {
        let palette = parse_palette(r##"{"vars":{"a":"#010203"},"colors":{"accent":"a"}}"##)
            .expect("minimal theme");
        assert_eq!(palette.accent, Color::Rgb(1, 2, 3));
        assert_eq!(
            palette.border_accent, palette.accent,
            "borderAccent -> accent"
        );
        assert_eq!(palette.border, Color::Reset);
        assert_eq!(palette.border_muted, Color::Reset, "borderMuted -> border");
        assert_eq!(palette.dim, Color::Reset, "dim -> muted");
        assert_eq!(palette.gauge_bg, Color::Reset, "gauge_bg -> selectedBg");
        assert_eq!(palette.disabled, Color::Reset, "disabled -> dim -> muted");
        assert_eq!(palette.text, Color::Reset);
        assert_eq!(palette.status_bg, Color::Reset);
    }

    #[test]
    fn unknown_var_and_bad_json_are_errors() {
        assert!(parse_palette(r#"{"vars":{},"colors":{"accent":"nope"}}"#).is_err());
        assert!(parse_palette(r##"{"vars":{},"colors":{"accent":"#12"}}"##).is_err());
        assert!(parse_palette("not json").is_err());
        assert!(parse_palette("[]").is_err());
    }

    #[test]
    fn unknown_name_is_none() {
        assert!(Theme::by_name("nope").is_none());
        assert!(Theme::by_name("Default").is_none());
        assert!(Theme::by_name("").is_none());
    }

    #[test]
    fn names_start_with_default_and_mono_then_sorted_catalogue() {
        let names = Theme::names();
        assert_eq!(names.len(), THEMES.len() + 2);
        assert_eq!(&names[..2], &["default", "mono"]);
        let catalogue: Vec<&str> = THEMES.iter().map(|(name, _)| *name).collect();
        assert_eq!(&names[2..], &catalogue[..]);
        for name in &names {
            assert_eq!(Theme::by_name(name).map(|theme| theme.name), Some(*name));
        }
    }

    #[test]
    fn default_reproduces_todays_constants() {
        let theme = Theme::default_theme();
        assert_eq!(theme.name, "default");
        assert_eq!(theme.palette, palette("default"));
        let palette = theme.palette;
        assert_eq!(palette.accent, Color::Rgb(92, 155, 255));
        assert_eq!(palette.muted, Color::Rgb(180, 195, 220));
        assert_eq!(palette.text, Color::White);
        assert_eq!(palette.dim, Color::Gray);
        assert_eq!(palette.border, Color::DarkGray);
        assert_eq!(palette.border_muted, Color::DarkGray);
        assert_eq!(palette.border_accent, Color::Rgb(92, 155, 255));
        assert_eq!(palette.success, Color::Green);
        assert_eq!(palette.warning, Color::Yellow);
        assert_eq!(palette.error, Color::Red);
        assert_eq!(palette.selected_bg, Color::Rgb(40, 60, 100));
        assert_eq!(palette.status_bg, Color::Rgb(20, 24, 34));
        assert_eq!(palette.gauge_bg, Color::Rgb(40, 40, 50));
        assert_eq!(palette.disabled, Color::DarkGray);
    }

    #[test]
    fn no_colour_literal_outside_theme_rs() {
        fn walk(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
            for entry in std::fs::read_dir(dir).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    walk(&path, out);
                } else if path.extension().is_some_and(|ext| ext == "rs") {
                    out.push(path);
                }
            }
        }
        let src = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut files = Vec::new();
        walk(&src, &mut files);
        assert!(files.len() > 10);
        let offenders: Vec<String> = files
            .iter()
            .filter(|path| path.file_name().is_some_and(|name| name != "theme.rs"))
            .filter(|path| {
                // Tests may spell out expected colours; production code may not.
                let source = std::fs::read_to_string(path).unwrap();
                let production = source.split("#[cfg(test)]").next().unwrap_or("");
                production.contains("Color::")
            })
            .map(|path| path.display().to_string())
            .collect();
        assert!(offenders.is_empty(), "hard-coded colours in {offenders:?}");
    }

    #[test]
    fn emphasis_is_colours_when_the_theme_has_them_and_modifiers_otherwise() {
        use ratatui::style::{Modifier, Style};
        let default = Palette::DEFAULT.emphasis();
        assert_eq!(
            default,
            Style::new()
                .fg(Color::White)
                .bg(Color::Rgb(40, 60, 100))
                .add_modifier(Modifier::BOLD)
        );
        let mono = Palette::MONO.emphasis();
        assert_eq!(mono.fg, None);
        assert_eq!(mono.bg, None);
        assert!(mono
            .add_modifier
            .contains(Modifier::BOLD | Modifier::REVERSED));
    }

    #[test]
    fn strip_theme_removes_both_forms_and_rejects_unknown_names() {
        let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let (rest, theme) = strip_theme(args(&["--theme", "mono", "transcribe", "a.wav"])).unwrap();
        assert_eq!(rest, args(&["transcribe", "a.wav"]));
        assert_eq!(theme.map(|theme| theme.name), Some("mono"));
        let (rest, theme) = strip_theme(args(&["--theme=dark-monokai"])).unwrap();
        assert!(rest.is_empty());
        assert_eq!(theme.map(|theme| theme.name), Some("dark-monokai"));
        let (rest, theme) = strip_theme(args(&["transcribe"])).unwrap();
        assert_eq!(rest, args(&["transcribe"]));
        assert!(theme.is_none());
        let error = strip_theme(args(&["--theme", "nope"])).unwrap_err();
        assert!(
            error.contains("nope") && error.contains("/theme"),
            "{error}"
        );
        assert!(strip_theme(args(&["--theme"])).is_err());
    }

    #[test]
    fn border_focus_is_the_accent_border_or_bold_when_the_theme_has_no_accent() {
        use ratatui::style::Modifier;
        let default = Palette::DEFAULT;
        assert_eq!(default.border_focus(true).fg, Some(default.border_accent));
        assert_eq!(default.border_focus(false).fg, Some(default.border));
        assert!(!default
            .border_focus(true)
            .add_modifier
            .contains(Modifier::BOLD));
        let mono = Palette::MONO;
        assert!(mono
            .border_focus(true)
            .add_modifier
            .contains(Modifier::BOLD));
        assert!(!mono
            .border_focus(false)
            .add_modifier
            .contains(Modifier::BOLD));
    }

    #[test]
    fn catalogue_names_are_ascii() {
        // `complete_theme_name` slices names by byte offsets, so a non-ASCII name
        // would need a char-aware common prefix.
        for name in Theme::names() {
            assert!(name.is_ascii(), "{name}");
        }
    }

    #[test]
    fn mono_is_all_reset() {
        let palette = palette("mono");
        for colour in [
            palette.accent,
            palette.border,
            palette.border_accent,
            palette.border_muted,
            palette.text,
            palette.muted,
            palette.dim,
            palette.success,
            palette.warning,
            palette.error,
            palette.selected_bg,
            palette.status_bg,
            palette.gauge_bg,
            palette.disabled,
        ] {
            assert_eq!(colour, Color::Reset);
        }
    }
}
