from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util

from .const import CONF_EV_CHARGING_POWER, CONF_EV_SOC
from .coordinator import _num
from .ev_learning import (
    infer_anchored_wall_energy_full_kwh,
    soc_observation_is_trusted,
)
from .v014_coordinator import (
    EnergyPlannerV014Coordinator,
    _MAX_EV_ENERGY_SAMPLES,
    _MAX_EV_POWER_SAMPLES,
    _MAX_SAMPLE_GAP_SECONDS,
    _SOC_WAIT_AFTER_CHARGE,
)

_EV_LEARNING_SCHEMA = 2
_SOC_ANCHOR_PRESTART_MAX_AGE_MINUTES = 5.0
_SOC_ANCHOR_LIVE_MAX_AGE_MINUTES = 30.0


class EnergyPlannerV015Coordinator(EnergyPlannerV014Coordinator):
    """v0.1.15 rejects EV wall-energy samples built from stale SOC anchors."""

    async def _ensure_ev_learning_data(self) -> dict:
        if self._ev_learning_data is not None:
            return self._ev_learning_data

        loaded = await self._ev_learning_store.async_load()
        if not isinstance(loaded, dict):
            loaded = {}

        migrated = loaded.get("learning_schema") != _EV_LEARNING_SCHEMA
        self._ev_learning_data = {
            "learning_schema": _EV_LEARNING_SCHEMA,
            "power_samples_w": list(loaded.get("power_samples_w", []))[-_MAX_EV_POWER_SAMPLES:],
            # v0.1.13/v0.1.14 wall samples did not verify that the starting SOC
            # was fresh when energy integration began. They are intentionally
            # discarded once when moving to the trusted-anchor schema.
            "wall_full_samples_kwh": (
                []
                if migrated
                else list(loaded.get("wall_full_samples_kwh", []))[-_MAX_EV_ENERGY_SAMPLES:]
            ),
            "session": (
                None
                if migrated
                else loaded.get("session")
                if isinstance(loaded.get("session"), dict)
                else None
            ),
        }
        if migrated:
            await self._save_ev_learning_data()
        return self._ev_learning_data

    def _ev_soc_observation(self):
        entity_id = self.cfg.get(CONF_EV_SOC)
        if not entity_id:
            return None, None, None
        state = self.hass.states.get(entity_id)
        value = _num(self.hass, entity_id)
        if state is None or value is None:
            return value, None, None
        observed_at = state.last_updated
        age_minutes = max((dt_util.now() - observed_at).total_seconds() / 60.0, 0.0)
        return float(value), observed_at, age_minutes

    @staticmethod
    def _integrate_session_to_now(session: dict, *, now, power_w: float) -> None:
        last_at = dt_util.parse_datetime(str(session.get("last_at", "")))
        try:
            last_power = float(session.get("last_power_w", power_w))
            wall_energy = float(session.get("wall_energy_kwh", 0.0))
        except (TypeError, ValueError):
            last_power = float(power_w)
            wall_energy = 0.0
        if last_at is not None:
            elapsed_s = max((now - last_at).total_seconds(), 0.0)
            if 0.0 < elapsed_s <= _MAX_SAMPLE_GAP_SECONDS:
                wall_energy += ((last_power + float(power_w)) / 2.0) * elapsed_s / 3_600_000.0
        session["wall_energy_kwh"] = wall_energy
        session["last_at"] = now.isoformat()
        session["last_power_w"] = float(power_w)

    @staticmethod
    def _record_trusted_soc(session: dict, *, soc: float, observed_at) -> bool:
        previous_at = dt_util.parse_datetime(str(session.get("latest_soc_observed_at", "")))
        if previous_at is not None and observed_at <= previous_at:
            return False
        session["latest_trusted_soc_pct"] = float(soc)
        session["latest_soc_observed_at"] = observed_at.isoformat()
        return True

    async def _update_ev_learning(self) -> None:
        """Learn EV power immediately; learn wall capacity only from trusted SOC anchors."""
        data = await self._ensure_ev_learning_data()
        if not self.cfg.get(CONF_EV_CHARGING_POWER):
            return

        now = dt_util.now()
        power = self._ev_power_w()
        ev_soc, soc_observed_at, soc_age_minutes = self._ev_soc_observation()
        session = data.get("session")
        charging = power is not None and power >= 500.0
        changed = False

        if charging:
            power_samples = [
                float(value)
                for value in data.get("power_samples_w", [])
                if float(value) >= 500.0
            ]
            power_samples.append(float(power))
            data["power_samples_w"] = power_samples[-_MAX_EV_POWER_SAMPLES:]
            changed = True

            if not isinstance(session, dict):
                session = {
                    "started_at": now.isoformat(),
                    "wall_energy_kwh": 0.0,
                    "last_at": now.isoformat(),
                    "last_power_w": float(power),
                    "stopped_at": None,
                    "anchor_soc_pct": None,
                    "anchor_wall_energy_kwh": None,
                    "anchor_observed_at": None,
                    "latest_trusted_soc_pct": None,
                    "latest_soc_observed_at": None,
                }
                if (
                    ev_soc is not None
                    and soc_observation_is_trusted(
                        observed_at=soc_observed_at,
                        session_started_at=now,
                        age_minutes=soc_age_minutes,
                        max_prestart_age_minutes=_SOC_ANCHOR_PRESTART_MAX_AGE_MINUTES,
                        max_live_age_minutes=_SOC_ANCHOR_LIVE_MAX_AGE_MINUTES,
                    )
                ):
                    session["anchor_soc_pct"] = ev_soc
                    session["anchor_wall_energy_kwh"] = 0.0
                    session["anchor_observed_at"] = soc_observed_at.isoformat()
                    self._record_trusted_soc(session, soc=ev_soc, observed_at=soc_observed_at)
            else:
                self._integrate_session_to_now(session, now=now, power_w=float(power))
                session["stopped_at"] = None

                started_at = dt_util.parse_datetime(str(session.get("started_at", "")))
                trusted = (
                    ev_soc is not None
                    and soc_observation_is_trusted(
                        observed_at=soc_observed_at,
                        session_started_at=started_at,
                        age_minutes=soc_age_minutes,
                        max_prestart_age_minutes=_SOC_ANCHOR_PRESTART_MAX_AGE_MINUTES,
                        max_live_age_minutes=_SOC_ANCHOR_LIVE_MAX_AGE_MINUTES,
                    )
                )
                if trusted:
                    if session.get("anchor_soc_pct") is None:
                        session["anchor_soc_pct"] = ev_soc
                        session["anchor_wall_energy_kwh"] = float(
                            session.get("wall_energy_kwh", 0.0) or 0.0
                        )
                        session["anchor_observed_at"] = soc_observed_at.isoformat()
                    self._record_trusted_soc(
                        session,
                        soc=ev_soc,
                        observed_at=soc_observed_at,
                    )
            data["session"] = session

        elif isinstance(session, dict):
            stopped_at = dt_util.parse_datetime(str(session.get("stopped_at", "")))
            if stopped_at is None:
                last_at = dt_util.parse_datetime(str(session.get("last_at", "")))
                try:
                    last_power = float(session.get("last_power_w", 0.0))
                    wall_energy = float(session.get("wall_energy_kwh", 0.0))
                except (TypeError, ValueError):
                    last_power = 0.0
                    wall_energy = 0.0
                if last_at is not None:
                    elapsed_s = max((now - last_at).total_seconds(), 0.0)
                    if 0.0 < elapsed_s <= _MAX_SAMPLE_GAP_SECONDS:
                        wall_energy += last_power * elapsed_s / 3_600_000.0
                session["wall_energy_kwh"] = wall_energy
                session["stopped_at"] = now.isoformat()
                stopped_at = now
                changed = True

            started_at = dt_util.parse_datetime(str(session.get("started_at", "")))
            trusted = (
                ev_soc is not None
                and soc_observation_is_trusted(
                    observed_at=soc_observed_at,
                    session_started_at=started_at,
                    age_minutes=soc_age_minutes,
                    max_prestart_age_minutes=_SOC_ANCHOR_PRESTART_MAX_AGE_MINUTES,
                    max_live_age_minutes=_SOC_ANCHOR_LIVE_MAX_AGE_MINUTES,
                )
            )
            if trusted:
                if session.get("anchor_soc_pct") is None:
                    # A first trustworthy SOC received only after charging stopped
                    # cannot calibrate energy already delivered. Establish it only as
                    # an observational endpoint; the next charge will provide a clean
                    # learning session.
                    session["anchor_soc_pct"] = ev_soc
                    session["anchor_wall_energy_kwh"] = float(
                        session.get("wall_energy_kwh", 0.0) or 0.0
                    )
                    session["anchor_observed_at"] = soc_observed_at.isoformat()
                if self._record_trusted_soc(
                    session,
                    soc=ev_soc,
                    observed_at=soc_observed_at,
                ):
                    changed = True

            inferred = infer_anchored_wall_energy_full_kwh(
                anchor_wall_energy_kwh=session.get("anchor_wall_energy_kwh"),
                observed_wall_energy_kwh=session.get("wall_energy_kwh"),
                anchor_soc_pct=session.get("anchor_soc_pct"),
                observed_soc_pct=session.get("latest_trusted_soc_pct"),
            )
            if inferred is not None:
                samples = [
                    float(value)
                    for value in data.get("wall_full_samples_kwh", [])
                    if 5.0 <= float(value) <= 250.0
                ]
                samples.append(inferred)
                data["wall_full_samples_kwh"] = samples[-_MAX_EV_ENERGY_SAMPLES:]
                data["session"] = None
                changed = True
            elif stopped_at is not None and now - stopped_at >= _SOC_WAIT_AFTER_CHARGE:
                data["session"] = None
                changed = True
            else:
                data["session"] = session

        if changed:
            await self._save_ev_learning_data()

    def _ev_learning_status(self) -> str:
        learning = self._ev_learning_data or {}
        session = learning.get("session")
        if not isinstance(session, dict):
            return "idle"

        charging = self._ev_power_w() is not None and self._ev_power_w() >= 500.0
        if session.get("anchor_soc_pct") is None:
            return (
                "charging_waiting_for_trusted_soc_anchor"
                if charging
                else "charge_complete_without_trusted_soc_anchor"
            )

        start = session.get("anchor_soc_pct")
        latest = session.get("latest_trusted_soc_pct")
        try:
            delta = float(latest) - float(start)
        except (TypeError, ValueError):
            delta = 0.0
        if charging:
            return "charging_learning" if delta >= 5.0 else "charging_waiting_for_soc_update"
        return "charge_complete_waiting_for_soc_update" if delta < 5.0 else "learning_ready"
