//! The `smelt` command: runs the Python CLI, which checks in the native core.
use std::env;
use std::ffi::OsString;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{self, Command};

mod checker;
mod git;

fn trace(message: impl std::fmt::Display) {
    if env::var_os("SMELT_TRACE").is_some() {
        eprintln!("smelt trace: {message}");
    }
}

fn python() -> io::Result<PathBuf> {
    if let Some(explicit) = env::var_os("SMELT_PYTHON") {
        return Ok(PathBuf::from(explicit));
    }
    let executable = env::current_exe()?;
    let directory = executable
        .parent()
        .ok_or_else(|| io::Error::other("missing executable directory"))?;
    let names = if cfg!(windows) {
        ["python.exe", "python3.exe"]
    } else {
        ["python", "python3"]
    };
    for name in names {
        let candidate = directory.join(name);
        if candidate.is_file() {
            return Ok(candidate);
        }
    }
    if let Some(path) = env::var_os("PATH") {
        for directory in env::split_paths(&path) {
            for name in names {
                let candidate = directory.join(name);
                if candidate.is_file() {
                    return Ok(candidate);
                }
            }
        }
    }
    Err(io::Error::other(
        "cannot find the Python environment beside smelt; set SMELT_PYTHON",
    ))
}

fn direct(python: &Path, arguments: &[OsString]) -> io::Result<i32> {
    let mut command = Command::new(python);
    // Import the installed analyzer rather
    // than a same-named package in the directory being checked.
    command.args(["-P", "-m", "smelt"]).args(arguments);
    command.env("SMELT_NATIVE_CLIENT", env::current_exe()?);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        Err(command.exec())
    }
    #[cfg(not(unix))]
    {
        let status = command.status()?;
        Ok(status.code().unwrap_or(2))
    }
}

fn run() -> io::Result<i32> {
    let arguments: Vec<OsString> = env::args_os().skip(1).collect();
    if arguments == [OsString::from("--internal-git")] {
        return git::serve();
    }
    if arguments == [OsString::from("--internal-check")] {
        return Ok(checker::serve_check());
    }
    if arguments == [OsString::from("--internal-files")] {
        return Ok(checker::serve_files());
    }
    if let [flag, config] = arguments.as_slice()
        && flag == "--internal-config"
    {
        return Ok(checker::serve_config(Path::new(config)));
    }
    if arguments == [OsString::from("--version")] {
        println!("smelt {}", env!("CARGO_PKG_VERSION"));
        return Ok(0);
    }
    let python = python()?;
    trace(format!("interpreter: {}", python.display()));
    direct(&python, &arguments)
}

fn main() {
    let code = match run() {
        Ok(code) => code,
        Err(error) => {
            eprintln!("smelt: {error}");
            2
        }
    };
    trace(format!("exiting with {code}"));
    process::exit(code);
}
