from __future__ import annotations

from pathlib import Path

import pytest

from mediasync_home.adapters.local_user_preferences import LocalUserPreferencesStore
from mediasync_home.application.user_preferences import (
    AppearancePreference,
    DensityPreference,
    UserLanguage,
    UserPreferences,
    load_user_preferences,
)


def test_local_user_preferences_round_trip_atomically(tmp_path: Path) -> None:
    path = tmp_path / "settings" / "user-preferences.json"
    store = LocalUserPreferencesStore(path)
    preferences = UserPreferences(
        appearance=AppearancePreference.DARK,
        density=DensityPreference.COMPACT,
        reduced_motion=True,
        language=UserLanguage.ENGLISH,
    )

    store.save(preferences)

    assert store.load() == preferences
    assert tuple(path.parent.glob("*.tmp")) == ()


def test_local_user_preferences_reject_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "user-preferences.json"
    path.write_text(
        (
            '{"appearance":"light","appearance":"dark","density":"comfortable",'
            '"language":"nb","reduced_motion":false,"schema_version":1}'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="DUPLICATE_KEY"):
        LocalUserPreferencesStore(path).load()


def test_load_user_preferences_falls_back_when_file_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "user-preferences.json"
    path.write_text("not-json", encoding="utf-8")
    fallback = UserPreferences(appearance=AppearancePreference.LIGHT)

    loaded = load_user_preferences(
        LocalUserPreferencesStore(path),
        fallback=fallback,
    )

    assert loaded == fallback
