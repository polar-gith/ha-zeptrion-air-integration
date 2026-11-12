# Home Assistant Zeptrion Air Integration

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=polar-gith&repository=ha-zeptrion-air-integration&category=integration)

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
        *   `zeptrion_air.blind_store_scene` (`scene: 1-4`): store the current position as scene S1-S4
        *   `zeptrion_air.blind_calibrate`: calibration run (measures the travel times)
*   **Light Control (Switches/Dimmers):** Channels of category 1 (on/off) and category 3 (dimmer) are exposed as Home Assistant `Light` entities. See [Light Entities (Switches & Dimmers)](#light-entities-switches--dimmers) below for details.
*   **Button Entities:** Provides button entities in Home Assistant for each of the custom cover services (Step Up/Down, S1-S4 scenes), making them easily accessible from the UI.
*   **Sensor Entities:** Exposes channel name, group, and Zeptrion icon ID as sensor entities (disabled by default). These can be enabled by the user if desired.
*   **Configurable Step Duration:** The duration (in milliseconds) for the "up step" and "down step" services can be configured via the integration's options flow.
*   **Wall keys as events (Smartfront):** If a Smartfront is fitted, the zApp gets one event entity "Wall keys" with an event type per key and action (`key_5_press`, `key_5_release`, ...), including Smartbuttons. See [Wall keys and Smartfront sensors](#wall-keys-and-smartfront-sensors).
*   **Smartfront sensors:** Temperature, and illuminance / humidity where the Smartfront has them, read every 60 s.
*   **Blinds with direction and position:** Opening/closing is shown live, also when moved at the wall; with travel times (entered or learned with the learning mode switch) the position is estimated and "set position" works.
*   **Robust connection:** Push updates over the zApp's WebSocket with an HTTP liveness check, a resync after every reconnect and a grace period before entities turn unavailable. See [Connection handling](#connection-handling).
*   **DHCP discovery:** zApps are also found by their DHCP host name `zapp-*` (mDNS does not answer reliably on every device); a changed IP address is taken over automatically.
*   **Diagnostics:** "Download diagnostics" on a zApp gives connection statistics and device data with host, serial number and names redacted.

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
*   Lights are not polled. After every reconnect of the event stream all channel states are read once, so changes made during a gap are corrected (this replaces the former option "Poll light states every 60 s", removed in 2026.11.5).
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

## Blinds: direction, position and learning mode

**Direction.** The zApp reports `100` while a blind's motor runs and `0` when it stands, without a direction. The direction comes from what started the movement: an open/close/step/position command from Home Assistant, or a Smartfront wall key (its function from `btfu`). A movement started by a scene or by another device has an unknown direction, unless the blind stands at a known end stop: then it can only move away from it (e.g. a scene recalled from fully closed moves up).

**End stops.** The relay of a blind channel stays on for its configured run time (about 60 s), also after the blind has stopped at its end. A movement that is not stopped by hand (Home Assistant stop, a wall key while moving) therefore ends at the end stop: the blind is shown as fully open or closed.

**Position (optional).** With the full travel time up and down configured for a blind, the integration estimates its position from the motor time, shows it while moving and supports "set position" (`move_open_<ms>` / `move_close_<ms>`; above 32 s it starts the movement and sends a stop). A movement reaching 95 % of the travel time snaps to the end. After a movement with unknown direction the position is unknown until the next full run. The last position survives a restart.

**Slats (venetian blinds).** With a slat tilt time set for a blind ("Channel n: slat tilt time (s)", typically 1-2 s; 0 = no slats, ignored), the first part of a run after a change of direction turns the slats instead of moving the height. The blind then also reports a tilt position (100 = slats open, turned by moving up; 0 = closed), supports "set tilt position" (use 0/100 to close/open the slats fully). The tilt buttons always turn the slats one step (option "step duration"). Position moves leave out the time the slats still have to turn. Measure the tilt time by reversing the blind and stopping it the moment the slats have turned.

**Travel times are taken over from the zApp automatically.** The zApp learns a blind's run time itself (it does a few learning runs after installation) and switches the motor off when the blind arrives at its end stop. A full run from one end stop to the other that nobody stopped therefore lasted exactly the travel time: the first such run sets an unknown travel time, and a known one is updated when two of the last five runs agree within 1 s and differ from it by more than 0.5 s. This needs no action; switch it off with the option "Take over blind travel times from the zApp".

Travel times can also be entered per blind in the hub's options ("Channel n: travel time up / down (s)"), or measured by hand with the **learning mode**, e.g. for devices whose run time is set longer than the travel:

1.  Switch on "Blind learning mode" on any zApp with blinds. The switches of all zApps are coupled; the mode switches itself off after 60 min.
2.  For every blind: close it fully and let it stop by itself; move it up and press stop the moment it arrives at the top; move it down and press stop when it arrives at the bottom. Do this twice. Wall keys and Home Assistant both work, in any order and across blinds.
3.  Switch learning mode off. A time is saved when at least two measurements agree within 1 s; a notification lists the results and blinds without a usable measurement.

If all blinds are the same (height, drive), switch on **"Blind learning: apply to all blinds"** (also coupled across zApps) before switching learning mode off: all measurements are then pooled and the agreed times are given to every blind without its own result, so measuring one blind twice is enough.

A measurement is a movement that started at a known end stop, went towards the other end and was stopped by hand; timed moves (steps, set position) never count. During learning, each blind shows its measurements in the `learning` attribute; `last_run` shows direction, duration and whether it was stopped by hand.

### Storing scenes S1-S4

Each blind has the buttons **Store S1 … Store S4** (under the device's *Configuration*), and the action `zeptrion_air.blind_store_scene` with `scene: 1-4`. They do the same as holding a scene key on the wall: the zApp stores the blind's current position (`store_s<n>`), which the wall keys and the *Scene S1-S4* buttons then recall. Stop the blind at the wanted position first; a moving blind is refused. Feller recommends to start from the top end stop (reference position) and set the slats before storing. Scenes cannot be read back from the zApp.

### Calibration run

After a blind or its motor was replaced, or when positions drift, press **Calibrate** (under the device's *Configuration*) or call `zeptrion_air.blind_calibrate`. The blind moves fully up (reference), fully down and fully up again; the two full runs, which the zApp ends at the end stops, are stored as the blind's travel times. It then returns to its previous position, if that was known, and a notification shows the result. Stopping or reversing the blind during the run aborts the calibration without changing anything. The zApp itself has no calibration function: its end positions are set on the motor, and stored scenes should be stored again afterwards.

## Connection handling

The zApp pushes channel changes over the WebSocket `ws://<host>/zrap/ws`. It sends no data frames while idle and does not answer client pings, so no client heartbeat is used. (It appears to send WebSocket pings of its own, which the client answers automatically; they keep the connection visibly alive.) In addition:

*   After 60 s without any frame, a cheap `GET /zrap/rssi` checks that the zApp still answers. If it does not, the connection is dropped and rebuilt.
*   Reconnects use exponential backoff from 1 s to 60 s with +/-20 % jitter.
*   After every (re)connect all channel states are read once (`/zrap/chscan`), so a change made during the gap is not lost.
*   Entities only become unavailable after 60 s without a connection. Short drops are logged at debug level; a longer outage logs one warning and one info message when the zApp is back.
*   Three hidden diagnostic sensors per zApp count the event stream since HA start or reload of the entry: *Connections* (connection setups, 1 after a clean start), *Short outages* (drops under 60 s, entities stayed available) and *Long outages* (entities were unavailable). They refresh with the 60 s hub poll and are found under the device's diagnostic entities; unhide them to show them on a dashboard.

HTTP requests follow the device's rules: reads are retried, commands are sent exactly once (a repeated `move_open_<ms>` or `dim_down_<ms>` would act twice), the 302 answer to a command is not followed, and at most two requests run against a zApp at a time.

## Dashboard card resource

The integration serves its blinds card at `/api/zeptrion_air/zeptrion-air-blinds-card.js` and registers it once as a Lovelace resource (storage mode only). Earlier versions could add the resource again on every Home Assistant start, because the resource list had not been loaded yet when it was checked. The resource list is now loaded first, the resource is added only when missing, and duplicates left behind by earlier versions are removed automatically on the next start.

## Options

Each hub (config entry) has its own options: **Settings > Devices & services > Zeptrion Air > (hub) > Configure**. Saving the options reloads that hub; changed travel times are taken over without a reload.

| Option | Key | Default | Description |
|---|---|---|---|
| Blind step duration (ms) | `step_duration_ms` | 250 | Duration of a single up/down step for blinds (100-32000 ms). |
| Take over blind travel times from the zApp | `auto_travel_times` | on | Only on hubs with blinds. See [Blinds](#blinds-direction-position-and-learning-mode). |
| Channel n: travel time up / down (s) | `travel_up_chN`, `travel_down_chN` | 0 | Full travel time per blind; 0 = unknown (no position). |
| Channel n: slat tilt time (s) | `tilt_time_chN` | 0 | Time the slats turn on a change of direction; 0 = no slats. |

## Current Limitations

*   **Dimmer Brightness is Optimistic:** see [Light Entities (Switches & Dimmers)](#light-entities-switches--dimmers) above.
*   **Cover positions are estimated:** The zApp reports only "motor runs" / "motor stands" for a blind, never a position. With travel times configured, the position is estimated from the motor time (see [Blinds: direction, position and learning mode](#blinds-direction-position-and-learning-mode)); a blind moved by a scene or another device's Smartbutton has an unknown position until its next full run.
*   **Wall keys need a Smartfront:** Without a Smartfront the zApp reports no key presses.
*   **Sensors are Static:** The sensor entities for channel name, group, and icon ID are populated once during setup and do not update dynamically if changed on the Zeptrion device itself (though these are typically static).

## Installation

**Requirements:** Home Assistant 2026.8.0 or newer (needed for `via_device_id`, which is used to link light and cover devices to their hub).

This repository is not listed in the default HACS store - add it as a **custom repository**:

1.  In Home Assistant, open **HACS**, then **Integrations**, then the three-dot menu (top right), then **Custom repositories**.
2.  Add `https://github.com/polar-gith/ha-zeptrion-air-integration` with category **Integration**.
3.  Install "Zeptrion Air Integration" from HACS as usual.

## Technical Details: API Usage

The official zApp API documentation by Feller and a short reference of the parts used here are linked in [docs/zapp-api.md](docs/zapp-api.md).

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
