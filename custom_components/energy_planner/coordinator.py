from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_BACKUP_RESERVE,
    CONF_CAPACITY_KWH,
    CONF_CHARGE_LIMIT,
    CONF_EV_HOME,
    CONF_EV_SOC,
    CONF_SOC_1,
    CONF_SOC_2,
    CONF_SOC_3,
    CONF_SOC_WEIGHTS,
    CONF_SOLAR_TODAY,
    CONF_SOLAR_TOMORROW,
    CONF_STORM_WARNING,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_EV_TARGET_SOC,
    DEFAULT_MIN_RESERVE,
    DEFAULT_STRONG_SOLAR_KWH,
    DEFAULT_WEIGHTS,
    OPT_AUTO_HEADROOM,
    OPT_EV_TARGET_SOC,
    OPT_MIN_RESERVE,
    OPT_STRONG_SOLAR_KWH,
)


def _num(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in {"unknown", "unavailable", "none", ""}:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _is_on(hass: HomeAssistant, entity_id: str | None) -> bool | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in {"unknown", "unavailable"}:
        return None
    return state.state == "on"


class EnergyPlannerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name="Energy Planner",
            update_interval=timedelta(minutes=1),
        )
        self.entry = entry

    @property
    def cfg(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    async def _async_update_data(self) -> dict[str, Any]:
        cfg = self.cfg
        weights_raw = str(cfg.get(CONF_SOC_WEIGHTS, DEFAULT_WEIGHTS))
        try:
            weights = [float(x.strip()) for x in weights_raw.split(",")]
        except ValueError:
            weights = [3.0, 2.0, 3.0]
        if len(weights) != 3 or sum(weights) <= 0:
            weights = [3.0, 2.0, 3.0]

        socs = [
            _num(self.hass, cfg.get(CONF_SOC_1)),
            _num(self.hass, cfg.get(CONF_SOC_2)),
            _num(self.hass, cfg.get(CONF_SOC_3)),
        ]
        weighted_soc = None
        if all(v is not None for v in socs):
            weighted_soc = sum(v * w for v, w in zip(socs, weights)) / sum(weights)

        capacity = float(cfg.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        stored = None if weighted_soc is None else capacity * weighted_soc / 100.0
        charge_limit = _num(self.hass, cfg.get(CONF_CHARGE_LIMIT))
        headroom = None
        if stored is not None and charge_limit is not None:
            headroom = max(capacity * charge_limit / 100.0 - stored, 0.0)

        solar_today = _num(self.hass, cfg.get(CONF_SOLAR_TODAY))
        solar_tomorrow = _num(self.hass, cfg.get(CONF_SOLAR_TOMORROW))
        upcoming = solar_today if self.hass.config.time_zone and __import__("homeassistant.util.dt", fromlist=["now"]).now().hour < 12 else solar_tomorrow

        reserve = _num(self.hass, cfg.get(CONF_BACKUP_RESERVE))
        storm = _is_on(self.hass, cfg.get(CONF_STORM_WARNING))
        minimum_reserve = float(cfg.get(OPT_MIN_RESERVE, DEFAULT_MIN_RESERVE))
        strong_solar = float(cfg.get(OPT_STRONG_SOLAR_KWH, DEFAULT_STRONG_SOLAR_KWH))
        auto_headroom = bool(cfg.get(OPT_AUTO_HEADROOM, False))

        ready = reserve is not None and storm is not None and upcoming is not None
        release = bool(
            ready
            and auto_headroom
            and not storm
            and upcoming >= strong_solar
            and reserve > minimum_reserve + 0.5
        )

        ev_soc = _num(self.hass, cfg.get(CONF_EV_SOC))
        ev_home_entity = cfg.get(CONF_EV_HOME)
        ev_home = None
        if ev_home_entity:
            state = self.hass.states.get(ev_home_entity)
            ev_home = None if state is None else state.state == "home"
        ev_target = float(cfg.get(OPT_EV_TARGET_SOC, DEFAULT_EV_TARGET_SOC))
        if ev_soc is None:
            ev_plan = "EV SOC unavailable"
        elif ev_home is False:
            ev_plan = "EV away"
        elif ev_soc >= ev_target - 1:
            ev_plan = "No charge needed"
        elif solar_tomorrow is not None and solar_tomorrow >= strong_solar:
            ev_plan = "Defer grid charging for solar"
        else:
            ev_plan = "Grid charge when convenient"

        return {
            "weighted_soc": weighted_soc,
            "stored_energy": stored,
            "battery_headroom": headroom,
            "upcoming_solar": upcoming,
            "reserve": reserve,
            "storm": storm,
            "control_ready": ready,
            "headroom_release": release,
            "ev_soc": ev_soc,
            "ev_plan": ev_plan,
        }

    async def async_apply_headroom_policy(self) -> None:
        await self.async_request_refresh()
        data = self.data or {}
        if not data.get("headroom_release"):
            return
        reserve_entity = self.cfg.get(CONF_BACKUP_RESERVE)
        minimum = float(self.cfg.get(OPT_MIN_RESERVE, DEFAULT_MIN_RESERVE))
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": reserve_entity, "value": minimum},
            blocking=True,
        )
        await self.async_request_refresh()
