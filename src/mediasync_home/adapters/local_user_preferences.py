from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from mediasync_home.application.user_preferences import UserPreferences


MAX_USER_PREFERENCES_BYTES = 16 * 1024


class LocalUserPreferencesStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> UserPreferences:
        if not self._path.exists():
            return UserPreferences()
        if self._path.stat().st_size > MAX_USER_PREFERENCES_BYTES:
            raise ValueError("USER_PREFERENCES_FILE_TOO_LARGE")
        payload = json.loads(
            self._path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
        if not isinstance(payload, dict):
            raise ValueError("USER_PREFERENCES_ROOT_INVALID")
        return UserPreferences.from_payload(payload)

    def save(self, preferences: UserPreferences) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    preferences.to_payload(),
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


def _unique_object(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("USER_PREFERENCES_DUPLICATE_KEY")
        payload[key] = value
    return payload
