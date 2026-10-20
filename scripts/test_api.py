#!/usr/bin/env python3
"""Manual API smoke test against enzoldhazam.hu (no Home Assistant required).

Usage:
  python3 scripts/test_api.py --username USER --password PASS --icon-id HOMEID

Or set NGBS_USERNAME / NGBS_PASSWORD / NGBS_ICON_ID env vars.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path

from aiohttp import ClientSession, DummyCookieJar

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "ngbs_icon"


def _load_client():
    """Load client.py without importing Home Assistant via package __init__."""
    pkg = types.ModuleType("ngbs_icon")
    pkg.__path__ = [str(PKG)]
    sys.modules["ngbs_icon"] = pkg

    def load(name: str, file: Path):
        spec = importlib.util.spec_from_file_location(name, file)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    load("ngbs_icon.const", PKG / "const.py")
    return load("ngbs_icon.client", PKG / "client.py")


_client_mod = _load_client()
NgbsClient = _client_mod.NgbsClient


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=os.environ.get("NGBS_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("NGBS_PASSWORD"))
    parser.add_argument("--icon-id", default=os.environ.get("NGBS_ICON_ID"))
    parser.add_argument(
        "--set-temp",
        type=float,
        default=None,
        help="Optional: set temperature on the first thermostat (read-only if omitted)",
    )
    args = parser.parse_args()

    if not args.username or not args.password:
        parser.error("username and password are required")

    async with ClientSession(cookie_jar=DummyCookieJar()) as session:
        client = NgbsClient(
            session, args.username, args.password, args.icon_id or "0"
        )
        print("Logging in…")
        await client.login()
        print("OK — session cookie acquired")

        if not args.icon_id:
            import json

            assert client._cookie
            async with session.get(
                f"{_client_mod.BASE_URL}/Ax",
                params={"action": "iconList"},
                headers={"Cookie": client._cookie},
                timeout=_client_mod._TIMEOUT,
            ) as resp:
                icons = (json.loads(await resp.text()).get("ICONS") or {})
            if not icons:
                print("No homes found on this account.")
                return 1
            if len(icons) == 1:
                args.icon_id = next(iter(icons))
                print(f"Auto-selected Home ID {args.icon_id}")
            else:
                print("Multiple homes found; pass --icon-id:")
                for icon_id, home in icons.items():
                    print(
                        f"  {icon_id}  name={home.get('NAME')!r} "
                        f"rooms={len(home.get('DP') or [])}"
                    )
                return 1
            client.icon_id = args.icon_id

        print("Fetching iconList…")
        home = await client.get_home()
        print(
            f"Home: name={home.name!r} firmware={home.firmware} "
            f"online={home.online} WTEMP={home.water_temp} ETEMP={home.external_temp} "
            f"BATT={home.battery!r} season_cooling={home.controller_cooling} "
            f"switch_mode={home.switchover_mode!r}"
        )
        for thermostat in home.thermostats.values():
            print(
                f"  {thermostat.id:6} {thermostat.title!r:20} "
                f"TEMP={thermostat.temp} REQ={thermostat.req} RH={thermostat.rh} "
                f"OUT={thermostat.out} CE={thermostat.ce} HC={thermostat.hc} "
                f"TMIN={thermostat.tmin} TMAX={thermostat.tmax}"
            )

        if args.set_temp is not None:
            first = next(iter(home.thermostats))
            print(f"Setting {first} target to {args.set_temp}…")
            await client.set_temperature(first, args.set_temp)
            print("Write queued/sent. Re-fetching…")
            await asyncio.sleep(1)
            home = await client.get_home()
            updated = home.thermostats[first]
            print(f"  {first}: REQ={updated.req} CE={updated.ce}")

        await client.async_close()

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
