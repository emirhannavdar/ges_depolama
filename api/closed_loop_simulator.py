"""Deterministic scenario replay against the existing physical battery simulator.

Not a realtime device or a MILP re-optimizer. Changes virtual PV/load inputs per
simulated minute and safely adapts a prior hourly charge/discharge instruction.
EPİAŞ hourly PTF comes unchanged from the supplied optimization plan.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from api.simulator import SimulatorEngine, VirtualBattery
except ImportError as exc:
    raise RuntimeError('api/simulator.py (mevcut simülatör) bulunamadı.') from exc


def finite_nonnegative(value: Any, name: str) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} sayısal olmalı') from exc
    if not math.isfinite(n) or n < 0:
        raise ValueError(f'{name} negatif veya sonsuz olamaz')
    return n


def parse_events(raw: dict[str, Any], start: datetime, end: datetime) -> list[dict[str, Any]]:
    events = []
    for index, event in enumerate(raw.get('events', [])):
        label = event.get('name') or f'event-{index + 1}'
        begin = datetime.fromisoformat(event['start'])
        stop = datetime.fromisoformat(event['end'])
        if begin.tzinfo is None or stop.tzinfo is None:
            raise ValueError(f'{label}: timezone belirtilmeli')
        if not begin < stop:
            raise ValueError(f'{label}: bitiş başlangıçtan sonra olmalı')
        if begin < start or stop > end:
            raise ValueError(f'{label}: olay plan sınırları içinde olmalı')
        pv_factor = finite_nonnegative(event.get('pv_factor', 1.0), f'{label}.pv_factor')
        load_factor = finite_nonnegative(event.get('load_factor', 1.0), f'{label}.load_factor')
        if pv_factor > 1.5 or load_factor > 5:
            raise ValueError(f'{label}: değişim çarpanı kabul edilen sınırı aşıyor')
        events.append({'name': label, 'start': begin, 'end': stop,
                       'pv_factor': pv_factor, 'load_factor': load_factor})
    return events


def verify_battery_matches_plan(optimization: dict[str, Any], battery: VirtualBattery) -> dict[str, float]:
    """Fail fast if battery.json and hourly MILP SOC trajectory are incompatible.

    Uses physical SOC deltas and hourly AC charge/discharge energies to infer
    *effective* storage capacity; does not assume an undocumented optimizer key.
    """
    cap = battery.effective_capacity_kwh
    if not math.isfinite(cap) or cap <= 0:
        raise ValueError('Batarya etkin kapasitesi pozitif olmalı')
    estimates: list[float] = []
    for h in optimization['schedule']:
        delta_soc = (float(h.get('new_soc', 0)) - float(h.get('previous_soc', 0))) / 100
        c = float(h.get('charge_energy_kwh', 0))
        d = float(h.get('discharge_energy_kwh', 0))
        delta_internal = c * battery.charge_efficiency - d / battery.discharge_efficiency
        if abs(delta_soc) > 1e-5 and abs(delta_internal) > 1e-4:
            estimate = delta_internal / delta_soc
            if estimate > 0 and math.isfinite(estimate):
                estimates.append(estimate)
    if estimates:
        estimates.sort()
        estimate = estimates[len(estimates) // 2]
        error_ratio = abs(estimate - cap) / estimate
        if error_ratio > 0.02:
            raise ValueError(
                'OPTİMİZASYON VE BATARYA DOSYASI UYUŞMUYOR: '
                f'planın SOC hareketinden hesaplanan etkin kapasite yaklaşık {estimate:,.2f} kWh, '
                f'battery.json etkin kapasitesi {cap:,.2f} kWh. '
                'battery.json dosyanı aynı /optimize isteğindeki batarya bilgileriyle güncelle. '
                '100 MWh için capacity_kwh=100000 (100 değil).'
            )
    return {'effective_capacity_kwh': cap,
            'inferred_plan_effective_capacity_kwh': estimates[len(estimates)//2] if estimates else None}


class AdaptiveScenarioEngine(SimulatorEngine):
    """Plant responds to selected virtual disturbances; optimizer dispatch remains
    the hourly target, with physical limits, no grid-battery charging or false PTF.
    """

    def __init__(self, optimization: dict[str, Any], battery: VirtualBattery,
                 scenario: dict[str, Any] | None = None):
        self.input_check = verify_battery_matches_plan(optimization, battery)
        super().__init__(optimization, battery)
        self.events = parse_events(scenario or {}, self.now, self.end)
        self.economics = {'with_battery_tl': 0.0, 'without_battery_tl': 0.0,
                          'sales_tl': 0.0, 'purchases_tl': 0.0,
                          'degradation_tl': 0.0}
        self.degradation_tl_per_kwh = finite_nonnegative(
            optimization.get('degradation_cost_tl_per_kwh', 0.5), 'degradation_cost_tl_per_kwh')
        self.last_actions = []

    def _one_slice(self, hour: dict[str, Any], seconds: float) -> dict[str, Any]:
        # If event crosses a step, engine.step() splits at event boundaries.
        now = self.now
        active = [e for e in self.events if e['start'] <= now < e['end']]
        pv_factor = 1.0
        load_factor = 1.0
        for event in active:
            pv_factor *= event['pv_factor']
            load_factor *= event['load_factor']
        actual_hour = dict(hour)
        actual_hour['generation_kwh'] = finite_nonnegative(hour['generation_kwh'], 'generation_kwh') * pv_factor
        actual_hour['site_load_kwh'] = finite_nonnegative(hour.get('site_load_kwh', 0), 'site_load_kwh') * load_factor
        # Old curtailment is invalid after a virtual weather/load change: physical
        # simulator recomputes overflow from export capacity.
        actual_hour['curtailment_kwh'] = 0.0
        # Preserve hourly economic intent but adapt required power if virtual PV
        # or site load changes. SimulatorEngine applies hard SOC/BMS/PCS constraints.
        # NOTE: no fresh MILP is solved in this version.
        record = super()._one_slice(actual_hour, seconds)
        dt_h = seconds / 3600
        price = float(hour['price_tl_mwh']) / 1000
        cost = float(hour.get('import_price_tl_mwh', hour['price_tl_mwh'])) / 1000
        pv = record['pv_kw']
        load = record['load_kw']
        # Baseline without BESS, with exactly same synthetic weather and load.
        pv_surplus = max(0.0, pv - load)
        baseline_export = min(pv_surplus, self.export_limit_kw if self.export_limit_kw is not None else pv_surplus)
        baseline_import = max(0.0, load - pv)
        if self.import_limit_kw is not None:
            baseline_import = min(baseline_import, self.import_limit_kw)
        baseline_net = (baseline_export * price - baseline_import * cost) * dt_h
        sales = record['grid_export_kw'] * dt_h * price
        purchase = record['grid_import_kw'] * dt_h * cost
        degradation = record['discharge_kw'] * dt_h * self.degradation_tl_per_kwh
        self.economics['without_battery_tl'] += baseline_net
        self.economics['sales_tl'] += sales
        self.economics['purchases_tl'] += purchase
        self.economics['degradation_tl'] += degradation
        self.economics['with_battery_tl'] += sales - purchase - degradation
        action = 'CHARGE' if record['charge_kw'] > 1e-8 else 'DISCHARGE' if record['discharge_kw'] > 1e-8 else 'IDLE'
        record.update({
            'source': 'SIMULATED_SCENARIO_NOT_FIELD_TELEMETRY',
            'plan_generation_kw': float(hour['generation_kwh']),
            'plan_load_kw': float(hour.get('site_load_kwh', 0)),
            'plan_charge_kw': float(hour.get('charge_energy_kwh', 0)),
            'plan_discharge_kw': float(hour.get('discharge_energy_kwh', 0)),
            'active_events': [e['name'] for e in active],
            'control_action': action,
            'grid_export_revenue_tl': round(sales, 5),
            'grid_import_cost_tl': round(purchase, 5),
            'battery_degradation_tl': round(degradation, 5),
            'incremental_battery_tl_cumulative': round(
                self.economics['with_battery_tl'] - self.economics['without_battery_tl'], 5),
        })
        self.last_actions.append(action)
        self.last = record
        return record

    def step(self, simulated_seconds: float = 60.0) -> list[dict[str, Any]]:
        if not math.isfinite(simulated_seconds) or simulated_seconds <= 0:
            raise ValueError('simulated_seconds pozitif olmalı')
        records = []
        remaining = simulated_seconds
        while remaining > 1e-8 and self.now < self.end:
            index = int((self.now - self.dates[0]).total_seconds() // 3600)
            boundary = self.dates[index] + timedelta(hours=1)
            for event in self.events:
                if self.now < event['start'] < boundary:
                    boundary = event['start']
                if self.now < event['end'] < boundary:
                    boundary = event['end']
            seconds = min(remaining, (boundary - self.now).total_seconds())
            if seconds <= 1e-9:
                raise RuntimeError('Zaman adımı ilerlemiyor')
            records.append(self._one_slice(self.hours[index], seconds))
            self.now += timedelta(seconds=seconds)
            remaining -= seconds
        return records

    def summary(self, claimed_kwp: float | None = None) -> dict[str, Any]:
        econ = self.economics
        net = econ['with_battery_tl']
        baseline = econ['without_battery_tl']
        daily_pv = sum(float(h['generation_kwh']) for h in self.hours)
        summary = {
            'data_source': 'Plan: supplied /optimize JSON, PTF unchanged; PV/load deviations: explicit synthetic test events',
            'plan_hours': len(self.hours), 'plan_pv_generation_kwh': round(daily_pv, 3),
            'battery_input_validation': self.input_check,
            'simulated_totals_kwh': {k: round(v, 3) for k, v in self.totals.items()},
            'baseline_without_battery_tl': round(baseline, 2),
            'simulated_with_battery_tl': round(net, 2),
            'simulated_incremental_battery_tl': round(net-baseline, 2),
            'simulated_sales_tl': round(econ['sales_tl'], 2),
            'simulated_purchases_tl': round(econ['purchases_tl'], 2),
            'simulated_battery_degradation_tl': round(econ['degradation_tl'], 2),
            'final_soc_pct': round(self.soc, 5),
            'final_soc_target_pct': self.hours[-1].get('new_soc'),
            'events_applied': [e['name'] for e in self.events],
            'warning': 'No intraday MILP re-optimization; economic results use PTF as reference, not contract settlement.'
        }
        if claimed_kwp:
            summary['claimed_installed_kwp'] = claimed_kwp
            summary['plan_specific_yield_kwh_per_kwp'] = round(daily_pv / claimed_kwp, 5)
            summary['capacity_input_verified'] = False
            summary['capacity_warning'] = ('The original forecast function may still read config.py. '
                                           'A yield check cannot prove which installed capacity was used. '
                                           'Log the effective capacity inside forecast.py.')
        return summary


def cli() -> None:
    parser = argparse.ArgumentParser(description='Kapalı çevrim sanal saha sapma testi')
    parser.add_argument('--plan', required=True)
    parser.add_argument('--battery', help='Battery JSON; omit if plan input contains battery')
    parser.add_argument('--scenario', required=True, help='JSON with events for synthetic weather/load shocks')
    parser.add_argument('--out', default='closed_loop_results.jsonl')
    parser.add_argument('--summary', default='closed_loop_summary.json')
    parser.add_argument('--step', type=float, default=60)
    parser.add_argument('--installed-kwp', type=float, default=None)
    args = parser.parse_args()
    payload = json.loads(Path(args.plan).read_text(encoding='utf-8-sig'))
    opt = payload.get('optimization', payload)
    if args.battery:
        battery_data = json.loads(Path(args.battery).read_text(encoding='utf-8-sig'))
        battery_data = battery_data.get('battery', battery_data)
    else:
        battery_data = payload.get('battery')
    if not battery_data:
        parser.error('Battery JSON not found; use --battery battery.json')
    engine = AdaptiveScenarioEngine(opt, VirtualBattery(**battery_data),
                                    json.loads(Path(args.scenario).read_text(encoding='utf-8-sig')))
    if args.installed_kwp is not None and args.installed_kwp <= 0:
        parser.error('installed-kwp must be positive')
    output = Path(args.out)
    with output.open('w', encoding='utf-8') as stream:
        while engine.now < engine.end:
            for row in engine.step(args.step):
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    summary = engine.summary(args.installed_kwp)
    Path(args.summary).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f'Ölçümler: {output}; özet: {args.summary}')


if __name__ == '__main__':
    cli()
