"""通用模拟器与原生 Android 连接配置。"""

from typing import Annotated

from auto_mas_core import (
    ConfigCollection,
    EmulatorEntry,
    ExecutablePath,
    UiVisibility,
    collection,
    ui,
)
from auto_mas_core.devices import ManagedEmulatorDevice
from pydantic import Field


class GeneralDevice(ManagedEmulatorDevice):
    class Info(ManagedEmulatorDevice.Info):
        adb_address: Annotated[str, ui(visibility=UiVisibility.SHOW)] = Field(
            default="", description="ADB 地址或设备序列号"
        )
        arguments: list[str] = Field(default_factory=list, description="启动参数")

    info: Info = Field(default_factory=Info)  # pyright: ignore[reportIncompatibleVariableOverride]


class GeneralGame(EmulatorEntry):
    class Info(EmulatorEntry.Info):
        adb_path: ExecutablePath = Field(
            default=None, description="ADB 程序，留空使用系统 adb"
        )

    info: Info = Field(default_factory=Info)  # pyright: ignore[reportIncompatibleVariableOverride]
    devices: ConfigCollection[GeneralDevice] = collection(GeneralDevice)  # type: ignore[assignment]  # pyright: ignore[reportIncompatibleVariableOverride]
