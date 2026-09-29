from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Any, Iterable

from .const import (
    CONF_SOC_1,
    CONF_SOC_2,
    CONF_SOC_3,
    DPU_BATTERY_PACK_CAPACITY_KWH,
)

ECOFLOW_IOT_DOMAIN = "ecoflow_iot"
_DPU_MODEL = "EcoFlow Delta Pro Ultra"
_DPU_PACK_COUNT_KEY = "hs_yj751_pd_appshow_addr.bpNum"
_DPU_SOC_KEY = "hs_yj751_pd_appshow_addr.soc"
_INVALID_STATES = {"unknown", "unavailable", "none", ""}
TOPOLOGY_STORAGE_VERSION = 1


@dataclass(frozen=True, slots=True)
class BatteryTopology:
    """Confirmed physical battery topology used by the planner."""

    source: str
    reason: str
    capacity_kwh: float
    bank_socs_pct: tuple[float, float, float] | None
    bank_capacities_kwh: tuple[float, float, float]
    pack_counts: tuple[int, int, int]
    bank_ids: tuple[str, str, str]
    discovered_dpu_count: int

    @property
    def total_pack_count(self) -> int:
        return sum(self.pack_counts)

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


def _configured_socs(
    hass,
    cfg: dict[str, Any],
) -> tuple[float, float, float] | None:
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


def _coordinator_dpu_samples(hass) -> list[_DpuSample]:
    """Read DPU pack topology directly from EcoFlow IoT's quota cache."""
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

    if configured_socs is None and previous is not None:
        by_serial = {sample.serial: sample for sample in values}
        if all(serial in by_serial for serial in previous.bank_ids):
            ordered = tuple(by_serial[serial] for serial in previous.bank_ids)
            return ordered[0], ordered[1], ordered[2]

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


def _capacities(
    pack_counts: tuple[int, int, int],
) -> tuple[float, float, float]:
    return tuple(
        count * DPU_BATTERY_PACK_CAPACITY_KWH for count in pack_counts
    )  # type: ignore[return-value]


def serialize_battery_topology(topology: BatteryTopology) -> dict[str, Any]:
    """Serialize only stable physical topology, never transient SOC values."""
    return {
        "version": TOPOLOGY_STORAGE_VERSION,
        "pack_counts": list(topology.pack_counts),
        "bank_ids": list(topology.bank_ids),
    }


def deserialize_battery_topology(
    raw: Any,
) -> BatteryTopology | None:
    """Restore the last confirmed EcoFlow topology for startup continuity."""
    if not isinstance(raw, dict):
        return None
    counts_raw = raw.get("pack_counts")
    ids_raw = raw.get("bank_ids")
    if (
        not isinstance(counts_raw, (list, tuple))
        or len(counts_raw) != 3
        or not isinstance(ids_raw, (list, tuple))
        or len(ids_raw) != 3
    ):
        return None

    counts: list[int] = []
    for value in counts_raw:
        numeric = _float(value)
        if numeric is None:
            return None
        count = int(round(numeric))
        if abs(numeric - count) > 0.01 or not 1 <= count <= 10:
            return None
        counts.append(count)

    ids = tuple(str(value) for value in ids_raw)
    if any(not value for value in ids):
        return None

    pack_counts = counts[0], counts[1], counts[2]
    bank_capacities = _capacities(pack_counts)
    return BatteryTopology(
        source="ecoflow_iot_cached",
        reason="Persisted last-known-good EcoFlow DPU topology",
        capacity_kwh=sum(bank_capacities),
        bank_socs_pct=None,
        bank_capacities_kwh=bank_capacities,
        pack_counts=pack_counts,
        bank_ids=(ids[0], ids[1], ids[2]),
        discovered_dpu_count=0,
    )


def same_physical_topology(
    left: BatteryTopology | None,
    right: BatteryTopology | None,
) -> bool:
    if left is None or right is None:
        return False
    return (
        left.pack_counts == right.pack_counts
        and abs(left.capacity_kwh - right.capacity_kwh) <= 0.01
    )


def _cached_topology(
    hass,
    cfg: dict[str, Any],
    previous: BatteryTopology,
    *,
    reason: str,
    discovered_dpu_count: int,
) -> BatteryTopology:
    return BatteryTopology(
        source="ecoflow_iot_cached",
        reason=reason,
        capacity_kwh=previous.capacity_kwh,
        bank_socs_pct=_configured_socs(hass, cfg),
        bank_capacities_kwh=previous.bank_capacities_kwh,
        pack_counts=previous.pack_counts,
        bank_ids=previous.bank_ids,
        discovered_dpu_count=discovered_dpu_count,
    )


def resolve_battery_topology(
    hass,
    cfg: dict[str, Any],
    *,
    previous: BatteryTopology | None = None,
) -> BatteryTopology | None:
    """Resolve current topology or use only a persisted/confirmed EcoFlow topology.

    Manual capacity and SOC-weight fallbacks are intentionally not used. Before
    the first successful EcoFlow discovery, callers should wait rather than
    fabricate a physical battery model. After discovery, the last-known-good
    topology bridges Home Assistant startup ordering and temporary EcoFlow gaps.
    """
    samples = _coordinator_dpu_samples(hass)
    configured_socs = _configured_socs(hass, cfg)
    mapping = _select_three(samples, configured_socs, previous)

    if mapping is None:
        if previous is None:
            return None
        return _cached_topology(
            hass,
            cfg,
            previous,
            reason=(
                "EcoFlow IoT DPU topology temporarily incomplete; "
                "using persisted last-known-good pack counts"
            ),
            discovered_dpu_count=len(samples),
        )

    pack_counts = tuple(sample.pack_count for sample in mapping)
    bank_capacities = _capacities(pack_counts)  # type: ignore[arg-type]
    bank_socs = configured_socs or tuple(sample.soc_pct for sample in mapping)

    return BatteryTopology(
        source="ecoflow_iot",
        reason="Delta Pro Ultra bpNum queried from EcoFlow IoT coordinator",
        capacity_kwh=sum(bank_capacities),
        bank_socs_pct=bank_socs,  # type: ignore[arg-type]
        bank_capacities_kwh=bank_capacities,
        pack_counts=pack_counts,  # type: ignore[arg-type]
        bank_ids=tuple(sample.serial for sample in mapping),  # type: ignore[arg-type]
        discovered_dpu_count=len(samples),
    )
