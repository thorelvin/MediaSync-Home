from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol


USER_PREFERENCES_SCHEMA_VERSION = 1


class AppearancePreference(str, Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class DensityPreference(str, Enum):
    COMFORTABLE = "comfortable"
    COMPACT = "compact"


class UserLanguage(str, Enum):
    NORWEGIAN = "nb"
    ENGLISH = "en"


@dataclass(frozen=True, slots=True)
class UserPreferences:
    appearance: AppearancePreference = AppearancePreference.SYSTEM
    density: DensityPreference = DensityPreference.COMFORTABLE
    reduced_motion: bool = False
    language: UserLanguage = UserLanguage.NORWEGIAN
    schema_version: int = USER_PREFERENCES_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != USER_PREFERENCES_SCHEMA_VERSION:
            raise ValueError("USER_PREFERENCES_SCHEMA_UNSUPPORTED")

    def to_payload(self) -> dict[str, object]:
        return {
            "appearance": self.appearance.value,
            "density": self.density.value,
            "language": self.language.value,
            "reduced_motion": self.reduced_motion,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "UserPreferences":
        if payload.get("schema_version") != USER_PREFERENCES_SCHEMA_VERSION:
            raise ValueError("USER_PREFERENCES_SCHEMA_UNSUPPORTED")
        reduced_motion = payload.get("reduced_motion")
        if not isinstance(reduced_motion, bool):
            raise ValueError("USER_PREFERENCES_REDUCED_MOTION_INVALID")
        try:
            return cls(
                appearance=AppearancePreference(_required_string(payload, "appearance")),
                density=DensityPreference(_required_string(payload, "density")),
                language=UserLanguage(_required_string(payload, "language")),
                reduced_motion=reduced_motion,
            )
        except ValueError as exc:
            raise ValueError("USER_PREFERENCES_VALUE_INVALID") from exc


class UserPreferencesStore(Protocol):
    def load(self) -> UserPreferences: ...

    def save(self, preferences: UserPreferences) -> None: ...


def load_user_preferences(
    store: UserPreferencesStore | None,
    *,
    fallback: UserPreferences | None = None,
) -> UserPreferences:
    default = fallback or UserPreferences()
    if store is None:
        return default
    try:
        return store.load()
    except (OSError, ValueError):
        return default


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"USER_PREFERENCES_{key.upper()}_INVALID")
    return value
