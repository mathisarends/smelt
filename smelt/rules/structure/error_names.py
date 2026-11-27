from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class PreferErrorName(Rule):
    code = "MOD013"
    name = "modeling/prefer-error-name"
    category = Category.MODELING
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary=(
            "An exception class or module is named with Exception instead of Error."
        ),
        rationale=(
            "PEP 8 names exceptions that signal errors with an Error suffix, as "
            "the builtins ValueError and OSError do. Mixing ParseException with "
            "ParseError, or exceptions.py with errors.py, leaves readers and "
            "agents guessing which spelling a project uses. This opt-in "
            "convention settles on Error. A class is reported when its name ends "
            "in Exception after a non-empty stem and a base names an exception: "
            "it ends in Error, Exception or Warning, is a builtin such as "
            "KeyboardInterrupt or ExceptionGroup, or is an exception class of the "
            "same module. Imported bases are judged by their names only, so a "
            "subclass of an imported Failure class is not recognised. Modules and "
            "packages named exception or exceptions are reported, with or without "
            "leading underscores. Names that merely mention exceptions, such as "
            "ExceptionHandler or exception_handlers.py, signals that are not "
            "errors, such as Cancelled, the builtin Exception itself and test "
            "files are outside scope."
        ),
        bad=(
            "# app/exceptions.py\n"
            "class AppException(Exception):\n    ...\n\n"
            "class ParseException(AppException):\n    ..."
        ),
        good=(
            "# app/errors.py\n"
            "class AppError(Exception):\n    ...\n\n"
            "class ParseError(AppError):\n    ..."
        ),
        fix=(
            "Rename the class to the suggested Error name and the module or "
            "package to errors, then update imports, except clauses and "
            "deliberate exports. When errors already exists, merge the two. A "
            "reasoned suppression can preserve a published name. No symbols or "
            "files are renamed automatically. Enable with rules: "
            "{modeling/prefer-error-name: error}, globally or in a style "
            "override, or --select MOD013."
        ),
        canonical=(
            "Name exception classes with an Error suffix and their module errors"
        ),
        config=("rules", "style.overrides"),
    )
