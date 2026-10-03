//! Slash commands, their menus and the path helpers behind the input line.

mod menu;
mod paths;
mod registry;
mod run;

pub(crate) use menu::*;
pub(crate) use paths::*;
pub(crate) use registry::*;
pub(crate) use run::*;

#[cfg(test)]
mod tests;
