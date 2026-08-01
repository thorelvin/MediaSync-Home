from __future__ import annotations

from pathlib import Path

import pytest

import mediasync_home.adapters.windows_durability as windows_durability_module
from mediasync_home.adapters.windows_durability import move_path_write_through


def test_write_through_move_publishes_file_without_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "final.txt"
    source.write_bytes(b"payload")

    move_path_write_through(source, destination, replace_existing=False)

    assert not source.exists()
    assert destination.read_bytes() == b"payload"


def test_write_through_move_refuses_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "final.txt"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")

    with pytest.raises(FileExistsError):
        move_path_write_through(source, destination, replace_existing=False)

    assert source.read_bytes() == b"new"
    assert destination.read_bytes() == b"old"


def test_write_through_move_replaces_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "final.txt"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")

    move_path_write_through(source, destination, replace_existing=True)

    assert not source.exists()
    assert destination.read_bytes() == b"new"


def test_write_through_move_publishes_directory(tmp_path: Path) -> None:
    source = tmp_path / "staged"
    destination = tmp_path / "final"
    source.mkdir()
    (source / "marker").write_bytes(b"marker")

    move_path_write_through(source, destination, replace_existing=False)

    assert not source.exists()
    assert (destination / "marker").read_bytes() == b"marker"


@pytest.mark.parametrize(
    ("replace_existing", "expected_flags"),
    [(False, 0x00000008), (True, 0x00000009)],
)
def test_write_through_move_passes_exact_win32_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_existing: bool,
    expected_flags: int,
) -> None:
    move_file_ex = _MoveFileExSpy()
    monkeypatch.setattr(
        windows_durability_module.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: _Kernel32Spy(move_file_ex),
    )

    move_path_write_through(
        tmp_path / "source.tmp",
        tmp_path / "destination.txt",
        replace_existing=replace_existing,
    )

    assert len(move_file_ex.calls) == 1
    assert move_file_ex.calls[0][2] == expected_flags
    assert move_file_ex.calls[0][0].startswith("\\\\?\\")
    assert move_file_ex.calls[0][1].startswith("\\\\?\\")


class _MoveFileExSpy:
    def __init__(self) -> None:
        self.argtypes: object = None
        self.restype: object = None
        self.calls: list[tuple[str, str, int]] = []

    def __call__(self, source: str, destination: str, flags: int) -> int:
        self.calls.append((source, destination, flags))
        return 1


class _Kernel32Spy:
    def __init__(self, move_file_ex: _MoveFileExSpy) -> None:
        self.MoveFileExW = move_file_ex
