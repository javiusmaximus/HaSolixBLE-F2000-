"""Select platform."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from SolixBLE import C1000, F2000, DisplayTimeout, LightStatus, SolixBLEDevice

_LOGGER = logging.getLogger(__name__)


if TYPE_CHECKING:
    from . import SolixBLEConfigEntry

LIGHT_MODE_OPTIONS = {
    "Off": LightStatus.OFF,
    "Low": LightStatus.LOW,
    "Medium": LightStatus.MEDIUM,
    "High": LightStatus.HIGH,
    "SOS": LightStatus.SOS,
}

DISPLAY_BRIGHTNESS_OPTIONS = {
    "Low": LightStatus.LOW,
    "Medium": LightStatus.MEDIUM,
    "High": LightStatus.HIGH,
}

DISPLAY_TIMEOUT_OPTIONS = {
    "20 seconds": DisplayTimeout.S20,
    "30 seconds": DisplayTimeout.S30,
    "60 seconds": DisplayTimeout.S60,
    "5 minutes": DisplayTimeout.S300,
    "30 minutes": DisplayTimeout.S1800,
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SolixBLEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the selects."""

    device = config_entry.runtime_data
    selects: list[SolixSelectEntity] = []

    # Support for light mode select.
    # NOTE: This is a write-only control (state_attribute=None) driven purely by
    # the last commanded value. Neither model exposes a usable light-status
    # readback: the C1000 has no light property at all, and on the F2000 the
    # light telemetry key ('cf') is stuck reporting OFF regardless of state.
    if type(device) in [C1000, F2000]:
        selects.append(
            SolixSelectEntity(
                device,
                "Light Mode",
                "light_mode",
                None,
                LIGHT_MODE_OPTIONS,
                "set_light_mode",
            )
        )

    # Support for display brightness select
    if type(device) in [F2000]:
        selects.append(
            SolixSelectEntity(
                device,
                "Display Brightness",
                "display_brightness",
                "display_mode",
                DISPLAY_BRIGHTNESS_OPTIONS,
                "set_display_mode",
            )
        )

    # Support for display timeout select
    if type(device) in [F2000]:
        selects.append(
            SolixSelectEntity(
                device,
                "Display Timeout",
                "display_timeout",
                "display_timeout_seconds",
                DISPLAY_TIMEOUT_OPTIONS,
                "set_display_timeout",
                value_lookup=True,
            )
        )

    async_add_entities(selects)


class SolixSelectEntity(SelectEntity):
    """Representation of a device."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        device: SolixBLEDevice,
        name: str,
        attribute: str,
        state_attribute: str | None,
        options: dict[str, object],
        set_function_attribute: str,
        value_lookup: bool = False,
    ) -> None:
        """Initialize the device object. Does not connect.

        :param device: The device API object.
        :param name: Name of the select entity.
        :param attribute: Attribute used in unique ID generation.
        :param state_attribute: Name of property in API object to determine state,
            or None for a write-only control with no reliable readback.
        :param options: Mapping of displayed option name to the value it represents.
        :param set_function_attribute: Name of function in API object to set the option.
        :param value_lookup: If True, state_attribute holds the raw value (e.g. int
            seconds) to compare against options.values() rather than an object to
            compare by identity (e.g. an Enum member).
        """
        self._device = device
        self._address = device.address
        self._state_attribute = state_attribute
        self._options_map = options
        self._value_lookup = value_lookup
        self._set_function = getattr(device, set_function_attribute)

        self._attr_name = name
        self._attr_current_option = None
        self._attr_unique_id = f"{device.address}_{attribute}"
        self._attr_options = list(options.keys())
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

        # Write-only control: the device has no reliable readback for this
        # setting, so state is driven purely by the last commanded value.
        if self._state_attribute is None:
            return

        # The underlying telemetry key for these settings (e.g. display
        # brightness/timeout) is not present in every passive telemetry push;
        # it only arrives on an explicit status poll. When absent the property
        # reads as UNKNOWN/-1 (or, for ``light``, raises because the key is
        # missing). In that case keep the last known selection rather than
        # resetting the entity to an unknown state.
        try:
            state = getattr(self._device, self._state_attribute)
        except Exception:  # noqa: BLE001 - telemetry key absent from this push
            return

        current_option = None
        for option, value in self._options_map.items():
            comparison_value = value.value if self._value_lookup else value
            if state == comparison_value:
                current_option = option
                break

        # Only overwrite when we have a definitive match. A non-match means the
        # value was not in this telemetry update, not that it changed.
        if current_option is not None:
            self._attr_current_option = current_option

    def _state_change_callback(self) -> None:
        """Run when device informs of state update. Updates local properties."""
        _LOGGER.debug("Received state notification from device %s", self.name)
        self._update_updatable_attributes()
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self._set_function(self._options_map[option])
        # Reflect the change immediately; the device does not reliably echo
        # these settings back in passive telemetry.
        self._attr_current_option = option
        self.async_write_ha_state()
