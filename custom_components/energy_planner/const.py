DOMAIN = "energy_planner"
PLATFORMS = ["sensor"]

CONF_SOC_1 = "soc_entity_1"
CONF_SOC_2 = "soc_entity_2"
CONF_SOC_3 = "soc_entity_3"
CONF_SOC_WEIGHTS = "soc_weights"
CONF_CAPACITY_KWH = "capacity_kwh"
CONF_CHARGE_LIMIT = "charge_limit_entity"
CONF_BACKUP_RESERVE = "backup_reserve_entity"
CONF_STORM_WARNING = "storm_warning_entity"
CONF_SOLAR_TODAY = "solar_today_entity"
CONF_SOLAR_TOMORROW = "solar_tomorrow_entity"
CONF_EV_SOC = "ev_soc_entity"
CONF_EV_HOME = "ev_home_entity"

OPT_AUTO_HEADROOM = "auto_headroom"
OPT_MIN_RESERVE = "minimum_reserve"
OPT_STRONG_SOLAR_KWH = "strong_solar_kwh"
OPT_EV_TARGET_SOC = "ev_target_soc"

DEFAULT_WEIGHTS = "3,2,3"
DEFAULT_CAPACITY_KWH = 49.152
DEFAULT_MIN_RESERVE = 10.0
DEFAULT_STRONG_SOLAR_KWH = 35.0
DEFAULT_EV_TARGET_SOC = 100.0
