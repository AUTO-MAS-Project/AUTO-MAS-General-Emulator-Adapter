"""通用模拟器适配，不限定宿主操作系统。"""

from auto_mas_core import GameAdapterPlugin, GameTypeDecl

from .config import GeneralDevice, GeneralGame
from .control import Control, search


class Plugin(GameAdapterPlugin):
    game_type_key = "general_emulator"
    decl = GameTypeDecl(
        game_class=GeneralGame,
        device_class=GeneralDevice,
        control_class=Control,
        search=search,
    )

    async def on_enable(self):
        for entry in self.instances():
            await entry._control.refresh()

    async def on_disable(self):
        await self.on_teardown()

    async def on_teardown(self):
        for entry in self.instances():
            await entry._control.teardown()
