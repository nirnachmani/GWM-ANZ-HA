# GWM ANZ for Home Assistant
[![HACS][hacs-badge]][hacs-url] [![release][release-badge]][release-url] [![license][license-badge]][license-url]

> **Beta / unsupported:** This is an unofficial beta integration, supplied as-is and without support.
>
> **Tested vehicle:** This was built for and tested only on a **GWM Tank 500 PHEV** using the Australia/New Zealand backend. It will likely need code changes for other GWM models, regions, or app/backend versions.

Control and monitor a GWM ANZ vehicle from Home Assistant. The integration talks directly to the GWM Australia/New Zealand cloud using the same style of account login, status polling, and remote-command APIs used by the official GWM app.

Remote commands affect a real vehicle. Test every command while the vehicle is parked, safe, and in view. Do not rely on automations until you have personally validated the behaviour on your own car.

## What You Get

Entities currently provided include:

- Sensors: battery SOC, electric range, fuel range, remaining fuel, odometer, charging status, remaining charging time, charge mode, tyre pressures, tyre temperatures, command status, update/acquisition timestamps, and last refresh time.
- Binary sensors: charging active, charge plug connected, lock open, A/C active, window open states, air circulation, front/rear demisting, and other live vehicle states where available.
- Device tracker: vehicle GPS location when returned by the GWM cloud.
- Climate: remote A/C on/off.
- Numbers: A/C target temperature, A/C run time, front/rear demist duration, seat levels, seat climate time, steering wheel heat time, and remote start time.
- Select: seat climate mode, where supported.
- Switches: charging start/stop, air circulation, front/rear demisting, seat climate, heated steering wheel, and remote vehicle start.
- Lock: lock and unlock vehicle doors.
- Buttons: close windows and close sunroof.

The integration also exposes a command status sensor because GWM remote commands are slow: the cloud accepts a command first, then the vehicle result is polled until it completes, fails, or times out.

## Installation

### HACS

[![Open the GWM ANZ HACS repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=nirnachmani&repository=GWM-ANZ-HA&category=integration)

This repository is intended to be installed as a HACS custom repository:

1. Open **HACS** in Home Assistant.
2. Open the menu and choose **Custom repositories**.
3. Add this repository URL:

```text
https://github.com/nirnachmani/GWM-ANZ-HA
```

4. Select category **Integration**.
5. Download **GWM ANZ**.
6. Restart Home Assistant.

### Manual installation

1. Copy this folder from the repository:

```text
custom_components/gwm_anz
```

2. Into your Home Assistant config directory:

```text
/config/custom_components/gwm_anz
```

3. Restart Home Assistant.
4. Add the integration from **Settings** -> **Devices & services** -> **Add integration** -> **GWM ANZ Cloud**.

## Authentication

During setup, enter:

- GWM ANZ account e-mail / username.
- GWM ANZ password.
- Country: `AU` or `NZ`.
- Optional remote command PIN/password from the official GWM app.
- Poll interval.

The integration generates a device id automatically and discovers the vehicle from the cloud. The encrypted cloud VIN is discovered automatically; you do not need to enter a VIN manually.

### First-login verification

GWM require a one-time verification code when the integration logs in as a new device. Setup will show a second form for the verification code. Check the e-mail/SMS destination associated with your GWM account and enter the latest code.

### Australia/New Zealand account sessions

The ANZ backend appears to permit only one active login session per account. Using the same account in Home Assistant and in the official GWM phone app can cause them to log each other out. A dedicated/shared vehicle account is recommended.

## Remote Commands

Remote commands are slower than normal Home Assistant switches because they go through the GWM cloud and then to the vehicle. After a command is sent, the **Command status** sensor may show states such as:

```text
queued <command-id>
waiting for vehicle result
completed - Success [0]
failed - <vehicle/cloud reason>
```

Avoid sending overlapping commands. If a command fails with a busy/in-progress style result, wait for the vehicle and cloud state to settle before trying again.

## Timers, Presets, and Commands

Several controls have a separate number entity for duration or preset value. Changing a number usually saves the next command setting; it does not by itself start the function.

- **A/C temperature** sets the staged remote A/C target temperature. The A/C switch/climate command uses that value when starting A/C.
- **Climate operation time** controls how long the next A/C command should run.
- **Seat levels** and **Seat climate mode** define the requested seat heat/ventilation settings. Turning on the seat climate switch sends those settings to the vehicle.
- **Seat climate time** controls how long the seat command should run.
- **Front/rear demisting duration** controls how long the next demist command should run.
- **Steering wheel time** controls how long the heated steering wheel command should run.
- **Start vehicle time** controls how long the remote vehicle-start command should run.


## Charging

The charging switch is only available when the car reports the charge plug/session as connected. 

The **Charging status** sensor may report:

- `disconnected`
- `connected`
- `charging`
- `awaiting_charging`
- `waiting_for_power`
- `error`

## Supported Vehicles

This integration is currently only known to work with the **GWM Tank 500 PHEV** on the Australia/New Zealand backend. Other GWM vehicles may expose different status codes, different command payloads, different capabilities, or different authentication behaviour.

Regional GWM services and vehicle firmware can differ. If you try this on another model, assume it will need modification and validate read-only sensors before attempting remote commands.

## Privacy And Safety

Your GWM credentials, tokens, and optional remote command PIN are stored by Home Assistant as part of this integration's config entry. Protect your Home Assistant instance and backups accordingly.

Remote commands can affect the real vehicle, including climate, charging, locks, demisting, windows/sunroof, seats, heated steering wheel, and vehicle start. Use them carefully.

## Disclaimer

This project is unofficial and is not affiliated with or endorsed by Great Wall Motor, GWM, Tank, ORA, Haval, or Home Assistant. Vehicle cloud APIs and remote command behavior may change without notice.

Use at your own risk. You are responsible for validating behavior, protecting credentials, keeping backups, and deciding whether remote commands are appropriate for your vehicle and environment.

## Special Thanks

Special thanks to [moryoav](https://github.com/moryoav) and the [moryoav/ha-gwm_ora](https://github.com/moryoav/ha-gwm_ora) project. This integration's structure, Home Assistant modelling, and README were inspired by that work.

Thanks also to [zivillian](https://github.com/zivillian) and [zivillian/ora2mqtt](https://github.com/zivillian/ora2mqtt), and to [ipsBruno](https://github.com/ipsBruno) and [ipsBruno/haval-h6-gwm-alexa-chatgpt-mqtt-integration](https://github.com/ipsBruno/haval-h6-gwm-alexa-chatgpt-mqtt-integration), for public GWM cloud/API examples that helped make this work easier.

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/nirnachmani/GWM-ANZ-HA?style=flat-square
[release-url]: https://github.com/nirnachmani/GWM-ANZ-HA/releases
[license-badge]: https://img.shields.io/github/license/nirnachmani/GWM-ANZ-HA?style=flat-square
[license-url]: https://github.com/nirnachmani/GWM-ANZ-HA/blob/main/LICENSE
