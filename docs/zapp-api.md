# zApp API reference

Official documentation by Feller AG:
**[zeptrionAIR API, 10.ZEPAPI-E.1612 (PDF)](https://mam.feller.ch/Technische%20Dokumentationen/zeptrionAIR-API_10.ZEPAPI-E.161216.pdf)**
(firmware 01.08.18, 46 pages).

A copy is stored in this repository as [docs/api/zeptrionAIR-API_10.ZEPAPI-E.161216.pdf](api/zeptrionAIR-API_10.ZEPAPI-E.161216.pdf),
with the permission of Feller AG (given to the maintainer by phone, November 2026). © Feller AG, all rights reserved.
The notes below summarise what the integration relies on.

## Endpoints used

| Endpoint | Method | Use |
|---|---|---|
| `/zrap/id` | GET | Device identification (serial, type, firmware) |
| `/zrap/chdes`, `/zrap/chdes/chN` | GET | Channel name, group, icon, category (1 switch, 3 dimmer, 5 blind, 6 awning) |
| `/zrap/chscan`, `/zrap/chscan/chN` | GET | Channel state 0–100; blinds always report `-1` (unknown) |
| `/zrap/chctrl/chN` | POST `cmd=…` | Channel commands, see below |
| `/zrap/chctrl` | POST `cmd1=…&cmd2=…` | Several channels in one request |
| `/zrap/rssi` | GET | Wi-Fi signal |
| `/zrap/ws` | WebSocket | Push of channel changes and key events (not in the PDF) |
| `/zapi/smartfront/id`, `/zapi/smartfront/sensor` | GET | Smartfront keys and sensors |
| `/zrap/sys` | POST `cmd=reboot` / `factorydefault` / `networkdefault` | Not used by the integration |

## Channel commands (`/zrap/chctrl`, chapter 3.6.3)

- Lights: `on`, `off`, `toggle`, `dim_up`, `dim_down`, `dim_up_<t>`, `dim_down_<t>`, `dim_<t>`, `stop`
- Blinds: `open`, `close` (to the end stop), `move_open`, `move_close`, `move_open_<t>`, `move_close_<t>`, `stop`
- Scenes: `recall_s<n>`, `store_s<n>`, `delete_s<n>` with n = 1–4
- `<t>` = 100–32000 ms. Commands answer with HTTP 302 and must not be repeated.

## Not provided by the device

- No position feedback for blinds, no reference run or calibration command. Position and travel times are estimated by the integration.
- No way to read which positions are stored in scenes S1–S4.
