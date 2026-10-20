"""Async HTTP client for the NGBS iCON cloud (enzoldhazam.hu)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from aiohttp import ClientError, ClientSession, ClientTimeout, FormData

from .const import (
    ATTR_CE,
    ATTR_HC,
    ATTR_REQ,
    BASE_URL,
    LOGIN_PAGE_MARKER,
    REQUEST_TIMEOUT,
    WRITE_DELAY,
)

_TIMEOUT = ClientTimeout(total=REQUEST_TIMEOUT)

_LOGGER = logging.getLogger(__name__)

_TOKEN_RE = re.compile(
    r'<input[^>]*name=["\']token["\'][^>]*value=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_TOKEN_RE_ALT = re.compile(
    r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']token["\']',
    re.IGNORECASE,
)


class NgbsAuthError(Exception):
    """Raised when login fails or credentials are rejected."""


class NgbsHomeNotFoundError(Exception):
    """Raised when the configured Home ID is unknown."""


class NgbsWriteSuperseded(Exception):
    """A queued write was replaced by a newer value for the same attribute."""


class NgbsClientClosed(Exception):
    """The client was closed before a queued write could be sent."""


@dataclass
class Thermostat:
    """A thermostat as reported by the cloud."""

    id: str
    title: str
    hc: int | None
    temp: float
    req: float
    rh: float
    out: int
    ce: int
    tmin: float | None = None
    tmax: float | None = None
    dew: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Thermostat:
        """Build a Thermostat from a DP entry."""
        return cls(
            id=str(data["ID"]),
            title=str(data.get("title") or data["ID"]),
            hc=_optional_int(data.get("HC")),
            temp=float(data.get("TEMP", 0)),
            req=float(data.get("REQ", 0)),
            rh=float(data.get("RH", 0)),
            out=int(data.get("OUT", 0)),
            ce=int(data.get("CE", 0)),
            tmin=_optional_float(data.get("TMIN")),
            tmax=_optional_float(data.get("TMAX")),
            dew=_optional_float(data.get("DEW")),
            raw=data,
        )

    def apply(self, changes: dict[str, Any]) -> None:
        """Optimistically apply local changes before the next poll."""
        mapping = {
            "HC": "hc",
            "TEMP": "temp",
            "REQ": "req",
            "RH": "rh",
            "OUT": "out",
            "CE": "ce",
        }
        for key, value in changes.items():
            attr = mapping.get(key)
            if attr is not None:
                setattr(self, attr, value)
            self.raw[key] = value


@dataclass
class HomeState:
    """Controller + thermostat snapshot for one Home ID."""

    icon_id: str
    thermostats: dict[str, Thermostat]
    controller_cooling: bool | None = None
    switchover_mode: str | None = None
    switchover_master: str | None = None
    water_temp: float | None = None
    external_temp: float | None = None
    battery: str | None = None
    online: bool | None = None
    firmware: str | None = None
    name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def is_cooling(self, thermostat: Thermostat) -> bool:
        """Prefer per-thermostat HC; fall back to controller season."""
        if thermostat.hc is None:
            return self.controller_cooling is True
        return thermostat.hc == 1


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_login_page(body: Any) -> bool:
    return isinstance(body, str) and LOGIN_PAGE_MARKER in body


def _extract_token(html: str) -> str | None:
    match = _TOKEN_RE.search(html) or _TOKEN_RE_ALT.search(html)
    return match.group(1) if match else None


def _extract_phpsessid(set_cookie: str | None) -> str | None:
    if not set_cookie:
        return None
    # getall joins are handled by the caller; here parse one Set-Cookie value.
    if "PHPSESSID=" in set_cookie:
        start = set_cookie.index("PHPSESSID=")
        end = set_cookie.find(";", start)
        return set_cookie[start:] if end < 0 else set_cookie[start:end]
    return None


def _phpsessid_from_response(response: Any) -> str | None:
    """Pull PHPSESSID from Set-Cookie headers or the response cookie jar view."""
    try:
        for header in response.headers.getall("Set-Cookie", []):
            found = _extract_phpsessid(header)
            if found:
                return found
    except Exception:  # noqa: BLE001 — fall through to jar / single header
        found = _extract_phpsessid(response.headers.get("Set-Cookie"))
        if found:
            return found

    for cookie in response.cookies.values():
        if cookie.key == "PHPSESSID":
            return f"PHPSESSID={cookie.value}"
    return None


class NgbsClient:
    """Talks to the NGBS iCON cloud the same way the web UI does."""

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        icon_id: str,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self.icon_id = icon_id
        self._cookie: str | None = None
        self._queue: dict[str, dict[str, asyncio.Future[None]]] = {}
        self._pending_values: dict[str, dict[str, str]] = {}
        self._waiters: set[asyncio.Future[None]] = set()
        self._drain_task: asyncio.Task[None] | None = None
        self._draining = False
        self._closed = False
        self._login_lock = asyncio.Lock()

    @property
    def authenticated(self) -> bool:
        """Whether a session cookie is currently held."""
        return self._cookie is not None

    async def login(self) -> None:
        """Obtain a fresh PHPSESSID by scraping CSRF and posting credentials."""
        async with self._login_lock:
            try:
                async with self._session.get(
                    BASE_URL,
                    timeout=_TIMEOUT,
                    allow_redirects=True,
                ) as response:
                    html = await response.text()
                    phpsessid = _phpsessid_from_response(response)

                if not phpsessid:
                    raise NgbsAuthError("The server did not hand out a session cookie.")

                token = _extract_token(html)
                if not token:
                    raise NgbsAuthError("Could not find the CSRF token on the login page.")

                body = urlencode(
                    {
                        "username": self._username,
                        "password": self._password,
                        "token": token,
                        "x-email": "",
                    }
                )

                async with self._session.post(
                    BASE_URL,
                    data=body,
                    headers={
                        "Cookie": phpsessid,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    timeout=_TIMEOUT,
                    allow_redirects=True,
                ) as result:
                    result_body = await result.text()
                    # Prefer a rotated session cookie if the server issues one.
                    phpsessid = _phpsessid_from_response(result) or phpsessid

                if _is_login_page(result_body):
                    raise NgbsAuthError("Invalid username or password.")

                self._cookie = phpsessid
                _LOGGER.debug("Logged in to NGBS iCON cloud")
            except NgbsAuthError:
                raise
            except (ClientError, asyncio.TimeoutError) as err:
                raise NgbsAuthError(f"Failed to log in: {err}") from err

    async def ensure_login(self) -> None:
        """Login if we do not yet hold a session."""
        if not self.authenticated:
            await self.login()

    async def get_home(self) -> HomeState:
        """Poll iconList and return the configured home's state.

        Raises NgbsAuthError when the session expired (login page).
        Raises NgbsHomeNotFoundError when icon_id is unknown.
        """
        await self.ensure_login()
        assert self._cookie is not None

        try:
            async with self._session.get(
                f"{BASE_URL}/Ax",
                params={"action": "iconList"},
                headers={"Cookie": self._cookie},
                timeout=_TIMEOUT,
            ) as response:
                text = await response.text()
                if _is_login_page(text):
                    self._cookie = None
                    raise NgbsAuthError("Session expired.")
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as err:
                    raise ConnectionError(
                        "Unexpected non-JSON response from iconList"
                    ) from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise ConnectionError(f"Failed to fetch icon list: {err}") from err

        if isinstance(data, str) and _is_login_page(data):
            self._cookie = None
            raise NgbsAuthError("Session expired.")

        home = (data or {}).get("ICONS", {}).get(self.icon_id)
        if home is None:
            raise NgbsHomeNotFoundError(
                f"Home ID '{self.icon_id}' was not found on this account."
            )

        thermostats = {
            device.id: device
            for device in (Thermostat.from_api(entry) for entry in home.get("DP") or [])
        }

        hc = home.get("HC")
        state = HomeState(
            icon_id=self.icon_id,
            thermostats=thermostats,
            controller_cooling=None if hc is None else int(hc) == 1,
            switchover_mode=home.get("HC_SWITCH_MODE"),
            switchover_master=home.get("HC_MASTERICON"),
            water_temp=_optional_float(home.get("WTEMP")),
            external_temp=_optional_float(home.get("ETEMP")),
            battery=home.get("BATT"),
            online=_optional_int(home.get("ONLINE")),
            firmware=str(home["FIRMWARE"]) if home.get("FIRMWARE") is not None else None,
            name=home.get("NAME"),
            raw=home,
        )

        _LOGGER.debug(
            "season: HC=%s HC_SWITCH=%s HC_SW_VALUE=%s HC_SWITCH_MODE=%s "
            "HC_MASTERICON=%s HCMASTER=%s WTEMP=%s thermostats=%s",
            home.get("HC"),
            home.get("HC_SWITCH"),
            home.get("HC_SW_VALUE"),
            home.get("HC_SWITCH_MODE"),
            home.get("HC_MASTERICON"),
            home.get("HCMASTER"),
            home.get("WTEMP"),
            [
                {"ID": t.id, "HC": t.hc, "OUT": t.out, "CE": t.ce}
                for t in thermostats.values()
            ],
        )
        return state

    async def set_attr(self, device_id: str, attr: str, value: str) -> None:
        """Queue an attribute write (debounced, coalesced per device/attr).

        Resolves once the value has been sent. If a newer value for the same
        attribute replaces this one before it is sent, raises
        ``NgbsWriteSuperseded`` so callers do not treat the dropped write as
        successful.
        """
        if self._closed:
            raise NgbsClientClosed("NGBS client closed")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()

        attrs = self._queue.setdefault(device_id, {})
        values = self._pending_values.setdefault(device_id, {})

        previous = attrs.get(attr)
        if previous is not None and not previous.done():
            previous.set_exception(NgbsWriteSuperseded())

        attrs[attr] = future
        values[attr] = value
        self._waiters.add(future)
        self._schedule_drain()
        try:
            await future
        finally:
            self._waiters.discard(future)

    async def set_temperature(self, device_id: str, temperature: float) -> None:
        """Set target temperature (half-degree steps) and leave Eco if needed."""
        rounded = round(temperature / 0.5) * 0.5
        await asyncio.gather(
            self.set_attr(device_id, ATTR_CE, "0"),
            self.set_attr(device_id, ATTR_REQ, _format_temp(rounded)),
        )

    async def set_eco(self, device_id: str, eco: bool) -> None:
        """Enable (True) or disable Eco mode. Eco stands in for Off."""
        await self.set_attr(device_id, ATTR_CE, "1" if eco else "0")

    async def set_season(self, device_id: str, cooling: bool) -> None:
        """Request heating (False) or cooling (True) season via HC."""
        await self.set_attr(device_id, ATTR_HC, "1" if cooling else "0")

    def _schedule_drain(self) -> None:
        if self._drain_task is None and not self._draining:
            self._drain_task = asyncio.create_task(self._delayed_drain())

    async def _delayed_drain(self) -> None:
        try:
            await asyncio.sleep(WRITE_DELAY)
            await self._drain()
        finally:
            self._drain_task = None

    async def _drain(self) -> None:
        self._draining = True
        try:
            while self._queue:
                device_id, attrs = next(iter(self._queue.items()))
                values = self._pending_values.pop(device_id, {})
                del self._queue[device_id]

                for attr, future in list(attrs.items()):
                    value = values.get(attr)
                    if value is None:
                        if not future.done():
                            future.set_result(None)
                        continue
                    try:
                        await self._send_attr(device_id, attr, value)
                        if not future.done():
                            future.set_result(None)
                    except Exception as err:  # noqa: BLE001 — propagate to waiter
                        if not future.done():
                            future.set_exception(err)
        finally:
            self._draining = False

    async def _send_attr(self, device_id: str, attr: str, value: str) -> None:
        await self.ensure_login()
        assert self._cookie is not None

        form = FormData()
        form.add_field("action", "setThermostat")
        form.add_field("icon", self.icon_id)
        form.add_field("thermostat", device_id)
        form.add_field("attr", attr)
        form.add_field("value", value)

        try:
            async with self._session.post(
                f"{BASE_URL}/Ax",
                data=form,
                headers={"Cookie": self._cookie},
                timeout=_TIMEOUT,
            ) as response:
                text = await response.text()
                if _is_login_page(text):
                    self._cookie = None
                    raise NgbsAuthError("Session expired while writing.")
                response.raise_for_status()
        except (ClientError, asyncio.TimeoutError) as err:
            raise ConnectionError(
                f"Failed to set {attr}={value} on {device_id}: {err}"
            ) from err

        _LOGGER.debug("%s thermostat: Updated %s to %s", device_id, attr, value)

    async def async_close(self) -> None:
        """Cancel pending drain work and fail any waiting writers."""
        self._closed = True
        if self._drain_task is not None:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None

        self._queue.clear()
        self._pending_values.clear()
        closed = NgbsClientClosed("NGBS client closed")
        for future in list(self._waiters):
            if not future.done():
                future.set_exception(closed)


def _format_temp(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)
