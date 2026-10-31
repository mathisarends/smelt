//! A small client: checks retain Python imports, all other commands run directly.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::env;
use std::ffi::OsString;
use std::fs;
use std::io::{self, BufRead, BufReader, IsTerminal, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{self, Command};
use std::thread;
use std::time::{Duration, Instant};

mod git;
mod worker;

const PROTOCOL: u32 = 1;
const LIMIT: u64 = 64 * 1024 * 1024;

fn trace(message: impl std::fmt::Display) {
    if env::var_os("SMELT_TRACE").is_some() {
        eprintln!("smelt trace: {message}");
    }
}

#[derive(Deserialize)]
struct Endpoint {
    port: u16,
    token: String,
    pid: u32,
    protocol: u32,
}

#[derive(Serialize)]
struct Request<'a> {
    token: &'a str,
    cwd: &'a str,
    arguments: &'a [String],
    tty: bool,
}

#[derive(Deserialize)]
struct Response {
    code: i32,
    out: String,
    err: String,
    pid: u32,
    #[serde(default)]
    restart: bool,
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
    // Like the former console entry point, import the installed analyzer rather
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

fn endpoint(python: &Path, cwd: &Path) -> io::Result<PathBuf> {
    let environment: BTreeMap<_, _> = env::vars_os().collect();
    let mut hasher = Sha256::new();
    // OS strings are hashed losslessly; changes in any environment variable get
    // their own worker, rather than retaining stale interpreter/Git settings.
    for value in [python.as_os_str(), cwd.as_os_str()] {
        hasher.update(value.as_encoded_bytes());
        hasher.update([0]);
    }
    for (name, value) in environment {
        hasher.update(name.as_encoded_bytes());
        hasher.update([0]);
        hasher.update(value.as_encoded_bytes());
        hasher.update([0]);
    }
    hasher.update(PROTOCOL.to_le_bytes());
    let root = if cfg!(windows) {
        PathBuf::from(
            env::var_os("LOCALAPPDATA").ok_or_else(|| io::Error::other("missing LOCALAPPDATA"))?,
        )
    } else if let Some(cache) = env::var_os("XDG_CACHE_HOME") {
        PathBuf::from(cache)
    } else {
        PathBuf::from(env::var_os("HOME").ok_or_else(|| io::Error::other("missing HOME"))?)
            .join(".cache")
    };
    let directory = root.join("smelt").join("sessions-v1");
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(&directory)?;
    }
    #[cfg(not(unix))]
    fs::create_dir_all(&directory)?;
    Ok(directory.join(format!("{:x}.json", hasher.finalize())))
}

fn request(path: &Path, cwd: &str, arguments: &[String]) -> io::Result<Response> {
    trace(format!("connecting via {}", path.display()));
    let metadata: Endpoint = serde_json::from_slice(&fs::read(path)?)?;
    if metadata.protocol != PROTOCOL {
        return Err(io::Error::other("different worker protocol"));
    }
    let address = SocketAddr::from(([127, 0, 0, 1], metadata.port));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(100))?;
    stream.set_read_timeout(Some(Duration::from_secs(120)))?;
    stream.set_write_timeout(Some(Duration::from_secs(10)))?;
    let payload = Request {
        token: &metadata.token,
        cwd,
        arguments,
        tty: io::stdout().is_terminal(),
    };
    // A streaming serializer issues many tiny TCP writes. With Nagle enabled,
    // the final newline can wait for a delayed ACK while the worker waits for
    // that newline. Send the complete framed request in one write instead.
    let mut encoded = serde_json::to_vec(&payload)?;
    encoded.push(b'\n');
    stream.write_all(&encoded)?;
    trace("request sent");
    let mut line = Vec::new();
    BufReader::new(stream)
        .take(LIMIT)
        .read_until(b'\n', &mut line)?;
    let response: Response = serde_json::from_slice(&line)?;
    trace(format!("received {} bytes", line.len()));
    if response.pid != metadata.pid || !(0..=2).contains(&response.code) {
        return Err(io::Error::other("unexpected worker response"));
    }
    Ok(response)
}

fn check(python: &Path, arguments: &[String]) -> io::Result<Response> {
    let cwd = env::current_dir()?;
    let name = cwd
        .to_str()
        .ok_or_else(|| io::Error::other("non-UTF-8 cwd"))?;
    let path = endpoint(python, &cwd)?;
    if let Ok(response) = request(&path, name, arguments)
        && !response.restart
    {
        return Ok(response);
    }
    // An atomic directory arbitrates concurrent first clients. Only its owner
    // starts a worker. A crashed starter only delays other clients briefly;
    // they fall back to the normal foreground check.
    let lock = path.with_extension("starting");
    if fs::metadata(&lock)
        .and_then(|metadata| metadata.modified())
        .ok()
        .and_then(|modified| modified.elapsed().ok())
        .is_some_and(|age| age > Duration::from_secs(10))
    {
        let _ = fs::remove_dir(&lock);
    }
    let owner = fs::create_dir(&lock).is_ok();
    if owner {
        let _ = fs::remove_file(&path);
        match worker::spawn(python, &path, &cwd) {
            Ok(mut child) => {
                trace(format!("started worker {}", child.id()));
                let deadline = Instant::now() + Duration::from_secs(5);
                while !path.is_file() && Instant::now() < deadline {
                    if child.try_wait()?.is_some() {
                        break;
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                if !path.is_file() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                trace(format!("endpoint ready: {}", path.is_file()));
            }
            Err(error) => {
                let _ = fs::remove_dir(&lock);
                return Err(error);
            }
        }
        let _ = fs::remove_dir(&lock);
    } else {
        let deadline = Instant::now() + Duration::from_millis(150);
        while !path.is_file() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
    }
    request(&path, name, arguments)
}

fn run() -> io::Result<i32> {
    let arguments: Vec<OsString> = env::args_os().skip(1).collect();
    if arguments == [OsString::from("--internal-git")] {
        return git::serve();
    }
    if arguments == [OsString::from("--version")] {
        println!("smelt {}", env!("CARGO_PKG_VERSION"));
        return Ok(0);
    }
    let python = python()?;
    trace(format!("interpreter: {}", python.display()));
    let text: Option<Vec<_>> = arguments
        .iter()
        .map(|arg| arg.to_str().map(String::from))
        .collect();
    let enabled = match env::var("SMELT_DAEMON").as_deref() {
        Ok("0") => false,
        Ok("1") => true,
        _ => env::var_os("CI").is_none(),
    };
    // Workers start outside the project. Relative Python search-path overrides
    // must keep their caller-relative meaning in a foreground process.
    let relative_python_paths = ["PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE"]
        .into_iter()
        .filter_map(env::var_os)
        .filter(|value| !value.is_empty())
        .any(|value| env::split_paths(&value).any(|path| path.is_relative()));
    if enabled
        && !relative_python_paths
        && let Some(text) = text
        && text.first().map(String::as_str) == Some("check")
        && !text.iter().any(|value| {
            value.starts_with("--fi") || matches!(value.as_str(), "--f" | "--help" | "-h")
        })
        && let Ok(response) = check(&python, &text)
        && !response.restart
    {
        io::stdout().write_all(response.out.as_bytes())?;
        io::stdout().flush()?;
        io::stderr().write_all(response.err.as_bytes())?;
        io::stderr().flush()?;
        trace("returning result");
        return Ok(response.code);
    }
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
