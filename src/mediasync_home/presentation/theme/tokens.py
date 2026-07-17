from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    name: str
    window_background: str
    surface: str
    surface_alt: str
    panel: str
    text: str
    text_muted: str
    border: str
    accent: str
    accent_text: str
    success: str
    success_surface: str
    warning: str
    warning_surface: str
    danger: str
    danger_surface: str
    focus: str
    selection: str
    space_1: int = 4
    space_2: int = 8
    space_3: int = 12
    space_4: int = 16
    space_5: int = 20
    space_6: int = 24
    space_8: int = 32
    space_10: int = 40
    space_12: int = 48
    radius_sm: int = 6
    radius_md: int = 8
    radius_lg: int = 8
    border_hairline: int = 1
    border_focus: int = 2
    font_size_body: int = 13
    font_size_small: int = 12
    font_size_title: int = 18


LIGHT_TOKENS = ThemeTokens(
    name="light",
    window_background="#eef2f4",
    surface="#fbfcfd",
    surface_alt="#f3f6f7",
    panel="#ffffff",
    text="#172026",
    text_muted="#5f6f78",
    border="#ccd6db",
    accent="#0f766e",
    accent_text="#ffffff",
    success="#1f7a4d",
    success_surface="#e7f4ed",
    warning="#9a6a12",
    warning_surface="#fff3cf",
    danger="#b42318",
    danger_surface="#fde8e5",
    focus="#2563eb",
    selection="#d8ece9",
)


DARK_TOKENS = ThemeTokens(
    name="dark",
    window_background="#151a1d",
    surface="#1d2428",
    surface_alt="#232d31",
    panel="#20282d",
    text="#eef4f5",
    text_muted="#aab8bd",
    border="#3a474d",
    accent="#2dd4bf",
    accent_text="#09201d",
    success="#7dd3a8",
    success_surface="#123527",
    warning="#f2c45b",
    warning_surface="#3a2e10",
    danger="#ff8a80",
    danger_surface="#3b1714",
    focus="#8ab4ff",
    selection="#183e3a",
)
