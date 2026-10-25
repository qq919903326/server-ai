class ToolExecutionError(ValueError):
    """An intentionally sanitized error which may be shown to users and the model."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
