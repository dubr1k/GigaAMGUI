//! The command line: argument handling and headless mode.

mod args;
mod headless;

pub(crate) use args::{apply_data_dir_argument, strip_data_dir, utf8_args, HEADLESS_USAGE};
pub(crate) use headless::run_headless;

#[cfg(test)]
mod tests;
