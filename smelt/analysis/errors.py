class AnalysisError(Exception):
    """The project cannot be analyzed (missing packages, unreadable source, ...)."""


class InvalidOptionError(AnalysisError):
    """A check option names something that does not exist; a usage error."""

    def __init__(self, message: str, *, option: str, value: str) -> None:
        super().__init__(message)
        self.option = option
        self.value = value
