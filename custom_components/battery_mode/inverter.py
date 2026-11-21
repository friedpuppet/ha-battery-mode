"""Raw register access to the Deye through ha-solarman's services."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .registers import READ_BLOCKS

SOLARMAN = "solarman"


class InverterError(Exception):
    """The inverter couldn't be read or written."""


class DeyeClient:
    """Reads and writes holding registers via ``solarman.read_holding_registers`` /
    ``solarman.write_multiple_registers`` (function 0x10; Deye ignores 0x06)."""

    def __init__(self, hass: HomeAssistant, device_id: str) -> None:
        self._hass = hass
        self._device_id = device_id

    async def read(self, address: int, count: int) -> dict[int, int]:
        try:
            response = await self._hass.services.async_call(
                SOLARMAN,
                "read_holding_registers",
                {"device": self._device_id, "address": address, "count": count},
                blocking=True,
                return_response=True,
            )
        except (HomeAssistantError, ValueError) as err:
            raise InverterError(f"read 0x{address:04X}×{count}: {err}") from err
        try:
            registers = {int(k): int(v) for k, v in (response or {}).items()}
        except (TypeError, ValueError) as err:
            raise InverterError(f"read 0x{address:04X}×{count}: bad response {response!r}") from err
        if any(address + i not in registers for i in range(count)):
            raise InverterError(f"read 0x{address:04X}×{count}: incomplete response")
        return registers

    async def read_all(self) -> dict[int, int]:
        registers: dict[int, int] = {}
        for address, count in READ_BLOCKS:
            registers.update(await self.read(address, count))
        return registers

    async def write(self, address: int, values: list[int]) -> None:
        try:
            await self._hass.services.async_call(
                SOLARMAN,
                "write_multiple_registers",
                {"device": self._device_id, "address": address, "values": values},
                blocking=True,
            )
        except (HomeAssistantError, ValueError) as err:
            raise InverterError(f"write 0x{address:04X} {values}: {err}") from err
