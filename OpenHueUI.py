"""
title: OpenHueUI
author: Brett Yandell
author_url: https://github.com/BrettYandell
description: Control Philips Hue smart lights via natural language. Turn lights on/off, set brightness, change colors, manage groups, apply scenes, use presets, trigger effects. Based on ThomasRohde/hue-mcp.
required_open_webui_version: 0.7.0
requirements: phue
version: 1.0.0
"""

from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field
import socket
import json

# ──────────────────────────────────────────────
# EVENT EMITTER
# ──────────────────────────────────────────────


class EventEmitter:
    def __init__(self, event_emitter: Callable[[dict], Any] = None):
        self.event_emitter = event_emitter

    async def progress_update(self, description: str):
        await self.emit(description)

    async def error_update(self, description: str):
        await self.emit(description, "error", True)

    async def success_update(self, description: str):
        await self.emit(description, "success", True)

    async def emit(
        self,
        description: str = "Unknown State",
        status: str = "in_progress",
        done: bool = False,
    ):
        if self.event_emitter:
            await self.event_emitter(
                {
                    "type": "status",
                    "data": {
                        "status": status,
                        "description": description,
                        "done": done,
                    },
                }
            )


# ──────────────────────────────────────────────
# COLOR UTILITIES
# ──────────────────────────────────────────────


