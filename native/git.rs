//! Read-only Git service. No repository handles survive an individual request.
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::Deserialize;
use serde_json::{Value, json};
use std::io::{self, BufRead, Read, Write};
use std::path::PathBuf;

const LIMIT: u64 = 64 * 1024 * 1024;

#[derive(Deserialize)]
struct Request {
    operation: String,
    root: PathBuf,
    #[serde(default)]
    identifiers: Vec<String>,
}

fn execute(request: Request) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
    let repo = gix::discover_opts(
        &request.root,
        Default::default(),
        gix::open::Options::default().bail_if_untrusted(true),
    )?;
    let root = repo.workdir().ok_or("bare repository")?;
    let root = root.to_str().ok_or("non-UTF-8 repository root")?;
    match request.operation.as_str() {
        "capture" | "index" => {
            let index = repo.index_or_empty()?;
            let mut records = Vec::new();
            for entry in index.entries() {
                if entry.mode == gix::index::entry::Mode::DIR {
                    return Err("sparse index requires Git expansion".into());
                }
                write!(
                    records,
                    "{:06o} {} {}\t",
                    entry.mode.bits(),
                    entry.id,
                    entry.stage_raw()
                )?;
                records.extend_from_slice(entry.path(&index).as_ref());
                records.push(0);
            }
            let head = repo.head()?.id().map(|id| id.to_string());
            Ok(json!({"repository": root, "head": head, "records": STANDARD.encode(records)}))
        }
        "blobs" => {
            let mut blobs = serde_json::Map::new();
            for identifier in request.identifiers {
                let id = gix::hash::ObjectId::from_hex(identifier.as_bytes())?;
                let blob = repo.find_blob(id)?;
                blobs.insert(identifier, json!(STANDARD.encode(&blob.data)));
            }
            Ok(json!({"blobs": blobs}))
        }
        "tree" => {
            let revision = request.identifiers.first().ok_or("missing revision")?;
            let id = gix::hash::ObjectId::from_hex(revision.as_bytes())?;
            let tree = repo.find_commit(id)?.tree()?;
            let mut entries = tree.traverse().breadthfirst.files()?;
            entries.sort_by(|a, b| a.filepath.cmp(&b.filepath));
            let mut records = Vec::new();
            for entry in entries {
                if entry.mode.is_tree() {
                    continue;
                }
                let kind = if entry.mode.is_commit() {
                    "commit"
                } else {
                    "blob"
                };
                write!(records, "{:06o} {kind} {}\t", entry.mode.value(), entry.oid)?;
                records.extend_from_slice(entry.filepath.as_ref());
                records.push(0);
            }
            Ok(json!({"records": STANDARD.encode(records)}))
        }
        _ => Err("unsupported operation".into()),
    }
}

pub fn serve() -> io::Result<i32> {
    let input = io::stdin();
    let mut input = input.lock();
    let output = io::stdout();
    let mut output = output.lock();
    loop {
        let mut line = Vec::new();
        if input.by_ref().take(LIMIT).read_until(b'\n', &mut line)? == 0 {
            return Ok(0);
        }
        if line.last() != Some(&b'\n') {
            return Err(io::Error::other("oversized Git request"));
        }
        let response = match serde_json::from_slice::<Request>(&line) {
            Ok(request) => match execute(request) {
                Ok(value) => value,
                Err(error) => json!({"fallback": error.to_string()}),
            },
            Err(error) => json!({"fallback": error.to_string()}),
        };
        serde_json::to_writer(&mut output, &response)?;
        output.write_all(b"\n")?;
        output.flush()?;
    }
}
