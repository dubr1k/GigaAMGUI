//! The LLM providers the TUI knows before the worker answers `llm_tools`.
//!
//! The worker's `cli_tools.PROVIDERS` is the source of truth for what is
//! installed; this table mirrors its ids so that settings keys, the worker
//! payload and the menus agree. It used to exist four times: the fallback name
//! list and the prefix match in worker.rs, the bare binary names there again
//! and in settings.rs, and the model suggestions in commands.rs.

pub(crate) struct Provider {
    /// The name shown in menus and stored as `llm_provider`.
    pub name: &'static str,
    /// The settings-key prefix (`llm_<prefix>_path`), the `cli_tools` id.
    pub prefix: &'static str,
    /// The CLI's bare binary name; a configured path equal to it is no override.
    /// `None` for the API and for "Other" (no default binary).
    pub binary: Option<&'static str>,
    /// Model suggestions for the `/settings-model` menu.
    pub models: &'static [&'static str],
    /// Pi and oh-my-pi route to an internal provider (`/llm-provider-name`).
    pub internal_provider: bool,
}

pub(crate) const PROVIDERS: [Provider; 7] = [
    Provider {
        name: "API",
        prefix: "api",
        binary: None,
        models: &["gpt-4.1-mini", "gpt-4.1", "gpt-5-mini", "gpt-5"],
        internal_provider: false,
    },
    Provider {
        name: "Claude Code",
        prefix: "claude",
        binary: Some("claude"),
        models: &["default", "sonnet", "opus", "haiku"],
        internal_provider: false,
    },
    // Codex with a ChatGPT account rejects explicit `-m` values such as
    // gpt-5-codex. Let the installed Codex client choose its supported model.
    Provider {
        name: "Codex",
        prefix: "codex",
        binary: Some("codex"),
        models: &["default"],
        internal_provider: false,
    },
    Provider {
        name: "OpenCode",
        prefix: "opencode",
        binary: Some("opencode"),
        models: &["default"],
        internal_provider: false,
    },
    Provider {
        name: "Pi",
        prefix: "pi",
        binary: Some("pi"),
        models: &["default"],
        internal_provider: true,
    },
    Provider {
        name: "oh-my-pi",
        prefix: "omp",
        binary: Some("omp"),
        models: &["default"],
        internal_provider: true,
    },
    Provider {
        name: "Other",
        prefix: "other",
        binary: None,
        models: &["default"],
        internal_provider: false,
    },
];

/// The provider by its menu name; an unknown name is treated as the API, as the
/// worker does.
pub(crate) fn provider(name: &str) -> &'static Provider {
    PROVIDERS
        .iter()
        .find(|provider| provider.name == name)
        .unwrap_or(&PROVIDERS[0])
}

/// Settings-key prefix for a provider — matches `cli_tools.PROVIDERS`.
pub(crate) fn provider_prefix(name: &str) -> &'static str {
    provider(name).prefix
}

/// The CLI providers with a default binary, in registry order.
pub(crate) fn cli_providers() -> impl Iterator<Item = &'static Provider> {
    PROVIDERS
        .iter()
        .filter(|provider| provider.binary.is_some())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_prefixes_and_binaries_match_the_worker_registry() {
        assert_eq!(
            PROVIDERS.map(|provider| provider.name),
            [
                "API",
                "Claude Code",
                "Codex",
                "OpenCode",
                "Pi",
                "oh-my-pi",
                "Other"
            ]
        );
        assert_eq!(provider_prefix("oh-my-pi"), "omp");
        assert_eq!(provider_prefix("unknown"), "api");
        assert_eq!(
            cli_providers()
                .map(|provider| (provider.prefix, provider.binary.unwrap()))
                .collect::<Vec<_>>(),
            [
                ("claude", "claude"),
                ("codex", "codex"),
                ("opencode", "opencode"),
                ("pi", "pi"),
                ("omp", "omp")
            ]
        );
        assert!(provider("Pi").internal_provider && !provider("Codex").internal_provider);
    }
}
