"""Number platform."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from SolixBLE import C1000, F2000, SolixBLEDevice

_LOGGER = logging.getLogger(__name__)


if TYPE_CHECKING:
    from . import SolixBLEConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SolixBLEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the numbers."""

    device = config_entry.runtime_data
    numbers: list[SolixNumberEntity] = []

    # Support for AC charging power limit
    if type(device) in [C1000, F2000]:
        numbers.append(
            SolixNumberEntity(
                device,
                "AC Charging Power Limit",
                "ac_charging_power",
                "ac_charging_power",
                "set_ac_charging_power",
                min_value=100,
                max_value=1440,
                step=10,
                unit="W",
            )
        )

    async_add_entities(numbers)


class SolixNumberEntity(NumberEntity):
    """Representation of a device."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        device: SolixBLEDevice,
        name: str,
        attribute: str,
        state_attribute: str,
        set_function_attribute: str,
        min_value: float,
        max_value: float,
        step: float,
        unit: str | None = None,
    ) -> None:
        """Initialize the device object. Does not connect.

        :param device: The device API object.
        :param name: Name of the number entity.
        :param attribute: Attribute used in unique ID generation.
        :param state_attribute: Name of property in API object to determine state.
        :param set_function_attribute: Name of function in API object to set the value.
        :param min_value: Minimum settable value.
        :param max_value: Maximum settable value.
        :param step: Step size between settable values.
        :param unit: Unit of measurement, if any.
        """
        self._device = device
        self._address = device.address
        self._state_attribute = state_attribute
        self._set_function = getattr(device, set_function_attribute)

        self._attr_name = name
        self._attr_native_value = None
        self._attr_unique_id = f"{device.address}_{attribute}"
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_device_info = DeviceInfo(
            name=device.name,
            connections={(CONNECTION_BLUETOOTH, device.address)},
        )
        self._update_updatable_attributes()

    async def async_added_to_hass(self) -> None:
        """Run when this Entity has been added to HA."""
        self._device.add_callback(self._state_change_callback)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from HA."""
        self._device.remove_callback(self._state_change_callback)

    def _update_updatable_attributes(self) -> None:
        """Update this entities updatable attrs from the devices state."""
        self._attr_available = self._device.available

        try:
            state = getattr(self._device, self._state_attribute)
        except Exception:  # noqa: BLE001 - telemetry key absent from this push
            return

        # These settings are only reported on an explicit status poll. A passive
        # telemetry push (e.g. after toggling an output) replaces the cached
        # telemetry and omits them, making the property read -1. Keep the last
        # known value in that case instead of blanking the entity.
        if state != -1:
            self._attr_native_value = state

    def _state_change_callback(self) -> None:
        """Run when device informs of state update. Updates local properties."""
        _LOGGER.debug("Received state notification from device %s", self.name)
        self._update_updatable_attributes()
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        await self._set_function(int(value))
        # Reflect immediately; the device only echoes this back on an explicit
        # poll, which may be up to POLL_INTERVAL away.
        self._attr_native_value = value
        self.async_write_ha_state()
