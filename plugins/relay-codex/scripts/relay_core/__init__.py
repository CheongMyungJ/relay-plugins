"""Shared Relay artifact recording helpers. No model or shell execution."""


class RelayError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
