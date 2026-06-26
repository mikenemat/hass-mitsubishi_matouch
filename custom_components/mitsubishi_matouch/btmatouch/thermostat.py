"""Class representing a Mitsubishi MA Touch BLE thermostat."""

import logging
import asyncio
import time
from types import TracebackType
from typing import Self
from construct import StreamError

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from bleak_retry_connector import establish_connection

from ._structures import (
    _MAMessageHeader,
    _MAMessageFooter,
    _MARequest,
    _MAResponse,
    _MAAuthenticatedRequest,
    _MAStatusRequest,
    _MAStatusResponse,
    _MAControlRequest,
    _MAControlResponse,
)
from .const import (
    DEFAULT_MAX_CONNECT_RETRIES,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_RESPONSE_TIMEOUT,
    DEFAULT_KEEPALIVE_INTERVAL,
    MAOperationMode,
    _MACharacteristic,
    _MAMessageType,
    _MAResult,
    _MAOperationModeFlags,
    MAVaneMode,
    MAFanMode,
)
from .exceptions import (
    MAAlreadyAwaitingResponseException,
    MARequestException,
    MAConnectionException,
    MAInternalException,
    MAResponseException,
    MAControlRequestFailedException,
    MAAuthException,
    MAStateException,
    MATimeoutException,
)
from .models import Status

__all__ = ["Thermostat"]

_LOGGER = logging.getLogger(__name__)


