# Four-day planner acceptance results

Synthetic fixtures, not observed household savings. Tests use a 55.296 kWh bank, 99% initial SOC, 40% reserve, and a 6.19 kW EV charger.

## Four sunny days at 02:00

Status: ready.

| Date | Solar kWh | House kWh | Battery before sunrise kWh | EV kWh | Remaining export kWh |
|---|---:|---:|---:|---:|---:|
| 2026-10-07 | 75.96 | 22.00 | 4.44 | 1.68 | 56.53 |
| 2026-10-08 | 75.96 | 24.00 | 6.67 | 0.00 | 49.06 |
| 2026-10-09 | 75.96 | 24.00 | 6.67 | 0.00 | 49.06 |
| 2026-10-10 | 75.96 | 24.00 | 6.67 | 0.00 | 49.06 |

Export before the allocated EV load: 205.72 kWh. After: 203.71 kWh.

## Mixed solar/grid EV window

Status: ready.

| Date | Solar kWh | House kWh | Battery before sunrise kWh | EV kWh | Remaining export kWh |
|---|---:|---:|---:|---:|---:|
| 2026-10-07 | 22.79 | 22.00 | 4.44 | 6.34 | 0.00 |
| 2026-10-08 | 22.79 | 24.00 | 0.00 | 3.66 | 1.74 |
| 2026-10-09 | 22.79 | 24.00 | 0.00 | 0.00 | 5.93 |
| 2026-10-10 | 22.79 | 24.00 | 0.00 | 0.00 | 5.93 |

Export before the allocated EV load: 24.14 kWh. After: 13.61 kWh.

## Sustained low solar

Status: conserve.

| Date | Solar kWh | House kWh | Battery before sunrise kWh | EV kWh | Remaining export kWh |
|---|---:|---:|---:|---:|---:|
| 2026-10-07 | 0.76 | 44.00 | 0.00 | 0.00 | 0.00 |
| 2026-10-08 | 0.76 | 48.00 | 0.00 | 0.00 | 0.00 |
| 2026-10-09 | 0.76 | 48.00 | 0.00 | 0.00 | 0.00 |
| 2026-10-10 | 0.76 | 48.00 | 0.00 | 0.00 | 0.00 |

Export before the allocated EV load: 0.00 kWh. After: 0.00 kWh.

## Checks

Tests enforce AC energy balance, storage losses, reserve and capacity at every interval; continuous battery state across days; no reuse of EV capacity; planned windows before sunrise and while away; retention of mixed-supply opportunities; revision updates without confirmation waits; conservation ending when solar returns; partial provider coverage; and common dashboard/forecast outputs.
