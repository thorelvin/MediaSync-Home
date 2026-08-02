from __future__ import annotations

import os


class WindowsTimeZoneLookupError(RuntimeError):
    pass


def current_windows_time_zone_id() -> str:
    if os.name != "nt":
        raise WindowsTimeZoneLookupError("WINDOWS_TIME_ZONE_UNAVAILABLE")

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation",
        ) as key:
            value, _value_type = winreg.QueryValueEx(key, "TimeZoneKeyName")
    except OSError as exc:
        raise WindowsTimeZoneLookupError("WINDOWS_TIME_ZONE_LOOKUP_FAILED") from exc
    if not isinstance(value, str) or not value.strip("\x00 "):
        raise WindowsTimeZoneLookupError("WINDOWS_TIME_ZONE_ID_INVALID")
    return value.strip("\x00 ")
