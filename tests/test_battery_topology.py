from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "energy_planner"
PACKAGE = "energy_planner_battery_testpkg"

package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

const_spec = importlib.util.spec_from_file_location(f"{PACKAGE}.const", ROOT / "const.py")
const = importlib.util.module_from_spec(const_spec)
sys.modules[f"{PACKAGE}.const"] = const
assert const_spec.loader is not None
const_spec.loader.exec_module(const)

topology_spec = importlib.util.spec_from_file_location(
    f"{PACKAGE}.battery_topology",
    ROOT / "battery_topology.py",
)
topology = importlib.util.module_from_spec(topology_spec)
sys.modules[f"{PACKAGE}.battery_topology"] = topology
assert topology_spec.loader is not None
topology_spec.loader.exec_module(topology)


class States:
    def __init__(self, values):
        self.values = {
            key: SimpleNamespace(state=str(value), attributes={})
            for key, value in values.items()
        }

    def get(self, entity_id):
        return self.values.get(entity_id)


class ConfigEntries:
    def __init__(self, entries):
        self.entries = entries

    def async_entries(self, domain):
        return self.entries if domain == "ecoflow_iot" else []


def dpu(serial: str, packs: int, soc: float):
    return (
        serial,
        SimpleNamespace(model="EcoFlow Delta Pro Ultra"),
        SimpleNamespace(
            online=True,
            quota={
                "hs_yj751_pd_appshow_addr.bpNum": packs,
                "hs_yj751_pd_appshow_addr.soc": soc,
            },
        ),
    )


def hass_with(dpus, socs=(31, 30, 41)):
    devices = {}
    data = {}
    for serial, device, state in dpus:
        devices[serial] = device
        data[serial] = state
    coordinator = SimpleNamespace(devices=devices, data=data)
    entry = SimpleNamespace(runtime_data=coordinator)
    return SimpleNamespace(
        states=States({"s1": socs[0], "s2": socs[1], "s3": socs[2]}),
        config_entries=ConfigEntries([entry]),
    )


def cfg(auto=True):
    return {
        const.CONF_SOC_1: "s1",
        const.CONF_SOC_2: "s2",
        const.CONF_SOC_3: "s3",
        const.CONF_SOC_WEIGHTS: "3,2,3",
        const.CONF_CAPACITY_KWH: 49.152,
        const.OPT_AUTO_BATTERY_TOPOLOGY: auto,
    }


class BatteryTopologyTests(unittest.TestCase):
    def test_queries_bpnum_and_replaces_manual_capacity(self):
        hass = hass_with(
            [
                dpu("dpu-a", 3, 31),
                dpu("dpu-b", 2, 30),
                dpu("dpu-c", 4, 41),
            ]
        )
        result = topology.resolve_battery_topology(hass, cfg())

        self.assertEqual(result.source, "ecoflow_iot")
        self.assertEqual(result.pack_counts, (3, 2, 4))
        self.assertEqual(result.total_pack_count, 9)
        self.assertAlmostEqual(result.capacity_kwh, 55.296, places=3)
        self.assertEqual(result.bank_socs_pct, (31.0, 30.0, 41.0))
        self.assertEqual(
            tuple(round(value, 3) for value in result.bank_capacities_kwh),
            (18.432, 12.288, 24.576),
        )

    def test_pack_change_is_detected_on_next_refresh(self):
        original = hass_with(
            [
                dpu("dpu-a", 3, 31),
                dpu("dpu-b", 2, 30),
                dpu("dpu-c", 3, 41),
            ]
        )
        before = topology.resolve_battery_topology(original, cfg())
        self.assertAlmostEqual(before.capacity_kwh, 49.152, places=3)

        changed = hass_with(
            [
                dpu("dpu-a", 3, 31),
                dpu("dpu-b", 2, 30),
                dpu("dpu-c", 4, 41),
            ]
        )
        after = topology.resolve_battery_topology(changed, cfg(), previous=before)
        self.assertEqual(after.total_pack_count, 9)
        self.assertAlmostEqual(after.capacity_kwh, 55.296, places=3)

    def test_matches_pack_counts_to_shp_bank_socs(self):
        hass = hass_with(
            [
                dpu("dpu-c", 4, 41),
                dpu("dpu-a", 3, 31),
                dpu("dpu-b", 2, 30),
            ],
            socs=(31, 30, 41),
        )
        result = topology.resolve_battery_topology(hass, cfg())
        self.assertEqual(result.pack_counts, (3, 2, 4))
        self.assertEqual(result.bank_ids, ("dpu-a", "dpu-b", "dpu-c"))

    def test_extra_unattached_dpu_is_excluded_by_soc_matching(self):
        hass = hass_with(
            [
                dpu("dpu-a", 3, 31),
                dpu("dpu-b", 2, 30),
                dpu("dpu-c", 4, 41),
                dpu("portable", 5, 88),
            ]
        )
        result = topology.resolve_battery_topology(hass, cfg())
        self.assertEqual(result.discovered_dpu_count, 4)
        self.assertEqual(result.pack_counts, (3, 2, 4))
        self.assertNotIn("portable", result.bank_ids)

    def test_temporary_incomplete_query_keeps_last_confirmed_capacity(self):
        hass = hass_with(
            [
                dpu("dpu-a", 3, 31),
                dpu("dpu-b", 2, 30),
                dpu("dpu-c", 4, 41),
            ]
        )
        confirmed = topology.resolve_battery_topology(hass, cfg())

        incomplete = hass_with(
            [
                dpu("dpu-a", 3, 32),
                dpu("dpu-b", 2, 31),
            ],
            socs=(32, 31, 42),
        )
        cached = topology.resolve_battery_topology(
            incomplete,
            cfg(),
            previous=confirmed,
        )
        self.assertEqual(cached.source, "ecoflow_iot_cached")
        self.assertEqual(cached.pack_counts, (3, 2, 4))
        self.assertAlmostEqual(cached.capacity_kwh, 55.296, places=3)
        self.assertEqual(cached.bank_socs_pct, (32.0, 31.0, 42.0))

    def test_manual_configuration_remains_available_as_fallback(self):
        hass = SimpleNamespace(
            states=States({"s1": 31, "s2": 30, "s3": 41}),
            config_entries=ConfigEntries([]),
        )
        result = topology.resolve_battery_topology(hass, cfg())
        self.assertEqual(result.source, "configured")
        self.assertAlmostEqual(result.capacity_kwh, 49.152, places=3)

    def test_auto_detection_can_be_disabled(self):
        hass = hass_with(
            [
                dpu("dpu-a", 3, 31),
                dpu("dpu-b", 2, 30),
                dpu("dpu-c", 4, 41),
            ]
        )
        result = topology.resolve_battery_topology(hass, cfg(auto=False))
        self.assertEqual(result.source, "configured")
        self.assertAlmostEqual(result.capacity_kwh, 49.152, places=3)


if __name__ == "__main__":
    unittest.main()
