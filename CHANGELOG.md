# Changelog

All notable changes to this integration. Versions follow Home Assistant style (`YEAR.MONTH.PATCH`); every version is a [GitHub release](https://github.com/polar-gith/ha-zeptrion-air-integration/releases) and installable through HACS.

## 2026.11.28 – 2026-11-22

- Step actions also under the names of the HomersDroid fork: `blind_step_up` / `blind_step_down` (same as `blind_up_step` / `blind_down_step`).
- All four step actions take an optional `duration` in milliseconds (100–32000); without it the step duration option applies.
- README: switching from alternize/ha-zeptrion-air-integration and its forks.

## 2026.11.27 – 2026-11-22

- Actions are translated (German, French, Italian) and have icons.
- Blinds and lights take their name from their channel device (Home Assistant naming rule). Names you set yourself are kept; otherwise a blind or light now shows the name of its device where the two differed.
- Scene, store and calibrate buttons, channel sensors and the Wi-Fi signal sensor are translated; their icons come from `icons.json`.
- Error messages are translated (German, French, Italian, English).
- *Zeptrion Air Blinds Card*: cleaner, more compact layout with the blind's name, state and position; the running direction is highlighted; button tooltips in the user's language.
- Setup dialogs explain their fields; the discovery dialog labels the step duration.
- Fix: adding an already configured zApp by hand ended with "unknown error" instead of "already configured".
- Generated dashboard in the user's language (German, French, Italian, English).
- *Zeptrion Air Blinds Card* deprecated in favour of the tile card feature *Zeptrion scenes S1–S4*.
- README: examples, troubleshooting, removal; hint to update the zApp firmware with Feller's zeptrion app.
- Quality scale: every rule up to Platinum met (self-assessed, `quality_scale.yaml`); README section *Quality*.
- Tests: switching from heldchen/ha-zeptrion-air (entity IDs, devices, actions kept), config flow, card resource, diagnostics; coverage above 97 % and strict type check in CI, now against the newest Home Assistant release (Python 3.14); weekly Dependabot updates for the test requirements and GitHub Actions.
- Internal: parallel updates declared for every platform; type fixes; unreachable checks and an unused dim command removed.

## 2026.11.26 – 2026-11-22

- This changelog.
- Issue forms for bug reports (zApp model, firmware, Smartfront, diagnostics, debug log) and feature requests.
- Automated tests for the dashboard parts (scenes feature, dashboard strategy, blinds card) in CI.
- Contribution guidelines updated: checks to run.

## 2026.11.25 – 2026-11-22

- Blinds report who started a run: attribute `started_by` (while moving) and `last_run.started_by` = `home_assistant`, `wall_key` (Smartfront key) or `other` (keys of an older button face, a Smartbutton, another zApp).
- README: older button faces report no key presses, only the channel starting and stopping (seen on a 3340-2-B with firmware 01.08.26).

## 2026.11.24 – 2026-11-22

- Dashboard strategy `custom:zeptrion-air`: all blinds and lights, one view per floor, one section per area, tiles with open/close, tilt, brightness and scenes.

## 2026.11.23 – 2026-11-22

- Tile card feature *Zeptrion scenes S1–S4* (`custom:zeptrion-air-scenes`) for blinds and lights.
- README: section *Dashboard cards*.

## 2026.11.22 – 2026-11-22

- Wall keys of button faces without `/zapi/smartfront/id`: event entity created with the first reported key press.
- Blinds card: scene buttons call `blind_recall_s1`–`s4` on the cover (before: guessed button entity IDs, which failed for renamed entities).
- Manifest: categories and connectivity for the Marketplace.

## 2026.11.21 – 2026-11-22

- Actions `blind_up_step` / `blind_down_step` (one step, option *step duration*), compatible with heldchen/ha-zeptrion-air automations.
- README: switching from heldchen/ha-zeptrion-air.

## 2026.11.20 – 2026-11-20

- Fixes from the HACS review: blinds card served as a static file, card values escaped, LICENSE lists Jeremy Diaz.

## 2026.11.19 – 2026-11-19

- HACS validation without exceptions; README lists the tested installation. Repository detached from the fork network.

## 2026.11.18 – 2026-11-14

- README and API notes after review; hardware version in the device info.

## 2026.11.17 – 2026-11-14

- No more Wi-Fi repair notices; button *Reconnect* instead of *Restart* (the zApp ignores the reboot command).
- Smartfront LEDs shown by default, light sliders only; *Undo LED change*.

## 2026.11.16 – 2026-11-14

- Smartfront LEDs as lights, Smartbutton programming, names written to the zApp.

## 2026.11.15 – 2026-11-14

- zApp rooms suggested as areas, reconfigure flow, discovery via `_http._tcp`, French and Italian, CI checks.

## 2026.11.14 – 2026-11-13

- Scenes S1–S4 for switches and dimmers.
- Blinds follow scenes and runs started by any zApp or device; calibration run replaces the learning mode.

## 2026.11.13 – 2026-11-13

- Calibration starts at the top when the zApp ignores *open* there.

## 2026.11.12 – 2026-11-13

- Store blind scenes S1–S4; calibration run. zApp API documentation (with permission of Feller AG).

## 2026.11.11 – 2026-11-12

- Hidden diagnostic sensors for connections and outages per zApp.

## 2026.11.10 – 2026-11-12

- Blind actions registered at platform setup, so renamed blinds keep them.

## 2026.11.9 – 2026-11-11

- A scene started at an end stop moves away from it.

## 2026.11.8 – 2026-11-11

- Tilt buttons turn the slats one step.

## 2026.11.7 – 2026-11-11

- Slat position derived from a known end stop.

## 2026.11.6 – 2026-11-11

- Travel times taken over from full runs the zApp ends by itself; slat tilt time per blind (tilt position and buttons); light polling option removed (all channels re-read after every reconnect).

## 2026.11.4 – 2026-11-11

- Blinds: direction from commands and wall keys, estimated position from travel times, set position, restored after restart.

## 2026.11.3 – 2026-11-11

- Connection: WebSocket with liveness check and backoff, states re-read after reconnect, entities unavailable only after 60 s.
- Commands sent exactly once, reads retried, at most two requests per zApp.
- Smartfront: wall key events, temperature/illuminance/humidity; DHCP discovery; diagnostics; tests against a simulated zApp.

## 2026.11.2 – 2026-11-10

- High-resolution brand images.

## 2026.11.1 – 2026-11-10

- Single-channel zApps keep their channel sub-device; deprecated `CONNECTION_CLASS` removed; own Feller icon and logo.

## 2026.11.0 – 2026-11-10

- Home Assistant style versioning. No functional changes.

## 2025.6.7 – 2026-11-10

- Dashboard card resource registered only once (duplicates removed on start).

## 2025.6.6 – 2026-11-03

- Options dialog fixed on current Home Assistant releases.

## 2025.6.5 – 2026-11-03

- Light polling as an option per hub (off by default); English and German translations.

## 2025.6.4 – 2026-11-03

- Lights polled every 60 s.

## 2025.6.3 – 2026-11-02

- Devices linked to their hub with `via_device_id`; requires Home Assistant 2026.8.

## 2025.6.2 – 2026-11-02

- Light platform for switches and dimmers; Lovelace card resource registered with the correct type.

Earlier versions: see [alternize/ha-zeptrion-air-integration](https://github.com/alternize/ha-zeptrion-air-integration), based on [heldchen/ha-zeptrion-air](https://github.com/heldchen/ha-zeptrion-air).
