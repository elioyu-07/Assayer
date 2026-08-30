class HostError(Exception):
    """Structured protocol error exposed by Host Core."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, next_step: str = "stop"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.next_step = next_step

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "requiredNextStep": self.next_step,
        }
