"""通用模拟器的可选进程启动、ADB 就绪与应用操作。"""

import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import UUID, uuid4

import psutil
from auto_mas_core import (
    DeviceStatus,
    DeviceSubscribeError,
    EmulatorControl,
    EmulatorExpect,
    LockTicket,
    config_manager,
)
from auto_mas_core.devices import DeviceInfo, DeviceRef
from auto_mas_core.utils import (
    ProcessInfo,
    ProcessManager,
    ProcessResult,
    ProcessRunner,
    file_lock,
    get_logger,
)

from .config import GeneralDevice, GeneralGame
from .process import (
    AudioMuteRecord,
    apply_launch_audio_mute,
    restore_audio_before_close,
)


class Control(EmulatorControl[GeneralGame, GeneralDevice]):
    capabilities = (
        "open",
        "close",
        "show",
        "hide",
        "minimize",
        "maximize",
        "create_instance",
        "delete_instance",
        "install_app",
        "open_store",
    )

    def __init__(self, config: GeneralGame | None = None) -> None:
        super().__init__(config=config)
        self._processes: dict[UUID, ProcessManager] = {}
        self._resources: dict[UUID, BinaryIO] = {}
        self._operation = asyncio.Lock()
        self._audio_mute_states: dict[str, AudioMuteRecord] = {}

    @property
    def busy(self) -> bool:
        return self._operation.locked()

    def device(self, uid: UUID) -> GeneralDevice:
        device = self.config.devices[uid]
        if device.info.tombstone:
            raise KeyError("设备连接已删除")
        return device

    async def _adb(self, uid: UUID, *args: str, timeout: float = 20) -> ProcessResult:
        device = self.device(uid)
        if not device.info.adb_address:
            raise ValueError("尚未配置 ADB 地址")
        adb = self.config.info.adb_path or "adb"
        if ":" in device.info.adb_address:
            await ProcessRunner.run_process(
                adb,
                "connect",
                device.info.adb_address,
                timeout=timeout,
                if_merge_std=True,
            )
        return await ProcessRunner.run_process(
            adb,
            "-s",
            device.info.adb_address,
            *args,
            timeout=timeout,
            if_merge_std=True,
        )

    async def device_info(self, uid: UUID) -> DeviceInfo:
        result = await self._adb(uid, "get-state")
        status = (
            DeviceStatus.ONLINE
            if result.returncode == 0 and result.stdout.strip() == "device"
            else DeviceStatus.OFFLINE
        )
        device = self.device(uid)
        return DeviceInfo(device.info.name, status, device.info.adb_address)

    async def refresh(self) -> None:
        for device in list(self.config.devices.values()):
            if device.info.adb_address and not device.info.tombstone:
                device.info.status = (await self.device_info(device.uid)).status
                await device.commit()

    async def _restore(self, uid: UUID) -> None:
        device = self.device(uid)
        if device.session.backup:
            for command, value in device.session.backup.items():
                result = await self._adb(uid, "shell", "wm", command, value or "reset")
                if result.returncode != 0:
                    raise RuntimeError(result.failure_detail())
            device.session.backup = {}
            await device.commit()
            await config_manager.flush()

    async def _on_device_subscribed(
        self, device_uid: UUID, expect: object | None
    ) -> None:
        if expect is not None and not isinstance(expect, EmulatorExpect):
            raise TypeError("通用模拟器期望须为 EmulatorExpect")
        device = self.device(device_uid)
        if not device.info.adb_address:
            raise ValueError("尚未配置 ADB 地址")
        key = hashlib.sha256(device.info.adb_address.encode()).hexdigest()
        directory = Path(tempfile.gettempdir()) / "AUTO-MAS" / "adb-locks"
        directory.mkdir(parents=True, exist_ok=True)
        handle = (directory / key).open("a+b")
        try:
            file_lock.acquire(handle)
        except OSError as error:
            handle.close()
            raise DeviceSubscribeError(
                "ADB 设备已被其他任务占用", usage="其他 Android 任务"
            ) from error
        self._resources[device_uid] = handle
        try:
            await self._restore(device_uid)
        except Exception as error:
            get_logger("通用模拟器").warning(f"配置恢复失败，保留原备份并继续：{error}")
        # 离线设备开机后才可通过 wm 设置；实际写入在 open 中完成。

    async def _on_device_unsubscribed(self, device_uid: UUID) -> None:
        if device_uid not in self._resources:
            return
        try:
            await self._restore(device_uid)
        finally:
            handle = self._resources.pop(device_uid, None)
            if handle is not None:
                try:
                    file_lock.release(handle)
                finally:
                    handle.close()

    async def startup(self) -> None:
        for device in list(self.config.devices.values()):
            if not device.session.backup or device.info.tombstone:
                continue
            try:
                await self._on_device_subscribed(device.uid, None)
                await self._on_device_unsubscribed(device.uid)
            except Exception as error:
                get_logger("通用模拟器").warning(f"启动恢复失败，留待下次尝试：{error}")

    async def open(
        self, device_uid: UUID, package: str | None = None, *, ticket: LockTicket
    ) -> None:
        self.check_subscription(device_uid, ticket)
        device = self.device(device_uid)
        async with self.operation_scope(self._operation):
            launched = False
            if (
                await self.device_info(device_uid)
            ).status != DeviceStatus.ONLINE and self.config.info.path is not None:
                manager = self._processes.setdefault(device_uid, ProcessManager())
                if not await manager.is_running():
                    await manager.open_process(
                        self.config.info.path, *device.info.arguments, breakaway=True
                    )
                    launched = True
            async with asyncio.timeout(self.config.info.max_wait_time):
                while (
                    await self.device_info(device_uid)
                ).status != DeviceStatus.ONLINE:
                    await asyncio.sleep(1)
            if launched:
                await apply_launch_audio_mute(
                    self._audio_mute_states,
                    str(device_uid),
                    lambda: asyncio.to_thread(self._audio_pids, device_uid),
                    silence_enabled=lambda: self.runtime.silent,
                )
            sub = self._subs.get(device_uid)
            expect = sub.expect if sub is not None else None
            if isinstance(expect, EmulatorExpect):
                if not device.session.backup:
                    backup = {}
                    for command in ("size", "density"):
                        result = await self._adb(device_uid, "shell", "wm", command)
                        if result.returncode != 0:
                            raise RuntimeError(result.failure_detail())
                        match = re.search(
                            r"Override (?:size|density):\s*(\S+)", result.stdout
                        )
                        backup[command] = match.group(1) if match else ""
                    device.session.backup = backup
                    await device.commit()
                    await config_manager.flush()
                result = await self._adb(
                    device_uid, "shell", "wm", "size", f"{expect.width}x{expect.height}"
                )
                if result.returncode != 0:
                    raise RuntimeError(result.failure_detail())
                if expect.dpi is not None:
                    result = await self._adb(
                        device_uid, "shell", "wm", "density", str(expect.dpi)
                    )
                    if result.returncode != 0:
                        raise RuntimeError(result.failure_detail())
            if package:
                await self.launch_app(device_uid, package, ticket=ticket)
            await self.refresh()

    async def close(self, device_uid: UUID, *, ticket: LockTicket) -> None:
        self.check_subscription(device_uid, ticket)
        await restore_audio_before_close(self._audio_mute_states, str(device_uid))
        manager = self._processes.get(device_uid)
        if manager is not None:
            await manager.kill()
        else:
            result = await self._adb(device_uid, "emu", "kill")
            if result.returncode != 0:
                raise RuntimeError("此设备不提供模拟器关闭入口")
        await self.refresh()

    async def _window(
        self,
        uid: UUID,
        action: Literal["show_window", "hide_window", "minimize_window"],
        ticket: LockTicket,
    ) -> None:
        self.check_subscription(uid, ticket)
        manager = await self._host_process(uid)
        if not await getattr(manager, action)():
            raise RuntimeError("此设备未提供可控制的宿主窗口")

    async def _host_process(self, uid: UUID) -> ProcessManager:
        manager = self._processes.get(uid)
        if manager is not None and await manager.is_running():
            return manager
        path = self.config.info.path
        if path is None:
            raise RuntimeError("此设备未配置宿主程序，不能操作窗口")
        identity = os.path.normcase(str(path.resolve()))
        arguments = self.device(uid).info.arguments

        def matching_pids() -> list[int]:
            return [
                proc.pid
                for proc in psutil.process_iter(["exe", "cmdline"])
                if proc.info["exe"]
                and os.path.normcase(str(Path(proc.info["exe"]).resolve())) == identity
                and (not arguments or (proc.info["cmdline"] or [])[1:] == arguments)
            ]

        matches = await asyncio.to_thread(matching_pids)
        if len(matches) != 1:
            raise RuntimeError("无法按宿主程序和启动参数唯一定位设备窗口")
        manager = ProcessManager()
        await manager.search_process(
            ProcessInfo(pid=matches[0]), timeout_seconds=1, owned=False
        )
        self._processes[uid] = manager
        return manager

    async def show(self, device_uid: UUID, *, ticket: LockTicket) -> None:
        await self._window(device_uid, "show_window", ticket)

    async def hide(self, device_uid: UUID, *, ticket: LockTicket) -> None:
        await self._window(device_uid, "hide_window", ticket)

    async def minimize(self, device_uid: UUID, *, ticket: LockTicket) -> None:
        await self._window(device_uid, "minimize_window", ticket)

    async def maximize(self, device_uid: UUID, *, ticket: LockTicket) -> None:
        self.check_subscription(device_uid, ticket)
        manager = await self._host_process(device_uid)
        if manager.main_hwnd is None:
            raise RuntimeError("此设备未提供可控制的宿主窗口")
        import win32con
        import win32gui

        win32gui.ShowWindow(manager.main_hwnd, win32con.SW_MAXIMIZE)
        if (
            win32gui.GetWindowPlacement(manager.main_hwnd)[1]
            != win32con.SW_SHOWMAXIMIZED
        ):
            raise RuntimeError("模拟器窗口未能最大化")

    async def launch_app(
        self, device_uid: UUID, package: str, *, ticket: LockTicket
    ) -> None:
        self.check_subscription(device_uid, ticket)
        result = await self._adb(
            device_uid,
            "shell",
            "monkey",
            "-p",
            package,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        )
        if result.returncode != 0:
            raise RuntimeError(result.failure_detail())

    async def open_store(self, device_uid: UUID, *, ticket: LockTicket) -> None:
        self.check_subscription(device_uid, ticket)
        result = await self._adb(
            device_uid,
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            "market://search?q=game",
        )
        if result.returncode != 0:
            raise RuntimeError(result.failure_detail())

    async def create_instance(self, name: str | None = None) -> UUID:
        uid = uuid4()
        slot = max((d.info.slot for d in self.config.devices.values()), default=-1) + 1
        self.config.devices.add(
            GeneralDevice,
            uid=uid,
            payload={
                "info": {
                    "name": name or "Android 连接",
                    "index": str(slot),
                    "slot": slot,
                }
            },
        )
        await self.config.devices.commit()
        return uid

    async def delete_instance(self, device_uid: UUID, *, ticket: LockTicket) -> None:
        self.check_subscription(device_uid, ticket)
        device = self.device(device_uid)
        device.info.tombstone = True
        await device.commit()

    async def install_app(
        self, device_uid: UUID, path: Path, *, ticket: LockTicket
    ) -> None:
        self.check_subscription(device_uid, ticket)
        result = await self._adb(
            device_uid,
            "install",
            "-r",
            str(path),
            timeout=self.config.info.max_wait_time,
        )
        if result.returncode != 0 or "Success" not in result.stdout:
            raise RuntimeError(result.failure_detail())

    def resolve_device(self, uid: UUID) -> DeviceRef:
        return DeviceRef(
            "general", str(self.config.info.path or ""), self.device(uid).info.index
        )

    def get_adb_path(self) -> Path | None:
        return self.config.info.adb_path

    def recovery_pending(self, uid: UUID) -> bool:
        return bool(self.config.devices[uid].session.backup)

    async def teardown(self) -> None:
        for uid in list(self._audio_mute_states):
            await restore_audio_before_close(self._audio_mute_states, uid)
        for manager in self._processes.values():
            await manager.clear()

    def _audio_pids(self, uid: UUID) -> list[int]:
        manager = self._processes.get(uid)
        pid = manager.main_pid if manager is not None else None
        if pid is None:
            return []
        try:
            children = psutil.Process(pid).children(recursive=True)
        except psutil.NoSuchProcess:
            return []
        return [pid, *(child.pid for child in children)]


async def search() -> list[GeneralGame]:
    return []
