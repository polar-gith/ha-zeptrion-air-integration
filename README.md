# Home Assistant Zeptrion Air Integration

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=polar-gith&repository=ha-zeptrion-air-integration&category=integration)

> **This is a maintained fork** of [alternize/ha-zeptrion-air-integration](https://github.com/alternize/ha-zeptrion-air-integration) by [@alternize](https://github.com/alternize), published by [@polar-gith](https://github.com/polar-gith). All credit for the original integration goes to alternize and its contributors. This fork adds a Lovelace card resource fix and a light platform for switches/dimmers that are not yet in the upstream release.
>
> Once those changes land upstream, this fork will be retired in favor of the original repository again. See [Credits & Upstream](#credits--upstream) below for details.

## Description

This is a custom integration for Home Assistant that allows you to control [Zeptrion Air](https://www.feller.ch/de/Produkte/Gebaeudeautomation/Zeptrion-Systeme/Zeptrion-Air) devices locally over your WLAN. Zeptrion Air hubs connect to your network and expose a REST API for controlling attached lights, blinds, and other devices. This integration aims to bring these devices into Home Assistant for automation and UI control.

## Features

*   **mDNS Discovery:** Automatically discovers Zeptrion Air hubs (devices with hostnames starting `zapp-`) on your local network.
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

*   The hub does not reliably report a dimmer's actual brightness, so Home Assistant keeps the last brightness value it set rather than reading it back.
*   After every command, the entity polls the channel's scan endpoint (`/zrap/chscan/ch{X}`) to refresh on/off state. For dimmers this poll is delayed (0.35 s plus a term proportional to how far the dimmer has to travel) to give the hub time to finish moving.
*   The integration also listens to the hub's websocket push events and applies on/off changes reported from there in real time. Brightness changes made directly on the hub are not reflected, for the same reason brightness isn't polled.

**Devices**

*   Each light channel is registered as its own Home Assistant device, linked to the parent hub via `via_device`, with the device model shown as "Switch" or "Dimmer" depending on category.

## Current Limitations

*   **Dimmer Brightness is Optimistic:** see [Light Entities (Switches & Dimmers)](#light-entities-switches--dimmers) above.
*   **No Position Feedback for Covers:** The Zeptrion Air API does not provide real-time position feedback for blinds/covers. Therefore, the Home Assistant cover entities will not display the current position (e.g., 50% open). Status updates are optimistic based on commands sent.
*   **Sensors are Static:** The sensor entities for channel name, group, and icon ID are populated once during setup and do not update dynamically if changed on the Zeptrion device itself (though these are typically static).

## Installation

This fork is not (and is not meant to become) a separate listing in the official HACS store - add it as a **custom repository** instead:

1.  In Home Assistant, open **HACS**, then **Integrations**, then the three-dot menu (top right), then **Custom repositories**.
2.  Add `https://github.com/polar-gith/ha-zeptrion-air-integration` with category **Integration**.
3.  Install "Zeptrion Air Integration" from HACS as usual.

Once this fork's changes have landed upstream, switch back to the official repository: [Add repository to HACS](https://my.home-assistant.io/redirect/hacs_repository/?category=integration&repository=ha-zeptrion-air-integration&owner=alternize).

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
*   **GET `/zrap/chscan` / `/zrap/chscan/ch{X}`**: While available, this endpoint was found to return `-1` (unknown state) for cover channels and is therefore not currently used for determining cover state.

## Contributing

Contributions to this integration are very welcome! If you have ideas for improvements, new features, or bug fixes, please feel free to:

1.  Open an issue on the GitHub repository to discuss your proposed changes.
2.  Submit a Pull Request with your contributions.

Please try to follow Home Assistant's development guidelines and ensure code is well-tested and documented.

## Credits & Upstream

This project is based entirely on the original work by [**alternize**](https://github.com/alternize) at [alternize/ha-zeptrion-air-integration](https://github.com/alternize/ha-zeptrion-air-integration). This fork, maintained by [**polar-gith**](https://github.com/polar-gith), adds a small number of fixes and features on top of that work so they can be used before they land in the upstream release. Please direct general feedback and feature requests to the upstream project; issues specific to this fork's changes can be filed here.

## Disclaimer & License

"Home Assistant" is a project of the Open Home Foundation. This project is not affiliated with nor endorsed by the Open Home Foundation. 

"Zeptrion", "Zeptrion Air" and the Zeptrion Air logo are property of & (c) by Feller AG 2016. This project is not affiliated with nor endorsed by Feller AG.

All other files in this project are licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
