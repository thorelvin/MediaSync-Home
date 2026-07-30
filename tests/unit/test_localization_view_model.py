from __future__ import annotations

import pytest

from mediasync_home.presentation.view_models.localization import (
    LanguageCode,
    localize_display_value,
    normalize_language_code,
)


@pytest.mark.parametrize(
    ("english", "norwegian"),
    [
        ("Latest run: run-a", "Siste kjøring: run-a"),
        ("Activity: Checking", "Aktivitet: Kontrollerer"),
        ("Attention: Waiting", "Oppmerksomhet: Venter"),
        ("Freshness per target: Not configured", "Ferskhet per mål: Ikke konfigurert"),
        (
            "Next action: Create backup when source and target are ready.",
            "Neste handling: Opprett backup når kilde og mål er klare.",
        ),
    ],
)
def test_localize_display_value_translates_activity_prefixes_both_directions(
    english: str,
    norwegian: str,
) -> None:
    assert localize_display_value(LanguageCode.NORWEGIAN, english) == norwegian
    assert localize_display_value(LanguageCode.ENGLISH, norwegian) == english


def test_normalize_language_code_accepts_only_supported_flags() -> None:
    assert normalize_language_code("nb") is LanguageCode.NORWEGIAN
    assert normalize_language_code("en") is LanguageCode.ENGLISH
    assert normalize_language_code("de") is None
