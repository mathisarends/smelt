//! How long each phase of a core request took, which `SMELT_PROFILE` prints.
//!
//! Without the variable a span only checks one cached flag, so phases stay
//! instrumented in release builds. Spans record the thread that ran them: the
//! base revision of a `--changed` check runs on threads named `baseline`.
use std::borrow::Cow;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

const VARIABLE: &str = "SMELT_PROFILE";

struct Record {
    label: Cow<'static, str>,
    scope: String,
    start: Duration,
    elapsed: Duration,
}

static RECORDS: Mutex<Vec<Record>> = Mutex::new(Vec::new());

/// When profiling started, or `None` when it is off.
fn origin() -> Option<Instant> {
    static ORIGIN: OnceLock<Option<Instant>> = OnceLock::new();
    *ORIGIN.get_or_init(|| std::env::var_os(VARIABLE).map(|_| Instant::now()))
}

/// Start the clock, so offsets count from the start of the request.
pub fn start() {
    origin();
}

/// A phase that ends when the span is dropped.
#[must_use = "a span measures until it is dropped"]
pub struct Span(Option<(Cow<'static, str>, Instant)>);

/// Measure a phase until the returned span is dropped.
pub fn span(label: impl Into<Cow<'static, str>>) -> Span {
    Span(origin().map(|_| (label.into(), Instant::now())))
}

/// Measure `work` as one phase.
pub fn time<R>(label: impl Into<Cow<'static, str>>, work: impl FnOnce() -> R) -> R {
    let _span = span(label);
    work()
}

impl Drop for Span {
    fn drop(&mut self) {
        let (Some((label, started)), Some(origin)) = (self.0.take(), origin()) else {
            return;
        };
        let elapsed = started.elapsed();
        let scope = std::thread::current()
            .name()
            .map_or("main", |name| name.split('-').next().unwrap_or(name))
            .to_owned();
        let record = Record {
            label,
            scope,
            start: started.saturating_duration_since(origin),
            elapsed,
        };
        if let Ok(mut records) = RECORDS.lock() {
            records.push(record);
        }
    }
}

/// Print every recorded phase to stderr in the order the phases started.
pub fn report() {
    if origin().is_none() {
        return;
    }
    let Ok(mut records) = RECORDS.lock() else {
        return;
    };
    records.sort_by_key(|record| record.start);
    let milliseconds = |duration: Duration| duration.as_secs_f64() * 1000.0;
    eprintln!(
        "smelt profile: {:>9} {:>9}  scope     phase",
        "start ms", "took ms"
    );
    for record in records.drain(..) {
        eprintln!(
            "smelt profile: {:>9.1} {:>9.1}  {:<9} {}",
            milliseconds(record.start),
            milliseconds(record.elapsed),
            record.scope,
            record.label,
        );
    }
}