def _gamma_expand(value: float) -> float:
    v = value / 255.0
    return ((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92


def rgb_to_xy(r: int, g: int, b: int) -> Tuple[float, float]:
    R = _gamma_expand(r)
    G = _gamma_expand(g)
    B = _gamma_expand(b)
    X = R * 0.664511 + G * 0.154324 + B * 0.162028
    Y = R * 0.283881 + G * 0.668433 + B * 0.047685
    Z = R * 0.000088 + G * 0.072310 + B * 0.986039
    total = X + Y + Z
    if total == 0:
        return (0.0, 0.0)
    return (round(X / total, 4), round(Y / total, 4))


COLOR_PRESETS = {
    "reading": {"ct": 346, "bri": 200},
    "relaxation": {"ct": 400, "bri": 144},
    "concentration": {"ct": 231, "bri": 220},
    "energize": {"ct": 200, "bri": 240},
    "bright": {"ct": 250, "bri": 254},
    "dimmed": {"ct": 370, "bri": 100},
    "nightlight": {"ct": 450, "bri": 20},
    "sleep": {"ct": 450, "bri": 30},
    "warm": {"ct": 400, "bri": 180},
    "cool": {"ct": 220, "bri": 200},
    "vivid": {"xy": (0.64, 0.33), "bri": 254},
    "calm": {"xy": (0.25, 0.35), "bri": 120},
}


def _coerce_bri(bri):
    if bri is None:
        return None
    return max(1, min(254, int(bri)))


def _coerce_ct(ct):
    return max(153, min(500, int(ct)))


# ──────────────────────────────────────────────
# HUE BRIDGE CLIENT
# ──────────────────────────────────────────────


class HueBridgeClient:
    def __init__(self, bridge_ip: str, username: str = ""):
        self.bridge_ip = (bridge_ip or "").strip()
        self.username = (username or "").strip() or None
        self._bridge = None

    def _get_bridge(self):
        if self._bridge is None:
            from phue import Bridge

            if self.username:
                self._bridge = Bridge(ip=self.bridge_ip, username=self.username)
            else:
                self._bridge = Bridge(ip=self.bridge_ip)
            self._bridge.connect()
        return self._bridge

    def register(self):
        from phue import Bridge

        b = Bridge(ip=self.bridge_ip)
        b.connect()
        if not getattr(b, "username", None):
            raise RuntimeError(
                "Registration failed. Press bridge link button and try again within 30 seconds."
            )
        self._bridge = b
        self.username = b.username
        return b.username

    def list_lights(self):
        b = self._get_bridge()
        result = []
        for light in b.lights:
            result.append(
                {
                    "id": light.light_id,
                    "name": light.name,
                    "on": light.on,
                    "brightness": getattr(light, "brightness", None),
                    "colormode": getattr(light, "colormode", None),
                    "xy": getattr(light, "xy", None),
                    "ct": getattr(light, "colortemp", None),
                    "reachable": getattr(light, "reachable", None),
                }
            )
        return result

    def list_groups(self):
        b = self._get_bridge()
        groups_raw = b.get_group()
        result = []
        for gid, info in groups_raw.items():
            result.append(
                {
                    "id": int(gid),
                    "name": info.get("name"),
                    "type": info.get("type"),
                    "lights": list(map(int, info.get("lights", []))),
                    "any_on": info.get("state", {}).get("any_on"),
                    "all_on": info.get("state", {}).get("all_on"),
                }
            )
        return result

    def list_scenes(self, group_id=None):
        b = self._get_bridge()
        scenes_raw = b.get_scene()
        result = []
        for sid, data in scenes_raw.items():
            g = data.get("group")
            if group_id is not None and str(group_id) != str(g):
                continue
            result.append(
                {
                    "id": sid,
                    "name": data.get("name"),
                    "group": int(g) if g and str(g).isdigit() else g,
                    "lights": data.get("lights"),
                }
            )
        return result

    def resolve_light(self, id_or_name):
        if isinstance(id_or_name, int) or (
            isinstance(id_or_name, str) and id_or_name.isdigit()
        ):
            return int(id_or_name)
        b = self._get_bridge()
        name_map = {l.name.lower(): l.light_id for l in b.lights}
        match = name_map.get(str(id_or_name).strip().lower())
        if match is None:
            raise ValueError(f"Light not found: {id_or_name}")
        return int(match)

    def resolve_group(self, id_or_name):
        groups = self.list_groups()
        if isinstance(id_or_name, int) or (
            isinstance(id_or_name, str) and id_or_name.isdigit()
        ):
            for g in groups:
                if g["id"] == int(id_or_name):
                    return int(id_or_name)
            raise ValueError(f"Group not found: {id_or_name}")
        name = str(id_or_name).strip().lower()
        for g in groups:
            if str(g["name"]).strip().lower() == name:
                return g["id"]
        raise ValueError(f"Group not found: {id_or_name}")

    def set_light(self, light_id, payload):
        b = self._get_bridge()
        b.set_light(int(light_id), payload)

    def set_group(self, group_id, payload):
        b = self._get_bridge()
        b.set_group(int(group_id), payload)

    def create_group(self, name, light_ids):
        b = self._get_bridge()
        b.create_group(name, light_ids)
        for g in self.list_groups():
            if g["name"] == name:
                return g["id"]
        return -1

    def refresh(self):
        b = self._get_bridge()
        return b.get_api()


# ──────────────────────────────────────────────
# OPENWEBUI TOOLS CLASS
# ──────────────────────────────────────────────


class Tools:
    """Philips Hue smart lighting control. Turn lights on/off, set brightness, change colors, manage groups, apply scenes, use presets, and trigger effects."""

    class Valves(BaseModel):
        bridge_ip: str = Field(
            default="",
            description="IP address of your Philips Hue Bridge (e.g. 192.168.1.10)",
        )
        username: str = Field(
            default="",
            description="Hue API username. Use link_to_bridge to register if blank.",
        )

    class UserValves(BaseModel):
        default_brightness: int = Field(
            default=200, description="Default brightness (1-254) when not specified"
        )
        favorite_preset: str = Field(
            default="relaxation", description="Your preferred color preset name"
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()
        self._client = None

    def _get_client(self) -> HueBridgeClient:
        if self._client is None or self._client.bridge_ip != self.valves.bridge_ip:
            self._client = HueBridgeClient(self.valves.bridge_ip, self.valves.username)
        return self._client

    # ── Bridge Setup ──

    async def link_to_bridge(
        self,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Register with the Philips Hue Bridge to obtain an API username. The user must press the physical link button on the bridge before calling this. Call this when bridge_ip is set but username is missing.

        :return: JSON with the new username or an error message.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.progress_update("Attempting to register with Hue Bridge...")
        try:
            client = self._get_client()
            new_username = client.register()
            await emitter.success_update(
                f"Registered! Save this username in Valves: {new_username}"
            )
            return json.dumps(
                {
                    "ok": True,
                    "username": new_username,
                    "message": "Save this username in the tool Valves settings.",
                }
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps(
                {
                    "ok": False,
                    "error": str(e),
                    "hint": "Press the link button on the bridge, then call link_to_bridge again within 30 seconds.",
                }
            )

    async def ping_bridge(
        self,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Test connectivity to the Hue Bridge. Use this to verify your bridge_ip and username are correct before issuing commands.

        :return: JSON with bridge reachability status.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.progress_update("Pinging Hue Bridge...")
        try:
            client = self._get_client()
            api = client.refresh()
            config = api.get("config", {})
            await emitter.success_update(
                f"Bridge reachable: {config.get('name', 'Hue Bridge')}"
            )
            return json.dumps(
                {
                    "ok": True,
                    "bridge_name": config.get("name"),
                    "api_version": config.get("apiversion"),
                    "bridge_ip": self.valves.bridge_ip,
                }
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    # ── Lights: Read ──

    async def get_all_lights(
        self,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Get a list of all Philips Hue lights with their current state (on/off, brightness, color). Use this to discover available lights and their IDs.

        :return: JSON list of all lights.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.progress_update("Fetching all lights...")
        try:
            lights = self._get_client().list_lights()
            await emitter.success_update(f"Found {len(lights)} lights")
            return json.dumps({"ok": True, "lights": lights})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def get_light(
        self,
        light_id_or_name: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Get detailed state of a specific light by its ID number or name.

        :param light_id_or_name: The light ID (e.g. "1") or light name (e.g. "Living Room Lamp").
        :return: JSON with the light's current state.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            lid = client.resolve_light(light_id_or_name)
            for l in client.list_lights():
                if l["id"] == lid:
                    await emitter.success_update(f"Light: {l['name']}")
                    return json.dumps({"ok": True, "light": l})
            return json.dumps(
                {"ok": False, "error": f"Light not found: {light_id_or_name}"}
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def find_light_by_name(
        self,
        name: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Search for a light by name (case-insensitive). Use when the user mentions a light by its name.

        :param name: The light name to search for.
        :return: JSON with the matching light or error.
        """
        return await self.get_light(name, __event_emitter__)

    # ── Lights: Control ──

    async def turn_on_light(
        self,
        light_id_or_name: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Turn on a specific light by ID or name.

        :param light_id_or_name: Light ID or name.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            lid = client.resolve_light(light_id_or_name)
            client.set_light(lid, {"on": True})
            await emitter.success_update(f"Light {lid} turned on")
            return json.dumps({"ok": True, "light_id": lid, "state": "on"})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def turn_off_light(
        self,
        light_id_or_name: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Turn off a specific light by ID or name.

        :param light_id_or_name: Light ID or name.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            lid = client.resolve_light(light_id_or_name)
            client.set_light(lid, {"on": False})
            await emitter.success_update(f"Light {lid} turned off")
            return json.dumps({"ok": True, "light_id": lid, "state": "off"})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def set_brightness(
        self,
        light_id_or_name: str,
        brightness: int,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Set the brightness of a specific light. Range is 1 (dimmest) to 254 (brightest). Automatically turns the light on.

        :param light_id_or_name: Light ID or name.
        :param brightness: Brightness level from 1 to 254.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            lid = client.resolve_light(light_id_or_name)
            bri = _coerce_bri(brightness)
            client.set_light(lid, {"on": True, "bri": bri})
            await emitter.success_update(f"Light {lid} brightness set to {bri}")
            return json.dumps({"ok": True, "light_id": lid, "brightness": bri})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def set_color_rgb(
        self,
        light_id_or_name: str,
        r: int,
        g: int,
        b: int,
        brightness: int = 0,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Set a light's color using RGB values (0-255 each). Automatically converts to Hue-compatible xy color. Turns the light on.

        :param light_id_or_name: Light ID or name.
        :param r: Red value 0-255.
        :param g: Green value 0-255.
        :param b: Blue value 0-255.
        :param brightness: Optional brightness 1-254. Pass 0 to keep current.
        :return: JSON confirmation with xy coordinates.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            lid = client.resolve_light(light_id_or_name)
            x, y = rgb_to_xy(r, g, b)
            payload = {"on": True, "xy": [x, y]}
            if brightness > 0:
                payload["bri"] = _coerce_bri(brightness)
            client.set_light(lid, payload)
            await emitter.success_update(f"Light {lid} color set to RGB({r},{g},{b})")
            return json.dumps(
                {"ok": True, "light_id": lid, "xy": [x, y], "rgb": [r, g, b]}
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def set_color_temperature(
        self,
        light_id_or_name: str,
        kelvin: int,
        brightness: int = 0,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Set a light's white color temperature. Use Kelvin scale: 2000K (very warm/orange) to 6500K (daylight/cool). Turns the light on.

        :param light_id_or_name: Light ID or name.
        :param kelvin: Color temperature in Kelvin (2000-6500).
        :param brightness: Optional brightness 1-254. Pass 0 to keep current.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            lid = client.resolve_light(light_id_or_name)
            mired = int(1000000 / max(2000, min(6500, kelvin)))
            payload = {"on": True, "ct": _coerce_ct(mired)}
            if brightness > 0:
                payload["bri"] = _coerce_bri(brightness)
            client.set_light(lid, payload)
            await emitter.success_update(f"Light {lid} color temp set to {kelvin}K")
            return json.dumps(
                {"ok": True, "light_id": lid, "kelvin": kelvin, "mired": mired}
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def alert_light(
        self,
        light_id_or_name: str,
        mode: str = "select",
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Make a light flash/blink as an alert. Use 'select' for one blink, 'lselect' for 15 seconds of blinking, 'none' to stop.

        :param light_id_or_name: Light ID or name.
        :param mode: Alert mode - 'select', 'lselect', or 'none'.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            lid = client.resolve_light(light_id_or_name)
            mode = str(mode).lower()
            if mode not in ("select", "lselect", "none"):
                return json.dumps(
                    {
                        "ok": False,
                        "error": "mode must be 'select', 'lselect', or 'none'",
                    }
                )
            client.set_light(lid, {"alert": mode})
            await emitter.success_update(f"Light {lid} alert: {mode}")
            return json.dumps({"ok": True, "light_id": lid, "alert": mode})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def set_light_effect(
        self,
        light_id_or_name: str,
        effect: str = "colorloop",
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Set a dynamic effect on a light. Use 'colorloop' to cycle through colors, 'none' to stop.

        :param light_id_or_name: Light ID or name.
        :param effect: 'colorloop' or 'none'.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            lid = client.resolve_light(light_id_or_name)
            effect = str(effect).lower()
            if effect not in ("colorloop", "none"):
                return json.dumps(
                    {"ok": False, "error": "effect must be 'colorloop' or 'none'"}
                )
            client.set_light(lid, {"on": True, "effect": effect})
            await emitter.success_update(f"Light {lid} effect: {effect}")
            return json.dumps({"ok": True, "light_id": lid, "effect": effect})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    # ── Groups: Read ──

    async def get_all_groups(
        self,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Get a list of all light groups (rooms/zones). Use this to discover group IDs before controlling them.

        :return: JSON list of all groups.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            groups = self._get_client().list_groups()
            await emitter.success_update(f"Found {len(groups)} groups")
            return json.dumps({"ok": True, "groups": groups})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def get_group(
        self,
        group_id_or_name: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Get details of a specific group by ID or name.

        :param group_id_or_name: Group ID or name.
        :return: JSON with group details.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            gid = client.resolve_group(group_id_or_name)
            for g in client.list_groups():
                if g["id"] == gid:
                    await emitter.success_update(f"Group: {g['name']}")
                    return json.dumps({"ok": True, "group": g})
            return json.dumps(
                {"ok": False, "error": f"Group not found: {group_id_or_name}"}
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def create_group(
        self,
        name: str,
        light_ids: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Create a new light group with the specified lights. Provide light IDs as comma-separated string.

        :param name: Name for the new group (e.g. "Bedroom").
        :param light_ids: Comma-separated light IDs (e.g. "3,4,5").
        :return: JSON with new group ID.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            ids = [int(x.strip()) for x in light_ids.split(",") if x.strip()]
            gid = self._get_client().create_group(name, ids)
            await emitter.success_update(f"Created group '{name}' with ID {gid}")
            return json.dumps(
                {"ok": True, "group_id": gid, "name": name, "lights": ids}
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    # ── Groups: Control ──

    async def turn_on_group(
        self,
        group_id_or_name: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Turn on all lights in a group (room/zone) by ID or name.

        :param group_id_or_name: Group ID or name.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            gid = client.resolve_group(group_id_or_name)
            client.set_group(gid, {"on": True})
            await emitter.success_update(f"Group {gid} turned on")
            return json.dumps({"ok": True, "group_id": gid, "state": "on"})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def turn_off_group(
        self,
        group_id_or_name: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Turn off all lights in a group (room/zone) by ID or name.

        :param group_id_or_name: Group ID or name.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            gid = client.resolve_group(group_id_or_name)
            client.set_group(gid, {"on": False})
            await emitter.success_update(f"Group {gid} turned off")
            return json.dumps({"ok": True, "group_id": gid, "state": "off"})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def set_group_brightness(
        self,
        group_id_or_name: str,
        brightness: int,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Set brightness for all lights in a group. Range 1-254. Turns the group on.

        :param group_id_or_name: Group ID or name.
        :param brightness: Brightness 1-254.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            gid = client.resolve_group(group_id_or_name)
            bri = _coerce_bri(brightness)
            client.set_group(gid, {"on": True, "bri": bri})
            await emitter.success_update(f"Group {gid} brightness set to {bri}")
            return json.dumps({"ok": True, "group_id": gid, "brightness": bri})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def set_group_color_rgb(
        self,
        group_id_or_name: str,
        r: int,
        g: int,
        b: int,
        brightness: int = 0,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Set RGB color for all lights in a group. Turns the group on.

        :param group_id_or_name: Group ID or name.
        :param r: Red 0-255.
        :param g: Green 0-255.
        :param b: Blue 0-255.
        :param brightness: Optional brightness 1-254. Pass 0 to keep current.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            gid = client.resolve_group(group_id_or_name)
            x, y = rgb_to_xy(r, g, b)
            payload = {"on": True, "xy": [x, y]}
            if brightness > 0:
                payload["bri"] = _coerce_bri(brightness)
            client.set_group(gid, payload)
            await emitter.success_update(f"Group {gid} color set to RGB({r},{g},{b})")
            return json.dumps(
                {"ok": True, "group_id": gid, "xy": [x, y], "rgb": [r, g, b]}
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    # ── Scenes ──

    async def get_all_scenes(
        self,
        group_id: str = "",
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        List all available scenes. Optionally filter by group ID.

        :param group_id: Optional group ID to filter scenes. Leave blank for all.
        :return: JSON list of scenes.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            gid = int(group_id) if group_id.strip() else None
            scenes = self._get_client().list_scenes(group_id=gid)
            await emitter.success_update(f"Found {len(scenes)} scenes")
            return json.dumps({"ok": True, "scenes": scenes})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def set_scene(
        self,
        group_id_or_name: str,
        scene_name_or_id: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Activate a scene on a group. Turns the group on and applies the scene. Match by scene name (case-insensitive) or scene ID.

        :param group_id_or_name: Group ID or name.
        :param scene_name_or_id: Scene name or scene ID.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            gid = client.resolve_group(group_id_or_name)
            scenes = client.list_scenes(group_id=gid)
            target = None
            for s in scenes:
                if s["id"] == scene_name_or_id:
                    target = s
                    break
            if target is None:
                n = scene_name_or_id.strip().lower()
                for s in scenes:
                    if str(s["name"]).strip().lower() == n:
                        target = s
                        break
            if target is None:
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"Scene not found in group {gid}: {scene_name_or_id}",
                    }
                )
            client.set_group(gid, {"on": True})
            client.set_group(gid, {"scene": target["id"]})
            await emitter.success_update(
                f"Scene '{target['name']}' applied to group {gid}"
            )
            return json.dumps(
                {
                    "ok": True,
                    "group_id": gid,
                    "scene_id": target["id"],
                    "scene_name": target["name"],
                }
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    # ── Presets ──

    async def set_color_preset(
        self,
        light_id_or_name: str,
        preset: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Apply a named color preset to a single light. Available presets: reading, relaxation, concentration, energize, bright, dimmed, nightlight, sleep, warm, cool, vivid, calm.

        :param light_id_or_name: Light ID or name.
        :param preset: Preset name.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            lid = client.resolve_light(light_id_or_name)
            p = COLOR_PRESETS.get(preset.strip().lower())
            if not p:
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"Unknown preset: {preset}. Available: {', '.join(COLOR_PRESETS.keys())}",
                    }
                )
            payload = {"on": True}
            if "ct" in p:
                payload["ct"] = _coerce_ct(p["ct"])
            if "xy" in p:
                payload["xy"] = list(p["xy"])
            if "bri" in p:
                payload["bri"] = _coerce_bri(p["bri"])
            client.set_light(lid, payload)
            await emitter.success_update(f"Light {lid} set to '{preset}' preset")
            return json.dumps(
                {"ok": True, "light_id": lid, "preset": preset, "applied": payload}
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def set_group_color_preset(
        self,
        group_id_or_name: str,
        preset: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Apply a named color preset to all lights in a group. Available presets: reading, relaxation, concentration, energize, bright, dimmed, nightlight, sleep, warm, cool, vivid, calm.

        :param group_id_or_name: Group ID or name.
        :param preset: Preset name.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            gid = client.resolve_group(group_id_or_name)
            p = COLOR_PRESETS.get(preset.strip().lower())
            if not p:
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"Unknown preset: {preset}. Available: {', '.join(COLOR_PRESETS.keys())}",
                    }
                )
            payload = {"on": True}
            if "ct" in p:
                payload["ct"] = _coerce_ct(p["ct"])
            if "xy" in p:
                payload["xy"] = list(p["xy"])
            if "bri" in p:
                payload["bri"] = _coerce_bri(p["bri"])
            client.set_group(gid, payload)
            await emitter.success_update(f"Group {gid} set to '{preset}' preset")
            return json.dumps(
                {"ok": True, "group_id": gid, "preset": preset, "applied": payload}
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    async def quick_scene(
        self,
        group_id_or_name: str,
        preset: str,
        brightness: int = 0,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Quickly set a mood on a group using a preset with optional brightness override. Combines turning on the group, applying the preset, and setting brightness in one call.

        :param group_id_or_name: Group ID or name.
        :param preset: Preset name (reading, relaxation, concentration, energize, bright, dimmed, nightlight, sleep, warm, cool, vivid, calm).
        :param brightness: Optional brightness override 1-254. Pass 0 for preset default.
        :return: JSON confirmation.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            gid = client.resolve_group(group_id_or_name)
            p = COLOR_PRESETS.get(preset.strip().lower())
            if not p:
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"Unknown preset: {preset}. Available: {', '.join(COLOR_PRESETS.keys())}",
                    }
                )
            payload = {"on": True}
            if "ct" in p:
                payload["ct"] = _coerce_ct(p["ct"])
            if "xy" in p:
                payload["xy"] = list(p["xy"])
            if brightness > 0:
                payload["bri"] = _coerce_bri(brightness)
            elif "bri" in p:
                payload["bri"] = _coerce_bri(p["bri"])
            client.set_group(gid, payload)
            await emitter.success_update(
                f"Quick scene '{preset}' applied to group {gid}"
            )
            return json.dumps(
                {"ok": True, "group_id": gid, "preset": preset, "applied": payload}
            )
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})

    # ── Maintenance ──

    async def refresh_lights(
        self,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Refresh the bridge cache and return updated light information. Use when light states seem stale or after physical changes.

        :return: JSON with refreshed light list.
        """
        emitter = EventEmitter(__event_emitter__)
        try:
            client = self._get_client()
            client.refresh()
            lights = client.list_lights()
            await emitter.success_update(f"Refreshed: {len(lights)} lights")
            return json.dumps({"ok": True, "lights": lights})
        except Exception as e:
            await emitter.error_update(str(e))
            return json.dumps({"ok": False, "error": str(e)})
