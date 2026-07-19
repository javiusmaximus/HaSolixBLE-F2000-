"""SolixBLE integration."""

import logging
from datetime import timedelta

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_scanner_count,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval
from SolixBLE import (
    C300,
    C300DC,
    C800,
    C1000,
    C1000G2,
    F2000,
    F3800,
    Generic,
    PrimeCharger160w,
    PrimeCharger250w,
    PrimePowerBank20k,
    Solarbank2,
    SolixBLEDevice,
)

from .const import Models

_LOGGER = logging.getLogger(__name__)

# Interval for actively polling devices for state that is not reliably pushed
# over passive telemetry (see the status-poll setup in async_setup_entry).
POLL_INTERVAL = timedelta(seconds=60)

type SolixBLEConfigEntry = ConfigEntry[SolixBLEDevice]


def get_power_station_class(model: Models) -> SolixBLEDevice:
    """Return correct class for power station from model."""

    if model is Models.C300:
        return C300
    elif model is Models.C300DC:
        return C300DC
    elif model is Models.C800:
        return C800
    elif model is Models.C1000:
        return C1000
    elif model is Models.C1000G2:
        return C1000G2
    elif model is Models.F2000:
        return F2000
    elif model is Models.F3800:
        return F3800
    elif model is Models.PRIME_CHARGER_160:
        return PrimeCharger160w
    elif model is Models.PRIME_CHARGER_250:
        return PrimeCharger250w
    elif model is Models.PRIME_POWER_BANK_20K:
        return PrimePowerBank20k
    elif model is Models.SOLARBANK_2:
        return Solarbank2
    elif model is Models.UNKNOWN:
        return Generic
    else:
        raise NotImplementedError(f"Unexpected model. Got: '{type(model)}'!")


async def async_setup_entry(hass: HomeAssistant, entry: SolixBLEConfigEntry) -> bool:
    """Set up the integration from a config entry."""

    assert entry.unique_id is not None
    address = entry.unique_id.upper()
    model = Models(entry.data["model"])

    ble_device = async_ble_device_from_address(hass, address, connectable=True)

    if ble_device is None:
        count_scanners = async_scanner_count(hass, connectable=True)
        _LOGGER.debug("Count of BLE scanners: %i", count_scanners)

        if count_scanners < 1:
            raise ConfigEntryNotReady(
                "No Bluetooth scanners are available to search for the device."
            )
        raise ConfigEntryNotReady("The device was not found.")

    DeviceClass = get_power_station_class(model)
    if model is Models.UNKNOWN:
        _LOGGER.warning(
            f"The device '{ble_device.name}' is not supported and values will not be available to Home Assistant! "
            f"However when the integration is in debug mode the raw telemetry data and differences between status "
            f"updates will be printed in the log and this can be used to aid in adding support for new devices."
        )

    device = DeviceClass(ble_device)
    try:
        await device.connect()
    except Exception as e:
        raise ConfigEntryNotReady(
            "Unexpected exception when connecting to device."
        ) from e

    if not device.connected:
        raise ConfigEntryNotReady("Device found but unable to connect.")

    if not device.negotiated:
        raise ConfigEntryNotReady(
            "Device connected but failed to negotiate encryption."
        )

    entry.runtime_data = device

    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform.SENSOR, Platform.SWITCH, Platform.SELECT, Platform.NUMBER]
    )

    # Some devices (e.g. F2000, C1000) do not push every state change over
    # passive telemetry - certain settings, and even physical on/off changes
    # made on the unit, are only reflected when the device is explicitly
    # polled. For any device that supports an explicit status poll, poll
    # periodically so Home Assistant reflects live device state instead of only
    # the last value commanded from HA.
    if hasattr(device, "get_status_update"):
        poll_state = {"running": False}

        async def _async_poll_status(_now) -> None:
            # Skip if a previous poll is still in flight (should not happen with
            # the interval well above the per-poll timeout, but be safe).
            if poll_state["running"]:
                return
            poll_state["running"] = True
            try:
                await device.get_status_update()
            except Exception as e:  # noqa: BLE001 - a failed poll must not raise
                _LOGGER.debug("Scheduled status poll failed: %s", e)
            finally:
                poll_state["running"] = False

        entry.async_on_unload(
            async_track_time_interval(hass, _async_poll_status, POLL_INTERVAL)
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolixBLEConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok_sensor = await hass.config_entries.async_forward_entry_unload(
        entry, Platform.SENSOR
    )
    unload_ok_switch = await hass.config_entries.async_forward_entry_unload(
        entry, Platform.SWITCH
    )
    unload_ok_select = await hass.config_entries.async_forward_entry_unload(
        entry, Platform.SELECT
    )
    unload_ok_number = await hass.config_entries.async_forward_entry_unload(
        entry, Platform.NUMBER
    )

    await entry.runtime_data.disconnect()

    entry.runtime_data = None

    return (
        unload_ok_sensor and unload_ok_switch and unload_ok_select and unload_ok_number
    )
