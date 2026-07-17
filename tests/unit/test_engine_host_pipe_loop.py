from __future__ import annotations

import pytest

from mediasync_home.composition.engine_host import build_parser, serve_bounded_pipe_requests


def test_bounded_pipe_loop_serves_exact_request_limit() -> None:
    server = _FakePipeServer()

    result = serve_bounded_pipe_requests(server, request_limit=3)

    assert result.completed is True
    assert result.error_type is None
    assert result.served_requests == 3
    assert server.calls == 3


def test_bounded_pipe_loop_reports_sanitized_failure() -> None:
    server = _FakePipeServer(fail_on_call=2)

    result = serve_bounded_pipe_requests(server, request_limit=4)

    assert result.completed is False
    assert result.error_type == "RuntimeError"
    assert result.served_requests == 1
    assert server.calls == 2


def test_engine_host_parser_requires_positive_serve_request_limit() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--serve-requests", "0"])


class _FakePipeServer:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self._fail_on_call = fail_on_call

    def serve_once(self) -> None:
        self.calls += 1
        if self.calls == self._fail_on_call:
            raise RuntimeError("internal detail must not leak")
