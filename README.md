# NGBS iCON Thermostat for Home Assistant

Home Assistant custom integration for [NGBS iCON](https://www.enzoldhazam.hu/app) room thermostats. It talks to the same cloud API as the manufacturer web UI ([enzoldhazam.hu](https://www.enzoldhazam.hu)).

Ported from the [homebridge-ngbs-icon-thermostat](https://github.com/peterrakolcza/homebridge-ngbs-icon-thermostat) plugin. Brand artwork from [ngbsh.hu](https://www.ngbsh.hu/hu/index.html).

## Features

- One **climate** entity per room (current temp, target temp, humidity)
- **Off** = Eco mode (same as the web UI and Homebridge plugin)
- **Heat** / **Cool** show the season the system is running
- Optional: ask the controller to switch season from a thermostat
- HVAC action from valve state: heating / cooling / idle / off
- Controller sensors: water temperature, outdoor temperature, battery status
- Per-room humidity and dew-point sensors
- UI setup (config flow), HACS-ready

Compatible with iCON 100 / 200 series (same cloud account as the web app).

## Requirements

- Home Assistant 2024.2 or newer (brand icons need **2026.3+**)
- [HACS](https://hacs.xyz/) (recommended) or manual install
- An [enzoldhazam.hu](https://www.enzoldhazam.hu) account and your Home ID

## Installation

### HACS (recommended)

1. HACS → **⋯** (three dots) → **Custom repositories**
2. Repository URL: this GitHub repo  
   Category: **Integration**
3. HACS → Integrations → search **NGBS iCON Thermostat** → **Download**
4. **Restart** Home Assistant
5. Settings → Devices & services → **Add integration** → **NGBS iCON Thermostat**

### Manual

1. Copy `custom_components/ngbs_icon` into your HA `config/custom_components/` folder
2. Restart Home Assistant
3. Add the integration as above

## Finding your Home ID

The Home ID (also called iCON ID) identifies which building/controller to control. One enzoldhazam.hu account can have more than one home.

1. Open the web app: [https://www.enzoldhazam.hu/app](https://www.enzoldhazam.hu/app)
2. Sign in with the same username and password you will use in Home Assistant
3. Open **System settings** (gear / system menu — the web UI is often in Hungarian)
4. Find **Home ID**
5. Copy the long numeric value (example shape: `500221478198`)

Use that exact number when adding the integration. If login works but setup says the Home ID was not found, double-check you copied the ID for the home you intend to control (not a name or serial number).

## Setup

When adding the integration you will be asked for:

| Field | Where to find it |
| --- | --- |
| **Username** | Email or username for [enzoldhazam.hu](https://www.enzoldhazam.hu) |
| **Password** | Account password |
| **Home ID (iCON ID)** | See [Finding your Home ID](#finding-your-home-id) above |
| **Allow switching between heating and cooling** | Optional — see below. Leave **off** unless your system lets thermostats choose the season |

After setup you get:

- A device for the controller (water / outdoor temp, battery)
- One climate device per room thermostat, plus humidity and dew-point sensors

Credentials are stored in Home Assistant’s config entry storage (same pattern as MQTT, UniFi, etc.). Protect your HA host and backups.

## Using the climate entities

| HA mode | What it means on NGBS |
| --- | --- |
| **Off** | Eco mode (`CE=1`) — no true hardware Off |
| **Heat** / **Cool** | Leave Eco; show (and optionally request) heating or cooling season |
| **Auto** | Treated as “on”; follows the system season |

| Attribute / action | Source |
| --- | --- |
| Current temperature | Measured room temp |
| Target temperature | Setpoint (0.5 °C steps) |
| Current humidity | Relative humidity |
| HVAC action | Valve open → heating/cooling; else idle or off |

### Behaviour notes

- Setting a target temperature while in Eco **leaves Eco** and resumes normal control.
- Most buildings switch season **centrally**. Heat/Cool then simply mean “on”; the entity keeps reporting the season the system is actually in.
- If your installation switches from the thermostat (web UI shows something like `termosztátról`), enable **Allow switching between heating and cooling** in the integration options. If the controller ignores the request, the integration logs a warning and falls back to following the system.
- Poll interval is **10 seconds** (one cloud request covers the whole home).

## Options

Settings → Devices & services → NGBS iCON → **Configure**:

- Toggle **Allow switching between heating and cooling** without re-adding the integration

## Troubleshooting

| Problem | What to try |
| --- | --- |
| Invalid username or password | Check login at [enzoldhazam.hu](https://www.enzoldhazam.hu) in a browser |
| Password changed / reauth required | HA will prompt to reauthenticate; update username/password without removing the integration |
| Home ID not found | Confirm Home ID under System settings; one account can have multiple homes |
| Entities unavailable / cloud errors | Cloud is sometimes slow; wait and check HA logs. Session renews hourly and on expiry |
| Heat/Cool seem swapped | Enable debug logging and look for the `season:` line (see below) |

### Debug logging

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.ngbs_icon: debug
```

Restart (or reload logger), then reproduce the issue. The `season:` debug line dumps what the controller reports about heating/cooling — useful when opening an issue.

## Development / API smoke test

Without Home Assistant, against a real account:

```bash
pip3 install aiohttp
export NGBS_USERNAME='...' NGBS_PASSWORD='...'   # optional: NGBS_ICON_ID='...'
python3 scripts/test_api.py
```

## License

Apache-2.0 — same as the Homebridge plugin this is based on.
