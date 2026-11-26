"""Safe application errors; provider response bodies never become client messages."""


class AppError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        self.message = message
        self.status = status
        super().__init__(message)


class ProviderError(AppError):
    def __init__(self, message: str, status: int = 502, *, uncertain: bool = False) -> None:
        self.uncertain = uncertain
        super().__init__(message, status)
