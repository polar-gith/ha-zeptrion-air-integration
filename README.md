# Zeptrion Air for Home Assistant

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=polar-gith&repository=ha-zeptrion-air-integration&category=integration)

Local control of [Feller Zeptrion Air](https://www.feller.ch/de/Produkte/Gebaeudeautomation/Zeptrion-Systeme/Zeptrion-Air) devices (zApps): blinds, awnings, switches and dimmers, wall keys and Smartfront sensors. No cloud; the zApp's local REST API and WebSocket are used.

**All functions of the zeptrion Air devices are integrated**: every channel command, scenes, wall keys and Smartbuttons, Smartfront sensors and LEDs, names, reconnect and diagnostics. Languages: English, German, French, Italian.

## Installation

Requires Home Assistant 2026.8 or newer.

1. **Settings → Marketplace** (HACS on Home Assistant 2026.10 and older) → *Add from link*: `https://github.com/polar-gith/ha-zeptrion-air-integration`, category *Integration*.
2. Install *Zeptrion Air* and restart Home Assistant.
3. zApps (host names `zapp-*`) are discovered via mDNS (`_zapp._tcp`, `_http._tcp`) and DHCP; confirm them under **Settings → Devices & services**. A changed IP address is taken over automatically; to change the address by hand use **⋮ → Reconfigure** on the zApp (entities and settings are kept).
4. The room stored in the zApp (channel group) is suggested as area when a device is created; areas you already assigned are not changed.

## What you get

| Per zApp | Entities |
|---|---|
| Blind / awning channel (cat. 5/6) | Cover with direction, position and slats; buttons *Scene S1–S4*, *Store S1–S4* and *Calibrate* (the last two under *Configuration*) |
| Switch / dimmer channel (cat. 1/3) | Light (on/off or brightness); buttons *Scene S1–S4* and *Store S1–S4* (the latter under *Configuration*) |
| Smartfront fitted | Event *Wall keys*, sensors temperature (module heat, reads above room temperature) and, if present, illuminance / humidity; light *Smartfront LED n* per key in use; button *Undo LED change* |
| Always | Button *Reconnect*; Wi-Fi signal; hidden diagnostic counters *Connections*, *Short outages*, *Long outages*; sensors name / group / icon ID per channel (disabled) |

Actions: `zeptrion_air.blind_up_step`, `blind_down_step`, `blind_recall_s1`, `blind_recall_s2`, `blind_recall_s3`, `blind_recall_s4`, `blind_store_scene` (`scene: 1-4`), `blind_calibrate`, `light_store_scene`, `light_recall_scene` (`scene: 1-4`), `program_smartbutton`, `write_name_to_zapp`.

## Blinds

**Direction and end stops.** The zApp only reports "motor runs" / "motor stands". The direction comes from what started the run: a Home Assistant command or a Smartfront wall key. A run nobody stopped ended at the end stop (the zApp switches the motor off there), so the blind is shown fully open or closed.

**Position.** With travel times known, the position is estimated from the motor time, shown while moving and *set position* works (`move_open_<ms>` / `move_close_<ms>`; above 32 s start + stop). A run reaching 95 % of the travel time snaps to the end. The position survives a restart. A run started by another device without known direction makes the position unknown until the next full run.

**Travel times** are set in three ways:
- automatically: a full run from one end stop to the other that nobody stopped lasted the travel time (option *Take over blind travel times from the zApp*, on by default),
- with **Calibrate**: fully up (skipped if the blind is already at the top; the zApp then ignores *open*), fully down, fully up, back to the previous position; the two full runs are stored and a notification shows the result. Stopping or reversing aborts without changes. Use it after installing or replacing a blind,
- by hand in the hub options.

Blinds of different height have different times; measure each one.

**Slats.** With a slat tilt time (typically 1–2 s), the first part of a run after a change of direction turns the slats. The blind then reports a tilt position (100 = open) and supports *set tilt position*; the tilt buttons turn the slats one step (option *step duration*).

**Scenes S1–S4.** The zApp stores a scene as the end stop the blind came from plus the net motor time from there, and replays it as "run to that end stop, then the net time back". Feller therefore says: drive the blind fully **up** first, then to the height, then store. Coming from the top the replay ends with a run down (slats closed/tilted); coming from the bottom it ends with a run up (slats open). The slat angle itself is not stored.
- *Store S1–S4* (and `blind_store_scene`) store right away if the blind came from the top. Otherwise the blind first runs to the top and back to its current position, then the scene is stored (a notification confirms it). Refused while the blind moves or when its position is unknown.
- Scenes are recalled from anywhere: the *Scene* buttons, wall keys, Smartbuttons of any zApp (they can send any command to any zApp) or other devices. The integration follows the runs without knowing who started them: from an end stop the run goes away from it; from a known position in between a run that ends at an end stop is recognised by its duration, and the run back follows. A run of unknown direction that ends elsewhere makes the position unknown until the next full run.
- Scenes cannot be read back from the zApp.

**Wall keys.** Depending on the blind, a wall key starts a full run, or moves only while held (a short tap turns the slats). A run that ends within 1 s after the key is released counts as stopped by hand, not as arrived at the end stop.

## Lights

Switches use on/off, dimmers brightness. The zApp has no "set brightness" command: brightness is reached by switching on and dimming down for a time (full travel 3.4 s), and the zApp reports any lit dimmer as `100`, so brightness is optimistic. `transition` is ignored. On/off changes from the wall are pushed in real time.

**Scenes S1–S4.** A switch or dimmer channel stores its current state as a scene too: on or **off**, and for dimmers the dim level. *Store S1–S4* (and `light_store_scene`) store it on the zApp like holding the scene key; *Scene S1–S4* (and `light_recall_scene`) recall it. Scenes stored through Home Assistant are also kept by the integration (attributes `scene_s1` … `scene_s4`, brightness 0–255, 0 = off), so a recall from Home Assistant shows the stored brightness. A scene recalled elsewhere (wall key, Smartbutton, other device) or stored with the scene key is only seen as on/off; the brightness shown stays the last known one.

## Wall keys (Smartfront)

One event entity per zApp with event types `key_<n>_press` / `key_<n>_release` for every key in use. Attributes: `key`, `function` (`channel`, `smartbutton`, `unknown`), `code`; channel keys also `channels` and `direction`. Smartbuttons can thus trigger automations:

```yaml
triggers:
  - trigger: event.received
    target:
      entity_id: event.zapp_21510211_wall_keys
    options:
      event_type:
        - key_5_press
```

(Before Home Assistant 2026.7: a `state` trigger on the entity with a condition on its `event_type` attribute.)

## Smartfront LEDs

Each key in use has an LED whose **background colour** can be set, e.g. as status display (window open = red). Per LED there is a light *Smartfront LED n* with colour and brightness sliders. The button *Undo LED change* of the zApp steps back through the last 20 LED changes (made in Home Assistant since the last restart). The zApp keeps using the LEDs for its own indications and shows the background colour otherwise. Turning an LED light off clears its background colour; the zApps come with a pink background (`#ff006d`). The LEDs are bright: 10–25 % is usually enough.

## Smartbuttons

Smartbuttons can be (re)programmed from Home Assistant, like with the zeptrion app: **Tools → Actions → Zeptrion Air: Program Smartbutton** (`zeptrion_air.program_smartbutton`). Choose the zApp that has the Smartbutton, the commands (blinds or lights of **any** zApp, e.g. `recall_s1`, `open`, `close`, `stop`, `toggle`, `on`, `off`, `move_close_5000`) and/or raw HTTP requests in the Feller format, then *Perform action*. The Smartbuttons blink; press the one to program within the wait time (default 60 s). A notification shows the result.

- The programmed Smartbutton works directly between the zApps, also when Home Assistant is down.
- The previous programme of that Smartbutton is replaced; it cannot be read back.
- The zApp accepts about 560 bytes: three to six commands. A longer programme is refused before anything blinks.
- For anything more complex, leave the Smartbutton as it is and use its press as trigger of an automation (see *Wall keys*): every press arrives as event.

Example, one Smartbutton for scene S1 of all blinds downstairs:

```yaml
action: zeptrion_air.program_smartbutton
data:
  device_id: <zApp device>
  actions:
    - entity_id: cover.play_blind
      command: recall_s1
    - entity_id: cover.corridor_ug_blind
      command: recall_s1
    - entity_id: cover.gym_sleep_blind
      command: recall_s1
```

## Names in the zApp

`zeptrion_air.write_name_to_zapp` writes the name shown in Home Assistant, and the area as group, into the zApp (at most 32 bytes each), so the zeptrion app shows the same names. Name and group can also be given explicitly.

## Connection

Push updates over the zApp WebSocket. After 60 s without a frame `GET /zrap/rssi` checks the zApp; reconnects back off from 1 to 60 s; after every reconnect all channels are read once. Entities turn unavailable only after 60 s without connection. Commands are sent exactly once (a repeated timed command would act twice), reads are retried, at most two requests run per zApp. The button *Reconnect* reloads a zApp in Home Assistant (connection, channel states, entities; they are unavailable for about half a minute, blind positions are kept); the zApp itself cannot be restarted remotely, a hanging zApp needs a short power cut. *Download diagnostics* gives connection statistics with host, serial and names redacted.

## Options (per hub)

**Settings → Devices & services → Zeptrion Air → (hub) → Configure.** Changed travel times apply without a reload.

| Option | Default | |
|---|---|---|
| Blind step duration (ms) | 250 | single step of the step buttons / tilt buttons, 100–32000 |
| Take over blind travel times from the zApp | on | see *Travel times* |
| Channel n: travel time up / down (s) | 0 | 0 = unknown (no position) |
| Channel n: slat tilt time (s) | 0 | 0 = no slats |

## Feller API coverage

Based on Feller's *zApp API* 10.ZEPAPI-E.1612 ([docs/api](docs/api)).

| API | Use in Home Assistant |
|---|---|
| `/zrap/chctrl` on, off, toggle, dim, open, close, move, stop, timed commands | lights, covers, step and tilt buttons, Smartbutton commands |
| `/zrap/chctrl` `store_s1-4`, `recall_s1-4` | scene buttons and actions (blinds and lights) |
| `/zrap/chctrl` `delete_s1-4` | not offered (not needed; a scene is overwritten by storing) |
| `/zrap/chscan`, WebSocket `/zrap/ws` | states, pushed in real time; read again after every reconnect |
| `/zrap/chnotify` (long poll) | not used; the WebSocket replaces it |
| `/zrap/chdes` read / write | device names, rooms as suggested areas, `write_name_to_zapp` |
| `/zrap/id`, `/zrap/rssi` | device info, Wi-Fi sensor, liveness check |
| `/zrap/sys` reboot | not usable: firmware 01.08.28 answers 302 but does not restart (tested); button *Reconnect* reloads the zApp in Home Assistant instead; factory and network reset deliberately not offered |
| `/zrap/net`, `/zrap/netscan` | not used (network setup belongs in the zeptrion app; `/zrap/net` contains the Wi-Fi password) |
| `/zrap/loc`, `/zrap/date`, `/zrap/ntp`, `/zrap/scheduler` | not used: timers and clock of the zApp stay in the zeptrion app; use Home Assistant automations instead |
| `/zapi/smartfront/id`, `/sensors` | wall keys, Smartfront sensors |
| `/zapi/smartfront/led` (background colour) | lights *Smartfront LED n* |
| Smartbutton programming `prgm` / `prgn` / `prgs` | `program_smartbutton` |
| mDNS `zapp-*` | discovery (plus DHCP) |

## Limitations

- Blind positions and dimmer brightness are estimates; the zApp reports neither. Scenes cannot be read back.
- Wall keys, Smartbuttons and LEDs need a Smartfront.
- The zApp cannot be restarted remotely (firmware 01.08.28 ignores the reboot command).
- Channel name / group / icon sensors are read at setup (and follow `write_name_to_zapp`).

## Reference

- zApp API by Feller: [docs/zapp-api.md](docs/zapp-api.md) (summary, observed behaviour) and the PDF in [docs/api](docs/api).
- Protocol notes by diaznet: [zeptrionair-api `docs/protocol.md`](https://github.com/diaznet/zeptrionair-api/blob/main/docs/protocol.md).
- The blinds card is served at `/api/zeptrion_air/zeptrion-air-blinds-card.js` and registered once as a dashboard resource.
- Versions follow `YEAR.MONTH.PATCH`, released as tags `vYYYY.M.PATCH`.

## Credits

Based on [alternize/ha-zeptrion-air-integration](https://github.com/alternize/ha-zeptrion-air-integration) by [alternize](https://github.com/alternize); maintained and developed independently by [polar-gith](https://github.com/polar-gith). Connection handling, command rules, Smartfront key decoding and sensors and DHCP discovery build on the measurements and code of [Jeremy Diaz (diaznet)](https://github.com/diaznet): [zeptrionair-api](https://github.com/diaznet/zeptrionair-api), [ha-zeptrionair](https://github.com/diaznet/ha-zeptrionair) (MIT). Issues and pull requests are welcome.

## License

MIT, see [LICENSE](LICENSE). "Home Assistant" is a project of the Open Home Foundation; "Zeptrion" and "Zeptrion Air" are trademarks of Feller AG. This project is affiliated with neither. The Feller logo in `brand/` only identifies the supported devices.