class Thermostat:
    """Representation of a Mitsubishi MA Touch thermostat."""

    def __init__(
        self,
        pin: int,
        address: str,
        ble_device: BLEDevice | None = None,
        max_connect_retries: int = DEFAULT_MAX_CONNECT_RETRIES,
        command_timeout: int = DEFAULT_COMMAND_TIMEOUT,
        response_timeout: int = DEFAULT_RESPONSE_TIMEOUT,
        keepalive_interval: int = DEFAULT_KEEPALIVE_INTERVAL,
    ):
        """Initialize the thermostat.

        The thermostat will be in a disconnected state after initialization.

        Args:
            mac_address (str): The MAC address of the thermostat.
            pin (int): The PIN for accessing the thermostat (hex representation).
            connection_timeout (int, optional): The connection timeout in seconds. Defaults to DEFAULT_CONNECTION_TIMEOUT.
            command_timeout (int, optional): The command timeout in seconds. Defaults to DEFAULT_COMMAND_TIMEOUT.
            response_timeout (int, optional): The response waiting timeout in seconds. Defaults to DEFAULT_RESPONSE_TIMEOUT.
        """

        self._mac_address = address
        self._pin = pin
        self._ble_device = ble_device
        self._max_connect_retries = max_connect_retries
        self._command_timeout = command_timeout
        self._response_timeout = response_timeout
        self._keepalive_interval = keepalive_interval

        self._firmware_version: str | None = None
        self._software_version: str | None = None

        self._conn: BleakClient | None = None
        self._connection_lock = asyncio.Lock()
        self._gatt_lock = asyncio.Lock()
        self._response_future: asyncio.Future[bytes] | None = None

        self._message_id = 0
        self._receive_length = 0
        self._receive_buffer = bytes(0)

        self._connect_count = 0
        self._disconnect_count = 0
        self._connected_at: float | None = None
        self._last_disconnect_uptime: float | None = None
        self._last_activity = 0.0
        self._keepalive_task: asyncio.Task | None = None
        self._last_status_hex: str | None = None
        self._expected_response_id: int | None = None

    @property
    def is_connected(self) -> bool:
        """Check if the thermostat is connected.

        Returns:
            bool: True if connected, False otherwise.
        """

        if self._conn is None:
            return False

        return self._conn.is_connected

    @property
    def connected_source(self) -> str | None:
        """Source (proxy/adapter MAC) serving the live connection, if known.

        Read from the BLEDevice details, since the advertisement-based scanner
        lookups don't reflect a connected (silent) device's path.
        """

        if not self.is_connected or self._ble_device is None:
            return None
        details = getattr(self._ble_device, "details", None)
        if isinstance(details, dict):
            return details.get("source")
        return None

    @property
    def firmware_version(self) -> str | None:
        """Get the thermostat firmware version."""

        return self._firmware_version

    @property
    def software_version(self) -> str | None:
        """Get the thermostat software version."""

        return self._software_version

    @property
    def connect_count(self) -> int:
        """Total successful connects since instantiation."""

        return self._connect_count

    @property
    def disconnect_count(self) -> int:
        """Total disconnect events observed since instantiation."""

        return self._disconnect_count

    @property
    def connection_uptime(self) -> float | None:
        """Seconds the current connection has been alive, or None if down."""

        if self._connected_at is None or not self.is_connected:
            return None
        return time.monotonic() - self._connected_at

    @property
    def last_disconnect_uptime(self) -> float | None:
        """Uptime (s) of the connection that most recently dropped, if any."""

        return self._last_disconnect_uptime

    @property
    def last_status_hex(self) -> str | None:
        """Raw hex of the most recent STATUS response (full frame for analysis)."""

        return self._last_status_hex

    async def async_connect(self) -> None:
        """Connect to the thermostat.

        After connecting, the device data and status will be queried and stored.

        Raises:
            MAStateException: If the thermostat is already connected.
            MAConnectionException: If the connection fails.
            MATimeoutException: If the connection times out.
            MARequestException: If an error occurs while sending a command.
        """

        if self.is_connected:
            raise MAStateException("Already connected")

        if self._ble_device is None:
            raise MAConnectionException("No BLE device available yet")

        _LOGGER.debug("[%s] Connecting...", self._mac_address)

        # Start each connection with clean protocol state so a mid-frame
        # disconnect on the previous link can't corrupt reassembly here.
        self._message_id = 0
        self._receive_length = 0
        self._receive_buffer = bytes(0)
        self._response_future = None

        try:
            self._conn = await establish_connection(
                BleakClient,
                self._ble_device,
                self._mac_address,
                disconnected_callback=self._on_disconnected,
                max_attempts=self._max_connect_retries
            )

            _LOGGER.debug("[%s] Connected!", self._mac_address)

            self._connect_count += 1
            self._connected_at = time.monotonic()
            self._last_activity = time.monotonic()

            await self._conn.start_notify(
                _MACharacteristic.NOTIFY, self._on_message_received
            )

            if self._firmware_version is None or self._software_version is None:
                self._firmware_version = await self._async_read_char_str(_MACharacteristic.FIRMWARE_VERSION)
                self._software_version = await self._async_read_char_str(_MACharacteristic.SOFTWARE_VERSION)
                _LOGGER.debug("[%s] Firmware version: %s, software version: %s", self._mac_address, self._firmware_version, self._software_version)

            # Start keepalive only after the link is fully ready, so a failure
            # during setup never leaves an orphan task on a half-open client.
            self._cancel_keepalive()
            if self._keepalive_interval > 0:
                self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        except BleakError as ex:
            self._cancel_keepalive()
            raise MAConnectionException(f"Could not connect to the device: {ex}") from ex
        except TimeoutError as ex:
            self._cancel_keepalive()
            raise MATimeoutException("Timeout during connection attempt") from ex

    async def async_disconnect(self) -> None:
        """Disconnect from the thermostat.

        Before disconnection all pending futures will be cancelled.

        Raises:
            MAStateException: If the thermostat is not connected.
            MAConnectionException: If the disconnection fails.
            MATimeoutException: If the disconnection times out.
        """

        self._cancel_keepalive()

        if not self.is_connected:
            _LOGGER.warning("[%s] No need to disconnect - not connected", self._mac_address)
            return

        try:
            await self._conn.disconnect()
        except EOFError:
            pass
        except BleakError as ex:
            raise MAConnectionException("Could not disconnect from the device") from ex
        except TimeoutError as ex:
            raise MATimeoutException("Timeout during disconnection") from ex

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Update the BLEDevice used for (re)connection.

        HA refreshes the BLEDevice as new advertisements arrive (including via
        ESP32 proxies), so the coordinator hands us the freshest one before each
        connect to keep reconnection reliable.
        """

        self._ble_device = ble_device
        self._mac_address = ble_device.address

    async def async_ensure_connected(self) -> None:
        """Ensure a live, authenticated connection, reused across polls.

        Keeping one persistent connection per device (instead of reconnecting on
        every poll) is what lets the integration scale to several thermostats,
        ideally with the connections distributed across ESP32 Bluetooth proxies.
        """

        if self.is_connected:
            return

        async with self._connection_lock:
            if self.is_connected:
                return
            await self.async_connect()
            await self.async_login(pin=self._pin)

    async def async_close(self) -> None:
        """Best-effort logout and disconnect; safe when already disconnected.

        Used on unload and to drop a broken link so the next poll reconnects.
        """

        async with self._connection_lock:
            self._cancel_keepalive()
            conn = self._conn
            if conn is None:
                return
            try:
                if conn.is_connected:
                    try:
                        await self.async_logout(pin=self._pin)
                    except Exception:
                        pass
                    await conn.disconnect()
            except Exception as ex:
                _LOGGER.debug("[%s] Error during close: %s", self._mac_address, ex)
            finally:
                self._conn = None

    async def async_login(self, pin: int) -> None:
        """Authentication, etc via unknown messages.

        Raises:
            MAStateException: If the thermostat is not connected.
            MARequestException: If an error occurs while sending the command.
            MATimeoutException: If the command times out.
            MAAlreadyAwaitingResponseException: If a status command is already pending.
            MAAuthException: If the PIN is incorrect.
        """

        request = _MAAuthenticatedRequest(message_type=_MAMessageType.LOGIN_REQUEST, request_flag=0x01, pin=pin)
        await self._async_write_request(request)

        # not sure what this does yet, but seems to be required
        request = _MAAuthenticatedRequest(message_type=_MAMessageType.UNKNOWN_1, request_flag=0x01, pin=pin)
        await self._async_write_request(request)

        # not sure what this does yet, but seems to be required
        request = _MAAuthenticatedRequest(message_type=_MAMessageType.UNKNOWN_2, request_flag=0x01, pin=pin)
        await self._async_write_request(request)

    async def async_logout(self, pin: int) -> None:
        """Unknown messages at end of connection.

        Raises:
            MAStateException: If the thermostat is not connected.
            MARequestException: If an error occurs while sending the command.
            MATimeoutException: If the command times out.
            MAAlreadyAwaitingResponseException: If a status command is already pending.
            MAAuthException: If the PIN is incorrect.
        """

        # not sure what this does yet, but seems to be required
        request = _MAAuthenticatedRequest(message_type=_MAMessageType.UNKNOWN_3, request_flag=0x01, pin=pin)
        await self._async_write_request(request)

        # not sure what this does yet, but seems to be required
        request = _MAAuthenticatedRequest(message_type=_MAMessageType.UNKNOWN_4, request_flag=0x01, pin=pin)
        await self._async_write_request(request)

        # not sure what this does yet, but seems to be required
        request = _MAAuthenticatedRequest(message_type=_MAMessageType.UNKNOWN_5, request_flag=0x01, pin=pin)
        await self._async_write_request(request)

    async def async_get_status(self) -> Status:
        """Query the latest status.

        Returns:
            Status: The status.

        Raises:
            MAStateException: If the thermostat is not connected.
            MARequestException: If an error occurs while sending the command.
            MATimeoutException: If the command times out.
            MAAlreadyAwaitingResponseException: If a status command is already pending.
            MAResponseException: If the status update response was invalid.
        """

        request = _MAStatusRequest(message_type=_MAMessageType.STATUS_REQUEST, request_flag=0x00)
        response_bytes = await self._async_write_request(request)
        response = _MAStatusResponse.from_bytes(response_bytes)
        status = Status._from_struct(response)
        self._last_status_hex = response_bytes.hex()
        _LOGGER.debug("[%s] Status payload: %s", self._mac_address, response_bytes.hex())
        _LOGGER.debug("[%s] Status IN: %s", self._mac_address, vars(response))
        #_LOGGER.debug("[%s] Status OUT: %s", self._mac_address, vars(status))
        return status

    async def async_set_cool_setpoint(self, temperature: float) -> None:
        """Set the heating setpoint temperature.

        Temperatures are in degrees Celsius and specified in 0.5 degree increments.

        Args:
            temperature (float): The new target temperature in degrees Celsius.

        Raises:
            MAStateException: If the thermostat is not connected.
            MAAlreadyAwaitingResponseException: If a status command is already pending.
            MARequestException: If an error occurs during the command.
            MATimeoutException: If the command times out.
            MAResponseException: If the temperature is invalid.
        """

        await self._async_write_control_request(
            flags_b=0x01,
            cool_setpoint=temperature
        )

    async def async_set_heat_setpoint(self, temperature: float) -> None:
        """Set the heating setpoint temperature.

        Temperatures are in degrees Celsius and specified in 0.5 degree increments.

        Args:
            temperature (float): The new target temperature in degrees Celsius.

        Raises:
            MAStateException: If the thermostat is not connected.
            MAAlreadyAwaitingResponseException: If a status command is already pending.
            MARequestException: If an error occurs during the command.
            MATimeoutException: If the command times out.
            MAResponseException: If the temperature is invalid.
        """

        await self._async_write_control_request(
            flags_b=0x02,
            heat_setpoint=temperature
        )

    async def async_set_operation_mode(self, operation_mode: MAOperationMode) -> None:
        """Set the operation mode.

        Args:
            operation_mode (MAOperationMode): The new operation mode.

        Raises:
            MAStateException: If the thermostat is not connected.
            MAAlreadyAwaitingResponseException: If a status command is already pending.
            MARequestException: If an error occurs during the command.
            MATimeoutException: If the command times out.
            MAResponseException: If the operation mode is not supported.
        """

        match operation_mode:
            case MAOperationMode.OFF:
                await self._async_write_control_request(
                    flags_a=0x01,
                    operation_mode_flags=_MAOperationModeFlags.HEAT,
                )
            case _:
                await self._async_write_control_request(
                    flags_a=0x01,
                    operation_mode_flags=_MAOperationModeFlags.POWER|_MAOperationModeFlags.HEAT,
                )

        match operation_mode:
            case MAOperationMode.AUTO:
                await self._async_write_control_request(
                    flags_a=0x02,
                    operation_mode_flags=_MAOperationModeFlags.POWER|_MAOperationModeFlags.AUTO|_MAOperationModeFlags.HEAT|_MAOperationModeFlags.COOL|_MAOperationModeFlags.DRY,
                )
            case MAOperationMode.HEAT:
                await self._async_write_control_request(
                    flags_a=0x02,
                    operation_mode_flags=_MAOperationModeFlags.POWER|_MAOperationModeFlags.HEAT
                )
            case MAOperationMode.COOL:
                await self._async_write_control_request(
                    flags_a=0x02,
                    operation_mode_flags=_MAOperationModeFlags.POWER|_MAOperationModeFlags.COOL
                )
            case MAOperationMode.DRY:
                await self._async_write_control_request(
                    flags_a=0x02,
                    operation_mode_flags=_MAOperationModeFlags.POWER|_MAOperationModeFlags.HEAT|_MAOperationModeFlags.DRY
                )
            case MAOperationMode.FAN:
                await self._async_write_control_request(
                    flags_a=0x02,
                    operation_mode_flags=_MAOperationModeFlags.POWER|_MAOperationModeFlags.FAN
                )

    async def async_set_fan_mode(self, fan_mode: MAFanMode) -> None:
        """Set the fan mode.

        Args:
            fan_mode (MAFanMode): The new fan mode.

        Raises:
            MAStateException: If the thermostat is not connected.
            MAAlreadyAwaitingResponseException: If a status command is already pending.
            MARequestException: If an error occurs during the command.
            MATimeoutException: If the command times out.
            MAResponseException: If the fan_mode is invalid.
        """

        await self._async_write_control_request(
            flags_c=0x01,
            fan_mode=fan_mode
        )

    async def async_set_vane_mode(self, vane_mode: MAVaneMode) -> None:
        """Set the vane mode.

        Args:
            vane_mode (MAVaneMode): The new vane mode.

        Raises:
            MAStateException: If the thermostat is not connected.
            MAAlreadyAwaitingResponseException: If a status command is already pending.
            MARequestException: If an error occurs during the command.
            MATimeoutException: If the command times out.
            MAResponseException: If the vane_mode is invalid.
        """

        await self._async_write_control_request(
            flags_c=0x02,
            vane_mode=vane_mode
        )

    ### Internal ###

    async def __aenter__(self) -> Self:
        """Async context manager enter.

        Connects to the thermostat. After connecting, authentication will be performed.

        Raises:
            MAStateException: If the thermostat is already connected.
            MAConnectionException: If the connection fails.
            MATimeoutException: If the connection times out.
            MARequestException: If an error occurs while sending a command.
        """

        await self._connection_lock.acquire()

        try:
            await self.async_connect()
            await self.async_login(pin=self._pin)
        except Exception as ex:
            if self.is_connected:
                try:
                    await self.async_disconnect()
                except Exception:
                    pass
            self._connection_lock.release()
            raise ex

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Async context manager exit.

        Disconnects from the thermostat. Before disconnection all pending futures will be cancelled.

        Raises:
            MAStateException: If the thermostat is not connected.
            MAConnectionException: If the disconnection fails.
            MATimeoutException: If the disconnection times out.
        """

        try:
            if self.is_connected:
                if exc_value is not None: # ignore exceptions if we already have one coming
                    try:
                        await self.async_disconnect()
                    except Exception:
                        pass
                else:
                    await self.async_logout(pin=self._pin)
                    await self.async_disconnect()
        finally:
            self._connection_lock.release()

    async def _async_read_char_str(self, uuid: str) -> str:
        return "".join(map(chr, await self._async_read_char(uuid)))

    async def _async_read_char(self, uuid: str) -> bytearray:
        """Read a device characteristic.

        Args:
            uuid (str): The uuid of the characteristic to read

        Raises:
            MAStateException: If the thermostat is not connected.
            MARequestException: If an error occurs while sending the command.
            MATimeoutException: If the command times out.
        """

        if not self.is_connected:
            raise MAStateException("Cannot read char - not connected")

        async with self._gatt_lock:
            try:
                value = await self._conn.read_gatt_char(uuid)
                self._last_activity = time.monotonic()
                return value
            except BleakError as ex:
                raise MARequestException("Error during read") from ex
            except TimeoutError as ex:
                raise MATimeoutException("Timeout during read") from ex

    async def _async_write_request(self, request: _MARequest) -> bytes:
        """Write a request to the thermostat.

        Args:
            command (_MARequest): The request to write.

        Raises:
            MAStateException: If the thermostat is not connected.
            MARequestException: If an error occurs while sending the command.
            MATimeoutException: If the command times out.
        """

        _LOGGER.debug("[%s] _async_write_request() called with request: %s", self._mac_address, type(request).__name__)

        if not self.is_connected:
            raise MAStateException("Cannot write request - not connected")

        if self._response_future is not None:
            raise MAAlreadyAwaitingResponseException(
                "Already awaiting a command response"
            )

        # TODO: clean this up
        payload = request.to_bytes()
        message = _MAMessageHeader(length=(1 + len(payload) + 2), message_id=self._message_id).to_bytes()
        message += payload
        message += _MAMessageFooter(crc=self._crc_sum(message)).to_bytes()

        self._expected_response_id = self._message_id
        self._message_id = self._message_id + 1 if self._message_id < 0x07 else 0

        self._response_future = asyncio.Future()

        async with self._gatt_lock:
            try:
                for i in range(0, len(message), 20):
                    part = message[i:i+20]
                    _LOGGER.debug("[%s] SND: %s", self._mac_address, part.hex())
                    await self._conn.write_gatt_char(_MACharacteristic.WRITE, part, response=False)
                self._last_activity = time.monotonic()
            except BleakError as ex:
                self._response_future = None
                raise MARequestException(f"Error during request write: {ex}") from ex
            except TimeoutError as ex:
                self._response_future = None
                raise MATimeoutException("Timeout during request write") from ex

        try:
            response_bytes = await asyncio.wait_for(self._response_future, self._response_timeout)
            response_header = _MAResponse.from_bytes(response_bytes)
            if response_header.message_type != request.message_type & 0xff:
                raise MAResponseException(f"Incorrect response message type received: {response_header.message_type}")
            match response_header.result:
                case _MAResult.SUCCESS:
                    return response_bytes
                case _MAResult.IN_MENUS:
                    raise MAResponseException(f"Failure result received: {response_header.result} - thermostat in menus?")
                case _MAResult.BAD_PIN:
                    raise MAAuthException("Failure result received: Incorrect PIN?")
                case _MAResult.UNKNOWN_3_BAD_PIN:
                    raise MAAuthException("Failure result received: Incorrect PIN?")
                case _:
                    raise MAResponseException(f"Failure result received: {response_header.result}")
        except TimeoutError as ex:
            raise MATimeoutException("Timeout while awaiting response") from ex
        except StreamError as ex:
            raise MAResponseException(f"Failed to parse response header: {ex}") from ex
        finally:
            self._response_future = None

    async def _async_write_control_request(
        self,
        flags_a: int = 0,
        flags_b: int = 0,
        flags_c: int = 0,
        operation_mode_flags: _MAOperationModeFlags = _MAOperationModeFlags.NONE,
        cool_setpoint: float = 0,
        heat_setpoint: float = 0,
        fan_mode: MAFanMode = MAFanMode.NONE,
        vane_mode: MAVaneMode = MAVaneMode.NONE
    ) -> None:
        request = _MAControlRequest(
            message_type=_MAMessageType.CONTROL_REQUEST,
            request_flag=0x01,
            flags_a=flags_a,
            flags_b=flags_b,
            flags_c=flags_c,
            operation_mode_flags=operation_mode_flags,
            cool_setpoint=cool_setpoint,
            heat_setpoint=heat_setpoint,
            unknown_setpoint_1=0,
            unknown_setpoint_2=0,
            unknown_setpoint_3=0,
            vane_fan_mode=(vane_mode.value << 4) + (fan_mode.value >> 4)
        )

        response_bytes = await self._async_write_request(request)
        response = _MAControlResponse.from_bytes(response_bytes)
        
        if (response.unknown_1 != 0x01 or response.unknown_2 != 0x01):
            raise MAControlRequestFailedException(f"Control request failed: unknown_1={response.unknown_1}, unknown_2={response.unknown_2}")
        # TODO: do we need further checks here?

    def _crc_sum(self, frame: bytes) -> int:
        """Calculate frame CRC."""

        return sum(frame) & 0xff

    def _cancel_keepalive(self) -> None:
        """Stop the keepalive task if running."""

        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None

    async def _keepalive_loop(self) -> None:
        """Hold the connection open against the device's ~16s idle-disconnect.

        The MA Touch firmware drops idle BLE links at ~16s. A cheap characteristic
        read well inside that window keeps the link alive so polls don't each pay a
        full reconnect+login. Skips when a request is already in flight or there was
        recent GATT activity.
        """

        try:
            while self.is_connected:
                await asyncio.sleep(self._keepalive_interval)
                if not self.is_connected:
                    return
                if self._response_future is not None:
                    continue
                if (time.monotonic() - self._last_activity) < (self._keepalive_interval - 1):
                    continue
                try:
                    await self._async_read_char(_MACharacteristic.SOFTWARE_VERSION)
                    _LOGGER.debug("[%s] keepalive", self._mac_address)
                except Exception as ex:  # noqa: BLE001
                    _LOGGER.debug("[%s] keepalive read failed: %s", self._mac_address, ex)
                    return
        except asyncio.CancelledError:
            raise

    def _on_disconnected(self, client: BleakClient) -> None:
        """Handle disconnection from the thermostat."""

        # bleak can fire this for a previous client after a fast reconnect; ignore
        # stale callbacks so they don't clobber the live connection's uptime/state.
        if client is not self._conn:
            _LOGGER.debug("[%s] Ignoring stale disconnect callback", self._mac_address)
            return

        _LOGGER.debug("[%s] Disconnected.", self._mac_address)

        self._cancel_keepalive()
        self._disconnect_count += 1
        if self._connected_at is not None:
            self._last_disconnect_uptime = time.monotonic() - self._connected_at
        self._connected_at = None

        if self._response_future is not None and not self._response_future.done():
            exception = MAConnectionException("Connection closed while awaiting response")
            self._response_future.set_exception(exception)

    async def _on_message_received(self, _: BleakGATTCharacteristic, data: bytearray) -> None:
        """Handle received messages from the thermostat."""

        _LOGGER.debug("[%s] RCV: %s", self._mac_address, data.hex())

        # This runs inside a bleak notification callback, where raised exceptions
        # are swallowed and would otherwise hang the waiter for the full
        # response_timeout. Contain every parse error: reset reassembly state and
        # fail the pending future fast so the caller retries immediately.
        try:
            data_bytes = bytes(data)

            if self._receive_length == 0:
                if len(data_bytes) < 3:
                    raise MAResponseException(f"Runt frame ({len(data_bytes)} bytes)")
                header = _MAMessageHeader.from_bytes(data_bytes)
                if header.length > 64:
                    raise MAResponseException(f"Frame too long: {header.length}")
                self._receive_length = header.length
                self._receive_buffer = data_bytes[2:]
            else:
                self._receive_buffer += data_bytes

            if len(self._receive_buffer) < self._receive_length:
                return
            if len(self._receive_buffer) > self._receive_length:
                raise MAResponseException("Frame overflow")

            self._receive_length = 0
            response_id = self._receive_buffer[0] if self._receive_buffer else None
            payload = self._receive_buffer[1:-2]
            self._receive_buffer = bytes(0)

            # The controller does NOT echo our request id unchanged: it replies with
            # request_id | 0x08 (it sets bit 3), confirmed across all message types.
            # So a frame whose id != expected|0x08 is stale/out-of-order. Debug-only:
            # the single pending-response future below is what resolves the request,
            # so a mismatch here is informational, not an error (no per-poll spam).
            if (
                response_id is not None
                and self._expected_response_id is not None
                and response_id != (self._expected_response_id | 0x08)
            ):
                _LOGGER.debug(
                    "[%s] Unexpected response id %s (expected %s)",
                    self._mac_address, response_id, self._expected_response_id | 0x08,
                )

            # TODO: validate the trailing checksum

            if self._response_future is not None and not self._response_future.done():
                self._response_future.set_result(payload)
            else:
                _LOGGER.warning("[%s] Unsolicited message received, payload: %s", self._mac_address, payload.hex())
        except Exception as ex:  # noqa: BLE001 - must never escape the notify callback
            _LOGGER.warning("[%s] Error processing received frame: %s", self._mac_address, ex)
            self._receive_length = 0
            self._receive_buffer = bytes(0)
            if self._response_future is not None and not self._response_future.done():
                self._response_future.set_exception(MAResponseException(f"Frame processing error: {ex}"))
