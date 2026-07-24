from __future__ import annotations

import ctypes
import os


MAX_WINDOWS_COMMAND_LINE = 32760


class WindowsCommandLineError(ValueError):
    pass


def quote_windows_argument(argument: str) -> str:
    if "\x00" in argument:
        raise WindowsCommandLineError("WINDOWS_ARGUMENT_CONTAINS_NUL")
    if argument == "":
        return '""'
    if not any(character.isspace() or character == '"' for character in argument):
        return argument

    result = ['"']
    backslashes = 0
    for character in argument:
        if character == "\\":
            backslashes += 1
            continue
        if character == '"':
            result.append("\\" * (backslashes * 2 + 1))
            result.append('"')
            backslashes = 0
            continue
        result.append("\\" * backslashes)
        result.append(character)
        backslashes = 0
    result.append("\\" * (backslashes * 2))
    result.append('"')
    return "".join(result)


def build_windows_command_line(argv: tuple[str, ...]) -> str:
    if not argv:
        raise WindowsCommandLineError("WINDOWS_COMMAND_LINE_REQUIRES_ARGV")
    command_line = " ".join(quote_windows_argument(argument) for argument in argv)
    if len(command_line) > MAX_WINDOWS_COMMAND_LINE:
        raise WindowsCommandLineError("WINDOWS_COMMAND_LINE_TOO_LONG")
    return command_line


def build_windows_argument_line(arguments: tuple[str, ...]) -> str:
    if not arguments:
        return ""
    return build_windows_command_line(arguments)


def parse_windows_argument_line(argument_line: str) -> tuple[str, ...]:
    if argument_line == "":
        return ()
    return parse_windows_command_line(argument_line)


def parse_windows_command_line(command_line: str) -> tuple[str, ...]:
    if os.name != "nt":
        raise WindowsCommandLineError("WINDOWS_COMMAND_LINE_PARSE_REQUIRES_WINDOWS")
    if "\x00" in command_line:
        raise WindowsCommandLineError("WINDOWS_COMMAND_LINE_CONTAINS_NUL")

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    argc = ctypes.c_int()
    argv_pointer = shell32.CommandLineToArgvW(command_line, ctypes.byref(argc))
    if not argv_pointer:
        raise WindowsCommandLineError(
            f"WINDOWS_COMMAND_LINE_PARSE_FAILED:{ctypes.get_last_error()}"
        )
    try:
        return tuple(str(argv_pointer[index]) for index in range(argc.value))
    finally:
        kernel32.LocalFree(argv_pointer)
