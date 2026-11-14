"""A simulated zApp (HTTP + WebSocket) for tests, modelled on firmware 01.08.28.

Behaviour as documented by diaznet/zeptrionair-api (docs/protocol.md):
commands answer 302, the WebSocket never answers pings and sends JSON frames.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from aiohttp import WSMsgType, web


@dataclass
class FakeZapp:
    serial: str = "21510211"
    channels: dict[int, dict[str, str]] = field(
        default_factory=lambda: {
            1: {"name": "Blind", "group": "Living", "icon": "1443", "type": "00", "cat": "5"},
            2: {"name": "Light", "group": "Living", "icon": "1492", "type": "00", "cat": "1"},
        }
    )
    values: dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0})
    btfu: str | None = "0,0,1000,1000,8501,8602,4501,4602"
    sensors: dict[str, str] = field(
        default_factory=lambda: {"temp": "23.50C", "lux": "none", "hum": "none"}
    )
    # failure injection
    fail_commands_with_timeout: bool = False
    command_delay: float = 0.0
    http_down: bool = False
    rssi_status: int = 200  # e.g. 500 like the 2018 firmware of zapp-22460015
    rssi_dbm: int = -64
    smartbutton_prefix: str = "/zapi/smartbt"

    def __post_init__(self) -> None:
        self.commands: list[tuple[int, str]] = []
        self.scenes: dict[tuple[int, int], int] = {}
        self.reboots = 0
        self.leds: dict[int, str] = {n: "#000000" for n in range(1, 9)}
        self.smartbutton_programming = False
        self.smartbutton_press: asyncio.Event = asyncio.Event()
        self.smartbutton_programmes: list = []  # (channel, scene) -> value, lights only
        self.requests: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.websockets: list[web.WebSocketResponse] = []
        self.ws_connects = 0
        self.app = web.Application()
        self.app.router.add_route("*", "/{tail:.*}", self._handle)

    # --------------------------------------------------------------- helpers

    async def push(self, frame: dict) -> None:
        for ws in list(self.websockets):
            if not ws.closed:
                await ws.send_str(json.dumps(frame))

    async def press(self, key: int, release_after: float = 0.05) -> None:
        bta = ["."] * 9
        bta[key - 1] = "P"
        await self.push({"eid2": {"bta": "".join(bta)}})
        await asyncio.sleep(release_after)
        await self.push({"eid2": {"bta": "........."}})

    async def drop_websockets(self) -> None:
        for ws in list(self.websockets):
            await ws.close()

    # --------------------------------------------------------------- server

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        path = request.path
        self.requests.append(f"{request.method} {path}")
        if self.http_down:
            # device unreachable: fail fast (a long sleep here would hold the
            # client's request slots and make tests timing-dependent)
            raise web.HTTPServiceUnavailable()
        if path == "/zrap/ws":
            return await self._websocket(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            return await self._route(request, path)
        finally:
            self.in_flight -= 1

    async def _route(self, request: web.Request, path: str) -> web.StreamResponse:
        xml = "text/xml"
        if path == "/zrap/id":
            body = (
                f'<?xml version="1.0" encoding="UTF-8"?><id><hw>01.05.02</hw><sn>{self.serial}</sn>'
                "<sys>ZEPTRION</sys><type>3340-2-B</type><oen>zApp</oen>"
                "<sw>01.08.28 (Jun  1 2018 09:13:39)</sw></id>"
            )
            return web.Response(text=body, content_type=xml)
        if path == "/zrap/rssi":
            if self.rssi_status != 200:
                raise web.HTTPInternalServerError()
            return web.Response(text=f"<rssi><dbm>{self.rssi_dbm}</dbm></rssi>", content_type=xml)
        if path == "/zrap/sys" and request.method == "POST":
            form = await request.post()
            if form.get("cmd") == "reboot":
                self.reboots += 1
            raise web.HTTPFound(location="/")
        if path.startswith("/zrap/chdes/ch") and request.method == "POST":
            n = int(path.rsplit("ch", 1)[1])
            form = await request.post()
            for key in ("name", "group"):
                if key in form:
                    self.channels[n][key] = str(form[key])
            raise web.HTTPFound(location="/")
        if path == "/zapi/smartfront/led" and self.btfu is not None:
            if request.method == "POST":
                for item in json.loads(await request.text()):
                    self.leds[int(item["id"])] = item["bg"]
                return web.json_response({})
            return web.json_response(
                [{"id": n, "on": False, "rgb": "#000000", "effect": "#ffffff", "bg": bg}
                 for n, bg in self.leds.items()]
            )
        sb = self.smartbutton_prefix
        if path == f"{sb}/prgm" and self.btfu is not None:
            if request.method == "POST":
                self.smartbutton_programming = bool(json.loads(await request.text()).get("on"))
            return web.json_response({"on": self.smartbutton_programming, "ntm": 60})
        if path == f"{sb}/prgn" and self.btfu is not None:
            try:
                await asyncio.wait_for(self.smartbutton_press.wait(), timeout=2)
                return web.json_response({"prg": True})
            except TimeoutError:
                return web.json_response({"prg": False})
        if path == f"{sb}/prgs" and request.method == "POST" and self.btfu is not None:
            self.smartbutton_programmes.append(json.loads(await request.text()))
            return web.json_response({})
        if path == "/zrap/chdes":
            inner = "".join(
                f"<ch{n}>" + "".join(f"<{k}>{v}</{k}>" for k, v in d.items()) + f"</ch{n}>"
                for n, d in self.channels.items()
            )
            return web.Response(text=f"<chdes>{inner}</chdes>", content_type=xml)
        if path == "/zrap/chscan":
            inner = "".join(f"<ch{n}><val>{v}</val></ch{n}>" for n, v in self.values.items())
            return web.Response(text=f"<chscan>{inner}</chscan>", content_type=xml)
        if path.startswith("/zrap/chscan/ch"):
            n = int(path.rsplit("ch", 1)[1])
            return web.Response(
                text=f"<chscan><ch{n}><val>{self.values.get(n, -1)}</val></ch{n}></chscan>",
                content_type=xml,
            )
        if path.startswith("/zrap/chctrl/ch") and request.method == "POST":
            n = int(path.rsplit("ch", 1)[1])
            form = await request.post()
            cmd = str(form.get("cmd"))
            self.commands.append((n, cmd))
            if self.command_delay:
                await asyncio.sleep(self.command_delay)
            if self.fail_commands_with_timeout:
                await asyncio.sleep(30)
            if cmd in ("on", "off"):
                self.values[n] = 100 if cmd == "on" else 0
                await self.push({"eid1": {"ch": n, "val": self.values[n]}})
            light = self.channels.get(n, {}).get("cat") in ("1", "3")
            if light and cmd.startswith("store_s"):
                self.scenes[(n, int(cmd[-1]))] = self.values[n]
            if light and cmd.startswith("recall_s") and (n, int(cmd[-1])) in self.scenes:
                # like the zApp: a lit dimmer reports 100 whatever its level
                self.values[n] = 100 if self.scenes[(n, int(cmd[-1]))] else 0
                await self.push({"eid1": {"ch": n, "val": self.values[n]}})
            raise web.HTTPFound(location="/")  # 302, must not be followed
        if path == "/":
            return web.Response(text="<html>redirect target</html>", content_type="text/html")
        if path == "/zapi/smartfront/id" and self.btfu is not None:
            return web.json_response(
                {"btfu": self.btfu, "sw": "01.01.08", "hw": "01.00.00", "sys": "ZEPTRION",
                 "sid": "1", "type": "920-3302.36.ZU"}
            )
        if path == "/zapi/smartfront/sensors" and self.btfu is not None:
            return web.json_response(self.sensors)
        raise web.HTTPNotFound()

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        # autoping=False: like the real device, never answer pings
        ws = web.WebSocketResponse(autoping=False)
        await ws.prepare(request)
        self.ws_connects += 1
        self.websockets.append(ws)
        try:
            async for message in ws:
                if message.type == WSMsgType.ERROR:
                    break
        finally:
            self.websockets.remove(ws)
        return ws
