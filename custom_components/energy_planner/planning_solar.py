"""Evaluate current horizon members without changing frozen comparison records."""
from datetime import timedelta
try:
    from .forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh
    from .solar_learning import lead_bucket, sky_bucket
    from .solar_learning_v4 import annotate_row as v4
    from .solar_learning_v41 import annotate_row as v41, ensure_target_geometry
    from .solar_challengers import blend_prediction, persistence_prediction
except ImportError:
    from forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh
    from solar_learning import lead_bucket, sky_bucket
    from solar_learning_v4 import annotate_row as v4
    from solar_learning_v41 import annotate_row as v41, ensure_target_geometry
    from solar_challengers import blend_prediction, persistence_prediction


def selected_curve(*, points, live_points, now, memory, observation, latitude, longitude, weather=()):
    result, selections = [], []
    at = max(now, points[0].at)
    end = points[-1].at
    while at < end:
        stop = min(at + timedelta(hours=1), end)
        raw = integrate_interval_energy_kwh(points, at, stop)
        live = integrate_interval_energy_kwh(live_points, at, stop)
        lead = (at-now).total_seconds()/3600
        value, model, weights = raw, 'provider', {'raw':1.0}
        if lead < 24:
            matching = [r for r in weather if abs((r['_at']-at).total_seconds()) <= 1800]
            w = matching[0] if matching else {}
            sky = w.get('sky')
            row = {'issued_at':now.timestamp(), 'start':at.timestamp(), 'end':stop.timestamp(),
                   'day':at.date().isoformat(), 'hour':at.hour, 'lead':lead_bucket(lead),
                   'raw_kwh':raw, 'live_kwh':live, 'forecast_sky':sky,
                   'sky_bin':sky_bucket(sky), 'forecast_temperature_c':w.get('temperature'),
                   'observed_at_issue':observation or {}}
            v4(memory, row)
            ensure_target_geometry([row], latitude, longitude)
            v41(memory, row)
            value, active, meta = blend_prediction(memory, row)
            weights = meta['weights']
            model = 'blend' if active else ('live' if lead < 3 else 'provider')
            if lead < 2:
                persistent, active, _ = persistence_prediction(row, points, latitude, longitude)
                if active:
                    value, model, weights = persistent, 'persistence', {'persistence':1.0}
                else:
                    value, model, weights = live, 'live', {'live':1.0}
        selections.append({'start':at.isoformat(), 'end':stop.isoformat(), 'model':model,
                           'energy_kwh':value, 'weights':weights})
        # Preserve each member's hourly energy exactly, including a partial hour.
        power = max(value,0)*1000/((stop-at).total_seconds()/3600)
        result.extend([IntervalPoint(at,power), IntervalPoint(stop-timedelta(microseconds=1),power)])
        at = stop
    result.append(IntervalPoint(end,0))
    return result, selections
