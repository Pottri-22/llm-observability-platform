"""Minimal fake OpenAI-compatible clients for the SDK test suite.

The SDK is unit-tested without the real `openai` package installed. These fakes mimic
just the surface `instrument()` touches: `client.chat.completions.create`,
`client.base_url`, and the response/chunk attribute shapes. Shared by SDK-B and SDK-C
tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeDelta:
    def __init__(self, content: str | None = None) -> None:
        self.content = content


class FakeChoice:
    def __init__(
        self,
        message: FakeMessage | None = None,
        delta: FakeDelta | None = None,
        finish_reason: str = "stop",
    ) -> None:
        self.message = message
        self.delta = delta
        self.finish_reason = finish_reason


class FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeResponse:
    """A non-streaming chat completion response."""

    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[Any] | None = None,
        usage: FakeUsage | None = None,
    ) -> None:
        self.choices = [FakeChoice(message=FakeMessage(content, tool_calls))]
        self.usage = usage


class FakeStreamChunk:
    """One chunk of a streamed response. The final usage chunk has no choices."""

    def __init__(
        self,
        content: str | None = None,
        usage: FakeUsage | None = None,
        has_choice: bool = True,
    ) -> None:
        self.choices = [FakeChoice(delta=FakeDelta(content))] if has_choice else []
        self.usage = usage


# `_result` is one of: FakeResponse, list[FakeStreamChunk], or an Exception instance.
class FakeCompletions:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.received_kwargs: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.received_kwargs.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        if isinstance(self._result, list):
            return iter(self._result)
        return self._result


class FakeAsyncCompletions:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.received_kwargs: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.received_kwargs.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        if isinstance(self._result, list):

            async def _agen() -> AsyncIterator[Any]:
                for chunk in self._result:
                    yield chunk

            return _agen()
        return self._result


class FakeChat:
    def __init__(self, completions: FakeCompletions | FakeAsyncCompletions) -> None:
        self.completions = completions


class FakeClient:
    """Stand-in for `openai.OpenAI` / `openai.AsyncOpenAI`.

    `result` is what `.chat.completions.create` should produce: a `FakeResponse`, a list
    of `FakeStreamChunk` (streaming), or an `Exception` to raise. Set `is_async=True` for
    the async variant — pair it with `instrument(..., async_client=True)` since the real
    `openai._base_client.AsyncAPIClient` isn't importable in tests.
    """

    def __init__(
        self,
        result: Any,
        base_url: str = "https://api.openai.com/v1",
        is_async: bool = False,
    ) -> None:
        completions: FakeCompletions | FakeAsyncCompletions = (
            FakeAsyncCompletions(result) if is_async else FakeCompletions(result)
        )
        self.chat = FakeChat(completions)
        self.base_url = base_url
