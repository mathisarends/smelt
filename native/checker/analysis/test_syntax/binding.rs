//! What a name in a test is known to hold.
use crate::checker::analysis::nodes::{parse_eval, subscript_parts};
use ruff_python_ast::Expr;
use std::collections::HashMap;
use std::sync::Arc;

pub const MONKEYPATCH: &str = "pytest.MonkeyPatch";
const MOCK_ROOTS: [&str; 2] = ["unittest.mock.", "pytest_mock.MockerFixture."];
const MOCK_OPERATIONS: [&str; 12] = [
    "Mock",
    "MagicMock",
    "AsyncMock",
    "NonCallableMock",
    "NonCallableMagicMock",
    "PropertyMock",
    "mock_open",
    "create_autospec",
    "patch",
    "spy",
    "stub",
    "async_stub",
];
const ITERATOR_ORIGINS: [&str; 8] = [
    "typing.Iterator",
    "typing.Generator",
    "typing.AsyncIterator",
    "typing.AsyncGenerator",
    "collections.abc.Iterator",
    "collections.abc.Generator",
    "collections.abc.AsyncIterator",
    "collections.abc.AsyncGenerator",
];

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Symbol,
    Instance,
    Mock,
    Member,
    Nonempty,
    Helper,
}

/// The items of a fixed tuple, each known or unknown.
pub type Items = Vec<Option<Arc<Binding>>>;

#[derive(Clone)]
pub struct Binding {
    pub qualified: String,
    pub kind: Kind,
    pub provenance: &'static str,
    pub items: Option<Items>,
    pub mock_spec: Option<String>,
    pub async_callable: Option<String>,
    /// Identifies one mock across aliases; two equal bindings can differ here.
    pub mock_origin: Option<String>,
    pub mutable: bool,
    pub text: Option<String>,
    pub helper_line: Option<usize>,
}

impl PartialEq for Binding {
    fn eq(&self, other: &Self) -> bool {
        self.qualified == other.qualified
            && self.kind == other.kind
            && self.provenance == other.provenance
            && self.items == other.items
            && self.mock_spec == other.mock_spec
            && self.async_callable == other.async_callable
            && self.mutable == other.mutable
            && self.text == other.text
            && self.helper_line == other.helper_line
    }
}

impl Binding {
    pub fn new(qualified: &str, kind: Kind, provenance: &'static str) -> Self {
        Self {
            qualified: qualified.to_owned(),
            kind,
            provenance,
            items: None,
            mock_spec: None,
            async_callable: None,
            mock_origin: None,
            mutable: false,
            text: None,
            helper_line: None,
        }
    }

    pub fn symbol(qualified: &str) -> Self {
        Self::new(qualified, Kind::Symbol, "import")
    }

    pub fn nonempty(provenance: &'static str) -> Self {
        Self::new("", Kind::Nonempty, provenance)
    }

    /// An instance of `qualified`; a known mock type makes it a mock.
    pub fn instance(qualified: &str, provenance: &'static str) -> Self {
        let kind = if is_mock_type(qualified) {
            Kind::Mock
        } else {
            Kind::Instance
        };
        let mut binding = Self::new(qualified, kind, provenance);
        let is_async = mock_operation(qualified)
            .is_some_and(|name| matches!(name, "AsyncMock" | "async_stub"));
        if is_async {
            binding.async_callable = Some(qualified.to_owned());
        }
        binding
    }

    /// The same value as a fixture whose type was inferred from its body.
    pub fn inferred_fixture(&self) -> Self {
        let mut binding = self.clone();
        binding.provenance = "fixture-inferred";
        binding.items = self.items.as_ref().map(|items| {
            items
                .iter()
                .map(|item| item.as_ref().map(|item| Arc::new(item.inferred_fixture())))
                .collect()
        });
        binding
    }
}

fn mock_operation(qualified: &str) -> Option<&str> {
    MOCK_ROOTS
        .iter()
        .find_map(|root| qualified.strip_prefix(root))
}

fn is_mock_type(qualified: &str) -> bool {
    mock_operation(qualified)
        .is_some_and(|name| !matches!(name, "patch" | "spy") && MOCK_OPERATIONS.contains(&name))
}

pub fn is_mock_constructor(qualified: &str) -> bool {
    is_mock_type(qualified)
}

pub fn is_mock_call(qualified: &str) -> bool {
    mock_operation(qualified).is_some_and(|name| MOCK_OPERATIONS.contains(&name))
}

pub fn is_patch_call(qualified: &str) -> bool {
    qualified
        .strip_prefix(MONKEYPATCH)
        .is_some_and(|rest| matches!(rest, ".setattr" | ".delattr"))
        || mock_operation(qualified).is_some_and(|name| {
            matches!(
                name,
                "patch" | "patch.object" | "patch.dict" | "patch.multiple"
            )
        })
}

/// `_fixture_binding`: what a fixture's return annotation promises.
pub fn fixture_binding(node: &Expr, env: &HashMap<&str, Arc<Binding>>) -> Option<Binding> {
    if let Expr::StringLiteral(text) = node {
        let parsed = parse_eval(text.value.to_str())?;
        return fixture_binding(parsed.expr(), env);
    }
    if let Expr::Subscript(subscript) = node {
        let origin = annotation_target(&subscript.value, env);
        let arguments = subscript_parts(&subscript.slice);
        if origin
            .as_deref()
            .is_some_and(|origin| ITERATOR_ORIGINS.contains(&origin))
        {
            return arguments
                .first()
                .and_then(|argument| fixture_binding(argument, env));
        }
        if matches!(origin.as_deref(), Some("builtins.tuple" | "typing.Tuple")) {
            let mut binding = Binding::nonempty("fixture-return");
            binding.items = Some(
                arguments
                    .iter()
                    .map(|argument| fixture_binding(argument, env).map(Arc::new))
                    .collect(),
            );
            return Some(binding);
        }
    }
    annotation_target(node, env)
        .filter(|qualified| !qualified.is_empty())
        .map(|qualified| Binding::instance(&qualified, "fixture-return"))
}

/// `_annotation`: the qualified name an annotation expression refers to.
pub fn annotation_target(node: &Expr, env: &HashMap<&str, Arc<Binding>>) -> Option<String> {
    match node {
        Expr::StringLiteral(text) => {
            let parsed = parse_eval(text.value.to_str())?;
            annotation_target(parsed.expr(), env)
        }
        Expr::Name(name) => env
            .get(name.id.as_str())
            .map(|binding| binding.qualified.clone()),
        Expr::Attribute(attribute) => annotation_target(&attribute.value, env)
            .filter(|parent| !parent.is_empty())
            .map(|parent| format!("{parent}.{}", attribute.attr)),
        _ => None,
    }
}
