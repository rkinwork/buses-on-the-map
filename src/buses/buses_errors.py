from typing import Iterable


class MessageError(Exception):
    def __init__(
        self,
        expts: Iterable[str],
        message='Problems with parsing and validating response',
    ):
        self._exceptions_messages = expts
        super().__init__(message)

    def __iter__(self):
        return (message for message in self._exceptions_messages)


class BoundsReadMessageError(MessageError):
    """Raises when we have problems with bounds."""


class BusReadMessageError(MessageError):
    """Raises when we can't parse message from bus."""
