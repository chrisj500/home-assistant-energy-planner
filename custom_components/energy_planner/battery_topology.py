from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Any, Iterable

from .const import (
    CONF_CAPACITY_KWH,
    CONF_SOC_1,
    CONF_SOC_2,
    CONF_SOC_3,
    CONF_SOC_WEIGHTS,
    DEFAULT_AUTO_BATTERY_TOPOLOGY,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_WEIGHTS,
    DPU_BATTERY_PACK_CAPACITY_KWH,
    OPT_AUTO_BATTERY_TOPOLOGY,
)

ECOFLOW_IOT_DOMAIN = "ecoflow_iot"
_DPU_MODEL = "EcoFlow Delta Pro Ultra"
_DPU_PACK_COUNT_KEY = "hs_yj751_pd_appshow_addr.bpNum"
_DPU_SOC_KEY = "hs_yj751_pd_appshow_addr.soc"
_INVALID_STATES = {"unknown", "unavailable", "none", ""}


@dataclass(frozen=True, slots=True)
class BatteryTopology:
    """Resolved battery model used by the planner."""

    source: str
    reason: str
    capacity_kwh: float
    bank_socs_pct: tuple[float, float, float] | None
    bank_capacities_kwh: tuple[float, float, float]
    pack_counts: tuple[int, int, int] | None
    bank_ids: tuple[str, str, str] | None
    discovered_dpu_count: int
    configured_capacity_kwh: float
    configured_weights: tuple[float, float, float]

    @property
    def total_pack_count(self) -> int | None:
        return None if self.pack_counts is None else sum(self.pack_counts)

    @property
    def auto_detected(self) -> bool:
        return self.source in {"ecoflow_iot", "ecoflow_iot_cached"}


@dataclass(frozen=True, slots=True)
class _DpuSample:
    serial: str
    pack_count: int
    soc_pct: float


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _parse_weights(raw: Any) -> tuple[float, float, float]:
    try:
        values = tuple(float(part.strip()) for part in str(raw).split(","))
    except (TypeError, ValueError):
        values = tuple(float(part) for part in DEFAULT_WEIGHTS.split(","))
    if len(values) != 3 or sum(values) <= 0 or any(value <= 0 for value in values):
        values = tuple(float(part) for part in DEFAULT_WEIGHTS.split(","))
    return values  # type: ignore[return-value]


def _configured_socs(hass, cfg: dict[str, Any]) -> tuple[float, float, float] | None:
    values: list[float] = []
    for key in (CONF_SOC_1, CONF_SOC_2, CONF_SOC_3):
        entity_id = cfg.get(key)
        state = hass.states.get(entity_id) if entity_id else None
        if state is None or str(state.state).lower() in _INVALID_STATES:
            return None
        value = _float(state.state)
        if value is None or not 0.0 <= value <= 100.0:
            return None
        values.append(value)
    return values[0], values[1], values[2]


