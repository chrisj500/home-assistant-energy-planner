"""Select the production solar curve from horizon-matched model evidence."""
from datetime import datetime, timedelta, timezone

try:
    from .forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh
    from .solar_learning import lead_bucket, sky_bucket
    from .solar_learning_v4 import annotate_row as v4
    from .solar_challengers import persistence_prediction
    from .solar_profile import HORIZONS as PROFILE_HORIZONS, prediction as profile_prediction, target_geometry
    from .solar_profile import scorecard as profile_scorecard
except ImportError:
    from forecast_solar_shadow import IntervalPoint, integrate_interval_energy_kwh
    from solar_learning import lead_bucket, sky_bucket
    from solar_learning_v4 import annotate_row as v4
    from solar_challengers import persistence_prediction
    from solar_profile import HORIZONS as PROFILE_HORIZONS, prediction as profile_prediction, target_geometry
    from solar_profile import scorecard as profile_scorecard


def _v4_has_beaten_raw(memory, candidate):
    """Require a matched, prospective win in the candidate's lead bucket."""
    issued = candidate['issued_at']
    latest = {}
    for row in memory.get('scored', []):
        if (not isinstance(row, dict) or not row.get('accepted')
                or row.get('lead') != candidate['lead']
                or row.get('end', float('inf')) > issued
                or issued - row.get('end', 0) > 30 * 86400):
            continue
        actual, raw, corrected = row.get('actual_kwh'), row.get('raw_kwh'), row.get('v4_learned_kwh')
        if not all(isinstance(value, (int, float)) for value in (actual, raw, corrected)):
            continue
        if raw < 0.05 or min(actual, raw, corrected) < 0:
            continue
        prior = latest.get(row['start'])
        if prior is None or row.get('issued_at', 0) > prior.get('issued_at', 0):
            latest[row['start']] = row
    rows = list(latest.values())
    rows = [row for row in rows if row.get('v4_trained')]
    if len(rows) < 12 or len({row.get('day') for row in rows}) < 3:
        return False, {'reason': 'insufficient_validated_v4_history', 'samples': len(rows),
                       'days': len({row.get('day') for row in rows})}
    raw_mae = sum(abs(row['raw_kwh'] - row['actual_kwh']) for row in rows) / len(rows)
    v4_mae = sum(abs(row['v4_learned_kwh'] - row['actual_kwh']) for row in rows) / len(rows)
    raw_bias = sum(row['raw_kwh'] - row['actual_kwh'] for row in rows) / len(rows)
    v4_bias = sum(row['v4_learned_kwh'] - row['actual_kwh'] for row in rows) / len(rows)
    improved = v4_mae < raw_mae and v4_bias >= raw_bias
    return improved, {'reason': 'v4_beats_raw' if improved else 'v4_not_better_than_raw',
                              'samples': len(rows), 'days': len({row.get('day') for row in rows}),
                              'v4_mae_kwh': v4_mae, 'raw_mae_kwh': raw_mae,
                              'v4_bias_kwh': v4_bias, 'raw_bias_kwh': raw_bias}


def selected_curve(*, points, live_points, now, memory, observation, latitude, longitude, weather=()):
    """Route persistence, calibrated V4, and an independent profile by lead.

    Raw provider output remains the warm-up fallback for unsupported horizons.
    V3, V4.1, and the adaptive blend remain scorecard challengers only.
    """
    result, selections = [], []
    profile_metrics = profile_scorecard(memory)
    at = max(now, points[0].at)
    end = points[-1].at
    while at < end:
        stop = min(at + timedelta(hours=1), end)
        raw = integrate_interval_energy_kwh(points, at, stop)
        live = integrate_interval_energy_kwh(live_points, at, stop)
        lead = (at-now).total_seconds()/3600
        matching = [r for r in weather if abs((r['_at']-at).total_seconds()) <= 1800]
        w = matching[0] if matching else {}
        sky = w.get('sky')
        row = {'issued_at':now.timestamp(), 'start':at.timestamp(), 'end':stop.timestamp(),
               'day':at.date().isoformat(), 'hour':at.hour, 'lead':lead_bucket(lead),
               'lead_hours':lead,
               'raw_kwh':raw, 'live_kwh':live, 'forecast_sky':sky,
               'sky_bin':sky_bucket(sky), 'forecast_temperature_c':w.get('temperature'),
               'observed_at_issue':observation or {}}
        v4(memory, row)
        elevation, azimuth = target_geometry(row, latitude, longitude)
        row['target_sun_elevation_deg'] = elevation
        row['target_sun_azimuth_deg'] = azimuth
        row['day_of_year'] = datetime.fromtimestamp(at.timestamp(), timezone.utc).timetuple().tm_yday
        profile_value, profile_trained, profile_meta = profile_prediction(memory, row)
        profile_group = profile_metrics.get('by_horizon', {}).get(row['lead'], {})
        profile_validated = (profile_group.get('samples', 0) >= 12
                             and profile_group.get('days', 0) >= 3
                             and profile_group.get('beats_raw', False))

        value, model, weights = raw, 'provider', {'raw':1.0}
        if lead < 2:
            persistent, active, persistence_meta = persistence_prediction(
                row, points, latitude, longitude
            )
            if active:
                value, model, weights = persistent, 'persistence', {'persistence':1.0}
            else:
                value, model, weights = live, 'live', {'live':1.0}
        elif lead < 24 and row.get('v4_trained') and _v4_has_beaten_raw(memory, row)[0]:
            value = row['v4_learned_kwh']
            model, weights = 'v4', {'v4':1.0}
        elif 24 <= lead < 96 and row['lead'] in PROFILE_HORIZONS and profile_trained and profile_validated:
            value = profile_value
            model, weights = 'profile', {'profile':1.0}
        elif lead < 3:
            # Current-day live anchoring is preferable during V4's evidence
            # warm-up, and is not extrapolated beyond the near-term horizon.
            value, model, weights = live, 'live', {'live':1.0}

        selections.append({'start':at.isoformat(), 'end':stop.isoformat(), 'model':model,
                           'energy_kwh':value, 'weights':weights,
                           'lead_bucket':row['lead'],
                           'v4_trained':bool(row.get('v4_trained')),
                           'v4_training_samples':row.get('v4_training_samples', 0),
                           'v4_training_days':row.get('v4_training_days', 0),
                           'v4_reason':row.get('v4_training_reason'),
                           'v4_validation':(_v4_has_beaten_raw(memory, row)[1]
                                            if 2 <= lead < 24 and row.get('v4_trained') else None),
                           'profile_trained':profile_trained,
                           'profile_metadata':profile_meta,
                           'profile_validation':profile_group if row['lead'] in PROFILE_HORIZONS else None})
        # Preserve each member's hourly energy exactly, including a partial hour.
        power = max(value,0)*1000/((stop-at).total_seconds()/3600)
        result.extend([IntervalPoint(at,power), IntervalPoint(stop-timedelta(microseconds=1),power)])
        at = stop
    result.append(IntervalPoint(end,0))
    return result, selections
