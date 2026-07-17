from __future__ import annotations

from enum import Enum


class ProcessRole(str, Enum):
    LAUNCHER = "launcher"
    ENGINE_HOST = "engine-host"
    TRIGGER_CLIENT = "trigger-client"
    GUI = "gui"

