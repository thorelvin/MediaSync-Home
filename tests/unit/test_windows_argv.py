from __future__ import annotations

import os

import pytest

from mediasync_home.adapters.windows_argv import (
    MAX_WINDOWS_COMMAND_LINE,
    WindowsCommandLineError,
    build_windows_argument_line,
    build_windows_command_line,
    parse_windows_argument_line,
)


@pytest.mark.skipif(os.name != "nt", reason="CommandLineToArgvW is Windows-only")
def test_windows_argument_line_round_trips_corpus() -> None:
    corpus = (
        ("plain", "two words", ""),
        (r"C:\Users\Ada\Pictures", r"\\server\share\folder"),
        (r"trailing\\", r'text with "quotes" inside'),
        ("--flag-like", "/windows-switch-like", "æøå"),
    )

    for arguments in corpus:
        argument_line = build_windows_argument_line(arguments)

        assert parse_windows_argument_line(argument_line) == arguments


def test_windows_command_line_rejects_empty_argv_nul_and_overflow() -> None:
    with pytest.raises(WindowsCommandLineError, match="WINDOWS_COMMAND_LINE_REQUIRES_ARGV"):
        build_windows_command_line(())

    with pytest.raises(WindowsCommandLineError, match="WINDOWS_ARGUMENT_CONTAINS_NUL"):
        build_windows_command_line(("ok", "bad\x00value"))

    with pytest.raises(WindowsCommandLineError, match="WINDOWS_COMMAND_LINE_TOO_LONG"):
        build_windows_command_line(("python.exe", "x" * MAX_WINDOWS_COMMAND_LINE))
