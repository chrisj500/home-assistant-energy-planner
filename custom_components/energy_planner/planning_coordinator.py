"""Final publication layer: one forecast and ledger for operational planning."""
from datetime import timedelta
import logging
from homeassistant.util import dt as dt_util
from .solar_learning_coordinator import EnergyPlannerSolarLearningCoordinator
from .coordinator import _solar_window, _controller_settings, _num
from .const import (CONF_CHARGE_LIMIT, OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY,
    CONF_EV_HOME, CONF_ACTUAL_SOLAR_POWER, CONF_BASE_LOAD_POWER, OPT_EV_SOLAR_ADVISORY_ENABLED)
from .forecast_solar_shadow import interval_points_from_payload, weather_rows
from .headroom import correct_current_day_points
from .rolling_ev import DaylightWindow
from .planning import build_plan, publish_plan
from .planning_solar import selected_curve

_LOGGER = logging.getLogger(__name__)


class EnergyPlannerPlanningCoordinator(EnergyPlannerSolarLearningCoordinator):
    async def _async_update_data(self):
        data = await super()._async_update_data()
        now = dt_util.now()
        try:
            if self._estimate_last_success is None or not 0 <= (now-self._estimate_last_success).total_seconds() <= 7200:
                raise ValueError('Solar provider data is missing or older than two hours')
            points = interval_points_from_payload(self._estimate_payload, now, assume_utc=True)
            windows = [DaylightWindow(day, *_solar_window(self.hass, day))
                       for day in [now.date()+timedelta(days=i) for i in range(4)]]
            covered = []
            for window in windows:
                start = max(now, window.sunrise)
                valid = start >= window.sunset or (points and points[0].at <= start and points[-1].at >= window.sunset
                    and not any((b.at-a.at).total_seconds()>7200 and a.at<window.sunset and b.at>start
                                for a,b in zip(points,points[1:])))
                if not valid:
                    break
                covered.append(window)
            missing_days = [w.day.isoformat() for w in windows[len(covered):]]
            if not covered or not points:
                raise ValueError('Solar intervals do not cover the next daylight period')
            windows = covered
            diagnostics = data.get('solar_learning_diagnostics', {})
            obs = diagnostics.get('current_observation', {})
            live = correct_current_day_points(points=points, reference=now,
                sunrise=windows[0].sunrise, sunset=windows[0].sunset,
                corrected_remaining_kwh=None,
                actual_solar_w=obs.get('power_w') if obs.get('valid') else None).points
            weather_at = self._professional_attempts.get('weather')
            weather = weather_rows(self._professional_cache.get('weather'), now) if (
                weather_at and 0 <= (now-weather_at).total_seconds() <=7200
                and not self._professional_cache.get('weather_error')) else []
            curve, members = selected_curve(points=points, live_points=live, now=now,
                memory=self._solar_memory or {}, observation=obs,
                latitude=self.hass.config.latitude, longitude=self.hass.config.longitude, weather=weather)
            load_w = data.get('rolling_planning_base_load_w')
            profile = data.get('rolling_planning_load_profile_w') or []
            if load_w is None:
                raise ValueError('Household planning load is unavailable')
            loads = {window.day.isoformat(): (profile[i] if i<len(profile) else load_w)/1000
                     for i,window in enumerate(windows)}
            settings = _controller_settings(self.hass, 0)
            efficiency = float(self.cfg.get(OPT_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY))
            if efficiency > 1: efficiency /= 100
            fresh_ev = data.get('rolling_ev_soc_data_status') == 'fresh'
            limit = _num(self.hass,self.cfg.get(CONF_CHARGE_LIMIT))
            limit = 100 if limit is None else limit
            plan = build_plan(now=now, windows=windows, points=curve,
                capacities=data.get('battery_bank_capacities_kwh'), socs=data.get('battery_bank_socs_pct'),
                load_by_day=loads, reserve_pct=data['effective_reserve_floor'],
                ceiling_pct=limit,
                charge_kw=settings.maximum_rate_w/1000 if settings.enabled else 0,
                # Use the verified charge ceiling as a conservative planning
                # discharge ceiling; do not assert an unobserved discharge rating.
                discharge_kw=settings.maximum_rate_w/1000,
                efficiency=efficiency,
                ev_capacity_kwh=(data.get('rolling_ev_available_energy_kwh') or 0) if fresh_ev else 0,
                ev_power_kw=(data.get('rolling_ev_charge_power_w') or 0)/1000,
                ev_return_capacity_kwh=data.get('rolling_ev_wall_full_kwh') or 0,
                storm=data.get('storm') is not False)
            plan['missing_days'] = missing_days
            if data.get('storm') is None:
                plan['status'] = 'storm_sensor_unavailable'
                plan['reason'] = 'Storm sensor is unavailable; battery reserve protection is retained.'
            plan['solar_members'] = members
            plan['generated_at'] = now.isoformat()
            plan['discharge_limit_source'] = 'configured_charge_ceiling_used_as_planning_discharge_ceiling'
            plan['charge_limit_source'] = getattr(settings, 'source', 'configured')
            publish_plan(data, plan)
            self._verify_plan_live(data, now)
            for model in ('blend','persistence'):
                used = any(r['model']==model for r in members)
                diagnostics.get(model, {})['forecast_applied'] = used
                if used:
                    data['solar_learning_'+model+'_status'] = 'planning'
                    diagnostics.get(model,{})['status'] = 'planning'
                    policy = diagnostics.get(model,{}).get('policy','')
                    diagnostics.get(model,{})['policy'] = policy.replace('shadow only','used in planning; prospective scoring continues')
            for member, key, sensor in (('v4','v4','solar_learning_v4_status'), ('v41','v4_1','solar_learning_v41_status')):
                used = any(r['model']=='blend' and r['weights'].get(member,0)>0 for r in members)
                diagnostics.get(key,{})['forecast_applied'] = used
                if used:
                    data[sensor] = 'planning'
                    diagnostics.get(key,{})['status'] = 'planning'
            diagnostics['planning_model'] = plan['model']
        except Exception as err:
            self._plan_surplus_since = None
            self._plan_last_live = now
            _LOGGER.exception('Four-day planning inputs unavailable')
            data.update(four_day_plan_status='unavailable', four_day_plan={
                'status':'unavailable','reason':str(err),'days':[]},
                forecast_reliability_status='unavailable', forecast_reliability_reason=str(err),
                rolling_ev_auto_charge_eligible=False, rolling_ev_recommended_energy_kwh=0,
                headroom_release=False, battery_outlook_status='unavailable', battery_outlook_reason=str(err))
        return data

    def _verify_plan_live(self, data, now):
        home = self.hass.states.get(self.cfg.get(CONF_EV_HOME, ''))
        solar = self._fresh_power(self.cfg.get(CONF_ACTUAL_SOLAR_POWER), now)
        load = self._fresh_power(self.cfg.get(CONF_BASE_LOAD_POWER), now)
        power = data.get('rolling_ev_charge_power_w') or 0
        start = dt_util.parse_datetime(data.get('rolling_ev_window_start') or '')
        end = dt_util.parse_datetime(data.get('rolling_ev_window_end') or '')
        last = getattr(self, '_plan_last_live', None)
        eligible = (data.get('storm') is False and self.cfg.get(OPT_EV_SOLAR_ADVISORY_ENABLED, False)
                    and home is not None and home.state == 'home' and power > 0
                    and solar is not None and load is not None and solar-load >= power+500
                    and start is not None and end is not None and start <= now < end)
        if not eligible or (last is not None and (now-last).total_seconds()>120):
            self._plan_surplus_since = None
        if eligible:
            self._plan_surplus_since = getattr(self, '_plan_surplus_since', None) or now
        self._plan_last_live = now
        since = getattr(self, '_plan_surplus_since', None)
        if since is not None and (now-since).total_seconds()>=600:
            data.update(rolling_ev_auto_charge_eligible=True, rolling_ev_status='green',
                        rolling_ev_auto_charge_reason='Planned window and sustained live surplus verified.')
