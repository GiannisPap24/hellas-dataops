"""Actionable errors shared by canonical weather validation."""


class DomainValidationError(ValueError):
    """Invalid domain input, with a stable code and the affected field path."""

    def __init__(self, code: str, field: str, message: str) -> None:
        self.code = code
        self.field = field
        super().__init__(f"{field}: {message} [{code}]")
