"""Shared HTTP ownership and sanitized status mapping."""

import httpx

from masterclaw.classifiers.base import (
    ClassifierAuthenticationError,
    ClassifierRateLimitError,
    ClassifierRequestError,
    ClassifierServerError,
    ClassifierTimeoutError,
)


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """Let an owned client borrow a caller-owned transport without closing it."""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._transport.handle_async_request(request)


def _http_error(status: int):
    message = f"classifier HTTP status {status}"
    if status in {401, 403}:
        return ClassifierAuthenticationError(message)
    if status == 408:
        return ClassifierTimeoutError(message)
    if status == 429:
        return ClassifierRateLimitError(message)
    if status >= 500:
        return ClassifierServerError(message)
    return ClassifierRequestError(message)
