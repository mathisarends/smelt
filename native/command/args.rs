//! The `smelt check` invocations the native command runs, read as argparse
//! reads them. Anything else, including every error, is left to the Python CLI.
use std::ffi::OsString;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Format {
    Text,
    Concise,
    Json,
    Sarif,
    Github,
}

#[derive(Debug, Default)]
pub struct Args {
    pub config: Option<String>,
    pub paths: Vec<String>,
    pub format: Option<Format>,
    pub fail_on_warning: bool,
    pub select: Option<String>,
    pub ignore: Option<String>,
    pub show_hints: bool,
    pub no_color: bool,
    pub no_debt: bool,
    pub strict_coverage: bool,
    pub show_coverage: bool,
}

impl Args {
    pub fn format(&self) -> Format {
        self.format.unwrap_or(Format::Text)
    }
}

/// Options that take a value, and where it goes.
fn takes_value(name: &str) -> bool {
    matches!(
        name,
        "--config" | "--format" | "--fail-on" | "--select" | "--ignore"
    )
}

fn flag(args: &mut Args, name: &str) -> Option<()> {
    match name {
        "--show-hints" => args.show_hints = true,
        "--no-color" => args.no_color = true,
        "--no-debt" => args.no_debt = true,
        // The native check keeps no cache.
        "--no-cache" => {}
        "--strict-coverage" => args.strict_coverage = true,
        "--show-coverage" => args.show_coverage = true,
        _ => return None,
    }
    Some(())
}

fn option(args: &mut Args, name: &str, value: String) -> Option<()> {
    match name {
        "--config" => args.config = Some(value),
        "--format" => {
            args.format = Some(match value.as_str() {
                "text" => Format::Text,
                "concise" => Format::Concise,
                "json" => Format::Json,
                "sarif" => Format::Sarif,
                "github" => Format::Github,
                _ => return None,
            });
        }
        "--fail-on" => {
            args.fail_on_warning = match value.as_str() {
                "error" => false,
                "warning" => true,
                _ => return None,
            };
        }
        "--select" => args.select = Some(value),
        "--ignore" => args.ignore = Some(value),
        _ => return None,
    }
    Some(())
}

/// The check `arguments` ask for; `None` for any other command or option.
pub fn parse(arguments: &[OsString]) -> Option<Args> {
    let arguments: Vec<&str> = arguments
        .iter()
        .map(|argument| argument.to_str())
        .collect::<Option<_>>()?;
    let mut args = Args::default();
    let mut index = 0;
    // Global options come before the command; only --config is one here.
    loop {
        let argument = *arguments.get(index)?;
        if argument == "check" {
            index += 1;
            break;
        }
        let (name, inline) = split(argument);
        if name != "--config" {
            return None;
        }
        let (value, used) = value(&arguments, index, inline)?;
        args.config = Some(value);
        index += used;
    }
    // The paths are one run of positional arguments, as argparse takes them.
    let mut paths_done = false;
    while let Some(&argument) = arguments.get(index) {
        if !argument.starts_with('-') {
            if paths_done {
                return None;
            }
            let start = index;
            while arguments
                .get(index)
                .is_some_and(|argument| !argument.starts_with('-'))
            {
                index += 1;
            }
            args.paths = arguments[start..index]
                .iter()
                .map(|path| (*path).to_owned())
                .collect();
            paths_done = true;
            continue;
        }
        let (name, inline) = split(argument);
        if takes_value(name) {
            let (value, used) = value(&arguments, index, inline)?;
            option(&mut args, name, value)?;
            index += used;
        } else {
            if inline.is_some() {
                return None;
            }
            flag(&mut args, name)?;
            index += 1;
        }
    }
    Some(args)
}

fn split(argument: &str) -> (&str, Option<&str>) {
    match argument.split_once('=') {
        Some((name, value)) if name.starts_with("--") => (name, Some(value)),
        _ => (argument, None),
    }
}

/// An option's value and how many arguments it used; `None` when argparse
/// would read the next argument as an option.
fn value(arguments: &[&str], index: usize, inline: Option<&str>) -> Option<(String, usize)> {
    if let Some(inline) = inline {
        return Some((inline.to_owned(), 1));
    }
    let value = arguments.get(index + 1)?;
    if value.starts_with('-') {
        return None;
    }
    Some(((*value).to_owned(), 2))
}

#[cfg(test)]
mod tests {
    use super::{Format, parse};
    use std::ffi::OsString;

    fn arguments(text: &str) -> Vec<OsString> {
        text.split_whitespace().map(OsString::from).collect()
    }

    #[test]
    fn supported_checks_parse_and_others_fall_back() {
        let args = parse(&arguments(
            "--config a.yml check src tests --format=json --fail-on warning",
        ))
        .expect("supported");

        assert_eq!(args.config.as_deref(), Some("a.yml"));
        assert_eq!(args.paths, ["src", "tests"]);
        assert_eq!(args.format(), Format::Json);
        assert!(args.fail_on_warning);
        for unsupported in [
            "check --changed",
            "check --staged",
            "check --statistics",
            "check --fix",
            "check src --no-color tests",
            "check --form json",
            "check --select -X",
            "check --",
            "rules",
            "--version",
        ] {
            assert!(parse(&arguments(unsupported)).is_none(), "{unsupported}");
        }
    }
}
