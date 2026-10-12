"""固定源内置应用的本人激活合同；不接入外部插件管理。"""

from pydantic import Field, StrictBool

from .base import InputModel

EDITOR_TOOL_IDS = ("prompt-optimizer", "ai-art-critique", "runninghub-workflow-provider")
EDITOR_TOOL_PREFERENCES_KEY = "editor_tool_states"


class CanvasEditorToolActivation(InputModel):
    enabled: StrictBool


class CanvasEditorToolStateRead(InputModel):
    plugin_id: str = Field(alias="pluginId")
    platform_available: bool = Field(alias="platformAvailable")
    user_enabled: bool = Field(alias="userEnabled")
    user_configured: bool = Field(alias="userConfigured")
    effective_enabled: bool = Field(alias="effectiveEnabled")
    can_toggle: bool = Field(alias="canToggle")
    can_configure: bool = Field(alias="canConfigure")


class CanvasEditorToolStatusRead(InputModel):
    statuses: dict[str, str]
    states: dict[str, CanvasEditorToolStateRead]


class CanvasEditorToolActivationRead(InputModel):
    state: CanvasEditorToolStateRead