def _manual_topology(
    hass,
    cfg: dict[str, Any],
    *,
    reason: str,
    discovered_dpu_count: int = 0,
) -> BatteryTopology:
    configured_capacity = _float(cfg.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
    if configured_capacity is None or configured_capacity <= 0:
        configured_capacity = DEFAULT_CAPACITY_KWH
    weights = _parse_weights(cfg.get(CONF_SOC_WEIGHTS, DEFAULT_WEIGHTS))
    total_weight = sum(weights)
    capacities = tuple(
        configured_capacity * weight / total_weight for weight in weights
    )
    return BatteryTopology(
        source="configured",
        reason=reason,
        capacity_kwh=configured_capacity,
        bank_socs_pct=_configured_socs(hass, cfg),
        bank_capacities_kwh=capacities,  # type: ignore[arg-type]
        pack_counts=None,
        bank_ids=None,
        discovered_dpu_count=discovered_dpu_count,
        configured_capacity_kwh=configured_capacity,
        configured_weights=weights,
    )


def _coordinator_dpu_samples(hass) -> list[_DpuSample]:
    """Read DPU topology directly from EcoFlow IoT's refreshed quota cache.

    This does not depend on the optional Home Assistant Battery pack count
    entity being enabled. EcoFlow IoT keeps bpNum in its coordinator quota even
    though the entity is diagnostic and disabled by default.
    """
    samples: list[_DpuSample] = []
    try:
        entries = hass.config_entries.async_entries(ECOFLOW_IOT_DOMAIN)
    except (AttributeError, TypeError):
        return samples

    for entry in entries:
        coordinator = getattr(entry, "runtime_data", None)
        devices = getattr(coordinator, "devices", None)
        states = getattr(coordinator, "data", None)
        if not isinstance(devices, dict) or not isinstance(states, dict):
            continue

        for serial, device in devices.items():
            state = states.get(serial)
            quota = getattr(state, "quota", None)
            if not isinstance(quota, dict):
                continue

            model = str(getattr(device, "model", "") or "")
            if model != _DPU_MODEL and _DPU_PACK_COUNT_KEY not in quota:
                continue

            raw_count = _float(quota.get(_DPU_PACK_COUNT_KEY))
            raw_soc = _float(quota.get(_DPU_SOC_KEY))
            if raw_count is None or raw_soc is None:
                continue

            count = int(round(raw_count))
            if abs(raw_count - count) > 0.01 or not 1 <= count <= 10:
                continue
            if not 0.0 <= raw_soc <= 100.0:
                continue

            samples.append(
                _DpuSample(
                    serial=str(serial),
                    pack_count=count,
                    soc_pct=raw_soc,
                )
            )

    deduped: dict[str, _DpuSample] = {}
    for sample in samples:
        deduped[sample.serial] = sample
    return list(deduped.values())


def _mapping_score(
    mapping: tuple[_DpuSample, _DpuSample, _DpuSample],
    configured_socs: tuple[float, float, float],
    previous: BatteryTopology | None,
) -> tuple[float, int, tuple[str, str, str]]:
    soc_error = sum(
        abs(sample.soc_pct - configured_socs[index])
        for index, sample in enumerate(mapping)
    )
    previous_ids = previous.bank_ids if previous is not None else None
    churn = (
        sum(
            1
            for index, sample in enumerate(mapping)
            if previous_ids is not None and sample.serial != previous_ids[index]
        )
        if previous_ids is not None
        else 0
    )
    return round(soc_error, 6), churn, tuple(sample.serial for sample in mapping)


def _select_three(
    samples: Iterable[_DpuSample],
    configured_socs: tuple[float, float, float] | None,
    previous: BatteryTopology | None,
) -> tuple[_DpuSample, _DpuSample, _DpuSample] | None:
    values = tuple(samples)
    if len(values) < 3:
        return None

    if len(values) == 3 and configured_socs is None:
        ordered = tuple(sorted(values, key=lambda item: item.serial))
        return ordered[0], ordered[1], ordered[2]

    if configured_socs is None:
        return None

    best = min(
        permutations(values, 3),
        key=lambda mapping: _mapping_score(mapping, configured_socs, previous),
        default=None,
    )
    return best


def _cached_topology(
    hass,
    cfg: dict[str, Any],
    previous: BatteryTopology,
    *,
    reason: str,
    discovered_dpu_count: int,
) -> BatteryTopology:
    configured_socs = _configured_socs(hass, cfg)
    bank_socs = configured_socs or previous.bank_socs_pct
    return BatteryTopology(
        source="ecoflow_iot_cached",
        reason=reason,
        capacity_kwh=previous.capacity_kwh,
        bank_socs_pct=bank_socs,
        bank_capacities_kwh=previous.bank_capacities_kwh,
        pack_counts=previous.pack_counts,
        bank_ids=previous.bank_ids,
        discovered_dpu_count=discovered_dpu_count,
        configured_capacity_kwh=previous.configured_capacity_kwh,
        configured_weights=previous.configured_weights,
    )


def resolve_battery_topology(
    hass,
    cfg: dict[str, Any],
    *,
    previous: BatteryTopology | None = None,
) -> BatteryTopology:
    """Resolve live battery capacity and per-bank SOC/capacity.

    EcoFlow IoT exposes each Delta Pro Ultra's bpNum in its coordinator quota.
    The planner queries that cache every refresh. Manual capacity and weights
    remain a compatibility fallback and can be forced by disabling
    auto_battery_topology.
    """
    configured = _manual_topology(hass, cfg, reason="manual_configuration")
    auto = bool(
        cfg.get(
            OPT_AUTO_BATTERY_TOPOLOGY,
            DEFAULT_AUTO_BATTERY_TOPOLOGY,
        )
    )
    if not auto:
        return configured

    samples = _coordinator_dpu_samples(hass)
    configured_socs = configured.bank_socs_pct
    mapping = _select_three(samples, configured_socs, previous)

    if mapping is None:
        if (
            previous is not None
            and previous.auto_detected
            and previous.pack_counts is not None
            and previous.bank_ids is not None
        ):
            return _cached_topology(
                hass,
                cfg,
                previous,
                reason=(
                    "EcoFlow IoT DPU topology temporarily incomplete; "
                    "using last confirmed pack counts"
                ),
                discovered_dpu_count=len(samples),
            )
        return _manual_topology(
            hass,
            cfg,
            reason=(
                "EcoFlow IoT did not provide three matchable Delta Pro Ultra "
                "pack-count/SOC records"
            ),
            discovered_dpu_count=len(samples),
        )

    pack_counts = tuple(sample.pack_count for sample in mapping)
    capacities = tuple(
        count * DPU_BATTERY_PACK_CAPACITY_KWH for count in pack_counts
    )
    capacity = sum(capacities)
    bank_socs = configured_socs or tuple(sample.soc_pct for sample in mapping)

    return BatteryTopology(
        source="ecoflow_iot",
        reason="Delta Pro Ultra bpNum queried from EcoFlow IoT coordinator",
        capacity_kwh=capacity,
        bank_socs_pct=bank_socs,  # type: ignore[arg-type]
        bank_capacities_kwh=capacities,  # type: ignore[arg-type]
        pack_counts=pack_counts,  # type: ignore[arg-type]
        bank_ids=tuple(sample.serial for sample in mapping),  # type: ignore[arg-type]
        discovered_dpu_count=len(samples),
        configured_capacity_kwh=configured.configured_capacity_kwh,
        configured_weights=configured.configured_weights,
    )
