# Home Assistant Zeptrion Air Integration

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=polar-gith&repository=ha-zeptrion-air-integration&category=integration)

> **This is an independently maintained continuation** of [alternize/ha-zeptrion-air-integration](https://github.com/alternize/ha-zeptrion-air-integration) by [@alternize](https://github.com/alternize), published by [@polar-gith](https://github.com/polar-gith). All credit for the original integration goes to alternize and its contributors. This repository has since diverged substantially (light platform, Lovelace card resource fixes and more) and is developed on its own from here on. See [Credits](#credits) below.

## Description

This is a custom integration for Home Assistant that allows you to control [Zeptrion Air](https://www.feller.ch/de/Produkte/Gebaeudeautomation/Zeptrion-Systeme/Zeptrion-Air) devices locally over your WLAN. Zeptrion Air hubs connect to your network and expose a REST API for controlling attached lights, blinds, and other devices. This integration aims to bring these devices into Home Assistant for automation and UI control.

## Features

*   **mDNS and DHCP Discovery:** Automatically discovers Zeptrion Air hubs (devices with hostnames starting `zapp-`) on your local network.
*   **Configuration Flow:** Allows setting up discovered devices and configuring options like step duration for blinds.
*   **Cover Control (Blinds/Markise):**
    *   Full support for Zeptrion channels controlling blinds/shutters (Category 5) and awnings/markise (Category 6).
    *   Standard Home Assistant cover controls: Open, Close, Stop.
    *   Custom services for fine-grained control:
        *   `zeptrion_air.blind_up_step`
        *   `zeptrion_air.blind_down_step`
        *   `zeptrion_air.blind_recall_s1`
        *   `zeptrion_air.blind_recall_s2`
        *   `zeptrion_air.blind_recall_s3`
        *   `zeptrion_air.blind_recall_s4`
*   **Light Control (Switches/Dimmers):** Channels of category 1 (on/off) and category 3 (dimmer) are exposed as Home Assistant `Light` entities. See [Light Entities (Switches & Dimmers)](#light-entities-switches--dimmers) below for details.
*   **Button Entities:** Provides button entities in Home Assistant for each of the custom cover services (Step Up/Down, S1-S4 scenes), making them easily accessible from the UI.
*   **Sensor Entities:** Exposes channel name, group, and Zeptrion icon ID as sensor entities (disabled by default). These can be enabled by the user if desired.
*   **Configurable Step Duration:** The duration (in milliseconds) for the "up step" and "down step" services can be configured via the integration's options flow.
*   **Wall keys as events (Smartfront):** If a Smartfront is fitted, the zApp gets one event entity "Wall keys" with an event type per key and action (`key_5_press`, `key_5_release`, ...), including Smartbuttons. See [Wall keys and Smartfront sensors](#wall-keys-and-smartfront-sensors).
*   **Smartfront sensors:** Temperature, and illuminance / humidity where the Smartfront has them, read every 60 s.
*   **Robust connection:** Push updates over the zApp's WebSocket with an HTTP liveness check, a resync after every reconnect and a grace period before entities turn unavailable. See [Connection handling](#connection-handling).
*   **DHCP discovery:** zApps are also found by their DHCP host name `zapp-*` (mDNS does not answer reliably on every device); a changed IP address is taken over automatically.
*   **Diagnostics:** "Download diagnostics" on a zApp gives connection statistics and device data with host, serial number and names redacted.
*   **Optional Light Polling:** Lights can additionally be polled every 60 s to correct on/off states missed on the websocket. Off by default; see [Options](#options).

## Light Entities (Switches & Dimmers)

Channels with category 1 (on/off) or category 3 (dimmer) are exposed as Home Assistant `Light` entities (`custom_components/zeptrion_air/light.py`).

**Color modes**

*   Category 1 (switch) channels use `ColorMode.ONOFF` - plain on/off.
*   Category 3 (dimmer) channels use `ColorMode.BRIGHTNESS` - on/off plus brightness.

**Turning on/off**

*   Switch channels send a plain on or off command to the hub.
*   Dimmer channels turned on without a requested brightness use the last non-zero brightness that was set (255 the first time).

**Brightness (dimmers only)**

*   The Zeptrion Air API has no "set brightness to X%" command. Instead, brightness is reached with a calibrated press-and-hold sequence, timed against a configured full-travel duration (`DIMMER_FULL_TRAVEL_MS`, 3.4 s).
*   Requested brightness is clamped to the 1-255 range.
*   `transition` is not supported; any transition time passed by Home Assistant is ignored.

**State updates are optimistic**

*   The hub reports `100` for a dimmer that is on, whatever its actual level, so Home Assistant keeps the last brightness value it set. Should a hub ever report an intermediate value (1-99), it is taken over as brightness; `0` means off.
*   After every command, the entity polls the channel's scan endpoint (`/zrap/chscan/ch{X}`) to refresh on/off state. For dimmers this poll is delayed (0.35 s plus a term proportional to how far the dimmer has to travel) to give the hub time to finish moving.
*   Optionally, every light can be polled every 60 s (`SCAN_INTERVAL`), with at most one request per hub at a time (`PARALLEL_UPDATES = 1`). This corrects on/off states that were missed on the websocket, e.g. after a reconnect. Polling is **off by default** and is enabled per hub via the option "Poll light states every 60 s" (see [Options](#options)).
*   The integration also listens to the hub's websocket push events and applies on/off changes reported from there in real time. Brightness changes made directly on the hub are not reflected, because the hub does not report them.

**Devices**

*   Each light channel is registered as its own Home Assistant device, linked to the parent hub via `via_device_id` (the hub's device-registry ID, replacing the deprecated `via_device` identifier tuple), with the device model shown as "Switch" or "Dimmer" depending on category. This also applies to hubs with a single channel.

## Wall keys and Smartfront sensors

A zApp with a Smartfront reports key presses on its WebSocket. The integration creates **one** event entity per zApp ("Wall keys"), not one per key, to keep the number of entities small:

*   Event types: `key_<n>_press` and `key_<n>_release` for every key in use (n = 1-8). Unused keys are left out.
*   Every event carries `key`, `function` (`channel`, `smartbutton` or `unknown`) and `code`; channel keys also `channels` and `direction` (`up` / `down`).
*   The key functions come from the Smartfront's `btfu` list (`/zapi/smartfront/id`). Smartbuttons (code `1000`) can trigger Home Assistant automations this way instead of only sending fixed HTTP requests.

Example trigger for key 5 being pressed:

```yaml
triggers:
  - trigger: state
    entity_id: event.zapp_21510211_wall_keys
    not_from: unavailable
conditions:
  - condition: state
    entity_id: event.zapp_21510211_wall_keys
    attribute: event_type
    state: key_5_press
```

The Smartfront temperature measures the module itself and therefore reads several degrees above room temperature.

## Connection handling

The zApp pushes channel changes over the WebSocket `ws://<host>/zrap/ws`. It sends nothing while idle and does not answer WebSocket pings, so:

*   After 60 s without a message, a cheap `GET /zrap/rssi` checks that the zApp still answers. If it does not, the connection is dropped and rebuilt.
*   Reconnects use exponential backoff from 1 s to 60 s with +/-20 % jitter.
*   After every (re)connect all channel states are read once (`/zrap/chscan`), so a change made during the gap is not lost.
*   Entities only become unavailable after 60 s without a connection. Short drops are logged at debug level; a longer outage logs one warning and one info message when the zApp is back.

HTTP requests follow the device's rules: reads are retried, commands are sent exactly once (a repeated `move_open_<ms>` or `dim_down_<ms>` would act twice), the 302 answer to a command is not followed, and at most two requests run against a zApp at a time.

## Dashboard card resource

The integration serves its blinds card at `/api/zeptrion_air/zeptrion-air-blinds-card.js` and registers it once as a Lovelace resource (storage mode only). Earlier versions could add the resource again on every Home Assistant start, because the resource list had not been loaded yet when it was checked. The resource list is now loaded first, the resource is added only when missing, and duplicates left behind by earlier versions are removed automatically on the next start.

## Options

Each hub (config entry) has its own options: **Settings > Devices & services > Zeptrion Air > (hub) > Configure**. Saving the options reloads that hub.

| Option | Key | Default | Description |
|---|---|---|---|
| Blind step duration (ms) | `step_duration_ms` | 250 | Duration of a single up/down step for blinds (100-32000 ms). |
| Poll light states every 60 s | `light_polling` | off | When on, every light channel of this hub is read from `/zrap/chscan/ch{X}` every 60 s to correct on/off states missed on the websocket. Dimmers keep their last brightness, because the hub reports `100` for any dimmed level. Load is small: one short HTTP request per light channel and minute. |

Light polling usually isn't needed: the hub pushes on/off changes over the websocket, and the state is re-read after every command. Turn it on for hubs whose websocket connection drops regularly.

## Current Limitations

*   **Dimmer Brightness is Optimistic:** see [Light Entities (Switches & Dimmers)](#light-entities-switches--dimmers) above.
*   **No Position Feedback for Covers:** The Zeptrion Air API does not provide real-time position feedback for blinds/covers. Therefore, the Home Assistant cover entities will not display the current position (e.g., 50% open). Status updates are optimistic based on commands sent.
*   **Wall keys need a Smartfront:** Without a Smartfront the zApp reports no key presses.
*   **Sensors are Static:** The sensor entities for channel name, group, and icon ID are populated once during setup and do not update dynamically if changed on the Zeptrion device itself (though these are typically static).

## Installation

**Requirements:** Home Assistant 2026.8.0 or newer (needed for `via_device_id`, which is used to link light and cover devices to their hub).

This repository is not listed in the default HACS store - add it as a **custom repository**:

1.  In Home Assistant, open **HACS**, then **Integrations**, then the three-dot menu (top right), then **Custom repositories**.
2.  Add `https://github.com/polar-gith/ha-zeptrion-air-integration` with category **Integration**.
3.  Install "Zeptrion Air Integration" from HACS as usual.

## Technical Details: API Usage

This integration interacts with the Zeptrion Air hub's local REST API using the following endpoints:

*   **GET `/zrap/id`**: Used to retrieve device identification information, including hardware version, serial number, system type, and software version. This is crucial for device setup and identification.
*   **GET `/zrap/chdes`**: Fetches descriptions for all available channels on the hub. This provides the channel name, assigned group, Zeptrion icon ID, and category (`cat`) which determines the type of device attached (e.g., blind, light).
*   **POST `/zrap/chctrl/ch{X}`** (with URL-encoded `cmd` parameter): Used to send control commands to a specific channel (`ch{X}`).
    *   `cmd=open`: Fully opens the cover.
    *   `cmd=close`: Fully closes the cover.
    *   `cmd=stop`: Stops any ongoing cover movement.
    *   `cmd=move_open_{time_ms}`: Moves the cover up for a specified duration in milliseconds.
    *   `cmd=move_close_{time_ms}`: Moves the cover down for a specified duration in milliseconds.
    *   `cmd=recall_s1` / `recall_s2` / `recall_s3` / `recall_s4`: Recalls a pre-programmed scene (position) for the cover.
    *   *(The API also supports `cmd=on`, `cmd=off`, and `cmd=dim val={Y}` for light channels; these are used by the `Light` entities described above.)*
*   **GET `/zrap/chscan` / `/zrap/chscan/ch{X}`**: Channel states. Lights report `0` / `100`; covers report `100` while the motor runs and `0` when it stands (firmware 01.08.28), never a position. Read after every reconnect and, for lights, after commands.
*   **GET `/zrap/rssi`**: Wi-Fi signal, also used as liveness check.
*   **GET `/zapi/smartfront/id`** and **`/zapi/smartfront/sensors`**: Smartfront key functions (`btfu`) and sensor values; `404` when no Smartfront is fitted.
*   **WebSocket `/zrap/ws`**: `eid1` frames for channel changes, `eid2` frames for key states.
*   `/zrap/net` is never read as a whole: in access-point mode it contains the Wi-Fi password.

The protocol notes in [diaznet/zeptrionair-api, `docs/protocol.md`](https://github.com/diaznet/zeptrionair-api/blob/main/docs/protocol.md) are the most complete description of these interfaces.

## Icon

The integration ships its own brand images in `custom_components/zeptrion_air/brand/` (`icon.png`, `logo.png` and their `@2x` variants). Home Assistant 2026.3 and newer shows them for the integration. The Feller logo is a trademark of Feller AG and is used only to identify the supported devices.

## Versioning

Starting with 2026.11.0, versions follow the Home Assistant scheme `YEAR.MONTH.PATCH`: the first release in a month is `YYYY.M.0`, fixes in the same month count up the last number. Releases are tagged `vYYYY.M.PATCH`; HACS installs from these releases.

## Contributing

Contributions to this integration are very welcome! If you have ideas for improvements, new features, or bug fixes, please feel free to:

1.  Open an issue on the GitHub repository to discuss your proposed changes.
2.  Submit a Pull Request with your contributions.

Please try to follow Home Assistant's development guidelines and ensure code is well-tested and documented.

## Credits

Thanks to [**Jeremy Diaz (diaznet)**](https://github.com/diaznet) for [zeptrionair-api](https://github.com/diaznet/zeptrionair-api) and [ha-zeptrionair](https://github.com/diaznet/ha-zeptrionair) (MIT). His measurements on real zApps (3340-2-B, firmware 01.08.28) and his protocol documentation are the basis of the connection handling, the safe HTTP command rules, the Smartfront key decoding (`btfu`, `eid2`), the Smartfront sensors and DHCP discovery in this integration since 2026.11.3. Ideas and code adapted from his work are marked in the source.

This project is based on the original work by [**alternize**](https://github.com/alternize) at [alternize/ha-zeptrion-air-integration](https://github.com/alternize/ha-zeptrion-air-integration). It is maintained by [**polar-gith**](https://github.com/polar-gith) and has since grown well beyond the original, so it is developed independently from here on. Please file issues and feature requests for this version here.

## Disclaimer & License

"Home Assistant" is a project of the Open Home Foundation. This project is not affiliated with nor endorsed by the Open Home Foundation. 

"Zeptrion", "Zeptrion Air" and the Zeptrion Air logo are property of & (c) by Feller AG 2016. This project is not affiliated with nor endorsed by Feller AG.

All other files in this project are licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
