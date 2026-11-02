"""Virtual GES+BESS simulator driven by a real-PTF optimization schedule.

No market prices, solar observations, or meter readings are synthesized.
The input schedule is a *forecast/plan*. Output values are simulated measurements.
Units: kW for power, kWh for energy, seconds for elapsed simulation time.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class VirtualBattery:
    capacity_kwh: float
    soc: float
    soh: float
    min_soc: float
    max_soc: float
    max_charge_kw: float
    max_discharge_kw: float
    charge_efficiency: float
    discharge_efficiency: float
    max_charge_c: float | None = None
    max_discharge_c: float | None = None

    @property
    def effective_capacity_kwh(self) -> float:
        return self.capacity_kwh * self.soh / 100


@dataclass
class VirtualDeviceLimits:
    bms_charge_allowed: bool = True
    bms_discharge_allowed: bool = True
    bms_max_charge_kw: float | None = None
    bms_max_discharge_kw: float | None = None
    pcs_online: bool = True
    pcs_max_charge_kw: float | None = None
    pcs_max_discharge_kw: float | None = None


class SimulatorEngine:
    """Deterministic, time-stepped digital twin of one battery and point of connection.

    This v1 uses hourly forecast values as *constant hourly average power*.
    It does not pretend to have real-time field metering or BMS telemetry.
    Initial stored energy's provenance is unknown; it can serve site load but
    cannot be exported. Newly solar-charged energy may be exported.
    """

    def __init__(self, optimization: dict[str, Any], battery: VirtualBattery):
        self.battery = battery
        self.hours = sorted(optimization['schedule'], key=lambda x: x['datetime'])
        if not self.hours:
            raise ValueError('Optimizasyon planı boş')
        self.dates = [datetime.fromisoformat(h['datetime']) for h in self.hours]
        if any(d.tzinfo is None for d in self.dates):
            raise ValueError('Saatlerin timezone bilgisi olmalı')
        if any(self.dates[i+1] - self.dates[i] != timedelta(hours=1)
               for i in range(len(self.dates)-1)):
            raise ValueError('Plan kesintisiz, saatlik (1h) aralıklarla olmalı')
        if abs(self.battery.soc - optimization['initial_soc']) > 1e-6:
            raise ValueError('Batarya başlangıç SOC ile plan SOC uyuşmuyor')
        if battery.effective_capacity_kwh <= 0:
            raise ValueError('Batarya kapasitesi geçersiz')
        if not (0 <= battery.min_soc <= battery.soc <= battery.max_soc <= 100):
            raise ValueError('SOC sınırları geçersiz')
        if not (0 < battery.charge_efficiency <= 1 and 0 < battery.discharge_efficiency <= 1):
            raise ValueError('Batarya verim değerleri geçersiz')
        self.export_limit_kw = optimization.get('grid_export_limit_kw')
        self.import_limit_kw = optimization.get('grid_import_limit_kw')
        self.device_limits = VirtualDeviceLimits(**optimization.get('device_limits', {}))
        self.now = self.dates[0]
        self.end = self.dates[-1] + timedelta(hours=1)
        self.stored_kwh = battery.effective_capacity_kwh * battery.soc / 100
        self.pv_origin_kwh = 0.0  # Initial energy provenance is unknown
        self.totals = {k: 0.0 for k in (
            'pv_generation_kwh', 'site_load_kwh', 'battery_charge_kwh',
            'battery_discharge_kwh', 'grid_import_kwh', 'grid_export_kwh',
            'curtailment_kwh', 'unserved_load_kwh')}
        self.last: dict[str, Any] | None = None

    @property
    def soc(self) -> float:
        return 100 * self.stored_kwh / self.battery.effective_capacity_kwh

    def _limits(self) -> tuple[float, float]:
        b, l = self.battery, self.device_limits
        c, d = b.max_charge_kw, b.max_discharge_kw
        if b.max_charge_c is not None:
            c = min(c, b.effective_capacity_kwh * b.max_charge_c)
        if b.max_discharge_c is not None:
            d = min(d, b.effective_capacity_kwh * b.max_discharge_c)
        if not l.pcs_online:
            return 0.0, 0.0
        if not l.bms_charge_allowed:
            c = 0.0
        if not l.bms_discharge_allowed:
            d = 0.0
        if l.bms_max_charge_kw is not None:
            c = min(c, l.bms_max_charge_kw)
        if l.bms_max_discharge_kw is not None:
            d = min(d, l.bms_max_discharge_kw)
        if l.pcs_max_charge_kw is not None:
            c = min(c, l.pcs_max_charge_kw)
        if l.pcs_max_discharge_kw is not None:
            d = min(d, l.pcs_max_discharge_kw)
        return max(0, c), max(0, d)

    def update_device_limits(self, **changes: Any) -> None:
        for name, value in changes.items():
            if not hasattr(self.device_limits, name):
                raise ValueError(f'Bilinmeyen cihaz limiti: {name}')
            if name.startswith(('bms_max_', 'pcs_max_')) and value is not None:
                if not math.isfinite(value) or value < 0:
                    raise ValueError('Negatif/geçersiz cihaz güç limiti')
            setattr(self.device_limits, name, value)

    def step(self, simulated_seconds: float = 1.0) -> list[dict[str, Any]]:
        """Advance simulated time. Returns one measurement per hour-boundary slice."""
        if not math.isfinite(simulated_seconds) or simulated_seconds <= 0:
            raise ValueError('simulated_seconds pozitif ve sonlu olmalı')
        records: list[dict[str, Any]] = []
        remaining = simulated_seconds
        while remaining > 1e-8 and self.now < self.end:
            hour_index = int((self.now - self.dates[0]).total_seconds() // 3600)
            hour_end = self.dates[hour_index] + timedelta(hours=1)
            seconds = min(remaining, (hour_end - self.now).total_seconds())
            records.append(self._one_slice(self.hours[hour_index], seconds))
            self.now += timedelta(seconds=seconds)
            remaining -= seconds
        return records

    def _one_slice(self, hour: dict[str, Any], seconds: float) -> dict[str, Any]:
        dt_h = seconds / 3600
        pv_kw = max(0.0, float(hour['generation_kwh']))  # hourly kWh / 1h
        load_kw = max(0.0, float(hour.get('site_load_kwh', 0)))
        charge_request_kw = max(0.0, float(hour.get('charge_energy_kwh', 0)))
        discharge_request_kw = max(0.0, float(hour.get('discharge_energy_kwh', 0)))
        curtail_request_kw = max(0.0, float(hour.get('curtailment_kwh', 0)))
        max_charge_kw, max_discharge_kw = self._limits()
        b = self.battery
        cap = b.effective_capacity_kwh
        # Surplus PV may charge the battery. Grid energy may not charge it.
        pv_to_load_kw = min(pv_kw, load_kw)
        solar_surplus_kw = pv_kw - pv_to_load_kw
        room_kwh = max(0, cap * b.max_soc / 100 - self.stored_kwh)
        charge_kw = min(charge_request_kw, max_charge_kw, solar_surplus_kw,
                        room_kwh / (b.charge_efficiency * dt_h))
        charge_kw = max(0, charge_kw)
        solar_surplus_kw -= charge_kw
        delta_pv_stored = charge_kw * dt_h * b.charge_efficiency
        self.stored_kwh += delta_pv_stored
        self.pv_origin_kwh += delta_pv_stored

        # Battery energy can serve the site load from any initial provenance.
        # Export only power backed by tracked solar-charged battery energy.
        min_stored = cap * b.min_soc / 100
        available_discharge_kw = max(
            0, (self.stored_kwh - min_stored) * b.discharge_efficiency / dt_h)
        requested_kw = min(discharge_request_kw, max_discharge_kw,
                           available_discharge_kw)
        unmet_load_kw = max(0, load_kw - pv_to_load_kw)
        battery_to_load_kw = min(requested_kw, unmet_load_kw)

        # Use unknown-origin energy for self-consumption first.
        unknown_kwh = max(0, self.stored_kwh - self.pv_origin_kwh)
        needed_internal_load = battery_to_load_kw * dt_h / b.discharge_efficiency
        unknown_used = min(unknown_kwh, needed_internal_load)
        pv_used_load = needed_internal_load - unknown_used
        self.pv_origin_kwh = max(0.0, self.pv_origin_kwh - pv_used_load)
        self.stored_kwh -= needed_internal_load
        unmet_load_kw -= battery_to_load_kw

        export_room_kw = (float('inf') if self.export_limit_kw is None
                          else max(0.0, self.export_limit_kw - solar_surplus_kw))
        battery_to_export_kw = min(
            max(0.0, requested_kw - battery_to_load_kw),
            export_room_kw,
            self.pv_origin_kwh * b.discharge_efficiency / dt_h)
        battery_to_export_kw = max(0, battery_to_export_kw)
        used_pv_internal = battery_to_export_kw * dt_h / b.discharge_efficiency
        self.pv_origin_kwh = max(0.0, self.pv_origin_kwh - used_pv_internal)
        self.stored_kwh -= used_pv_internal

        # Follow planned curtailment, and curtail unavoidable export overflow.
        planned_curtail_kw = min(curtail_request_kw, solar_surplus_kw)
        solar_surplus_kw -= planned_curtail_kw
        export_kw = solar_surplus_kw + battery_to_export_kw
        extra_curtail_kw = max(0.0, export_kw - (self.export_limit_kw or 0.0)) if self.export_limit_kw is not None else 0.0
        # Do not curtail battery output: it is already limited; only surplus PV.
        extra_curtail_kw = min(extra_curtail_kw, solar_surplus_kw)
        export_kw -= extra_curtail_kw
        curtailed_kw = planned_curtail_kw + extra_curtail_kw

        import_kw = unmet_load_kw
        unserved_kw = 0.0
        if self.import_limit_kw is not None and import_kw > self.import_limit_kw:
            unserved_kw = import_kw - self.import_limit_kw
            import_kw = self.import_limit_kw

        discharge_kw = battery_to_load_kw + battery_to_export_kw
        if charge_kw > 1e-7 and discharge_kw > 1e-7:
            raise RuntimeError('Eş zamanlı şarj ve deşarj')
        if export_kw > 1e-7 and import_kw > 1e-7:
            raise RuntimeError('Eş zamanlı şebekeye alış ve satış')
        residual_kw = pv_kw + discharge_kw + import_kw + unserved_kw - (
            load_kw + charge_kw + export_kw + curtailed_kw)
        if abs(residual_kw) > 1e-6:
            raise RuntimeError(f'Fiziksel enerji dengesi hatası: {residual_kw}')
        self.stored_kwh = max(0.0, self.stored_kwh)
        self.pv_origin_kwh = min(self.stored_kwh, max(0.0, self.pv_origin_kwh))
        metrics = {
            'pv_generation_kwh': pv_kw * dt_h,
            'site_load_kwh': load_kw * dt_h,
            'battery_charge_kwh': charge_kw * dt_h,
            'battery_discharge_kwh': discharge_kw * dt_h,
            'grid_import_kwh': import_kw * dt_h,
            'grid_export_kwh': export_kw * dt_h,
            'curtailment_kwh': curtailed_kw * dt_h,
            'unserved_load_kwh': unserved_kw * dt_h,
        }
        for k, v in metrics.items():
            self.totals[k] += v
        self.last = {
            'simulated_at': (self.now + timedelta(seconds=seconds)).isoformat(),
            'slice_seconds': seconds,
            'source': 'SIMULATED_FROM_FORECAST_AND_OPTIMIZATION_PLAN',
            'ptf_tl_mwh': hour['price_tl_mwh'],
            'pv_kw': round(pv_kw, 6),
            'load_kw': round(load_kw, 6),
            'charge_kw': round(charge_kw, 6),
            'discharge_kw': round(discharge_kw, 6),
            'grid_export_kw': round(export_kw, 6),
            'grid_import_kw': round(import_kw, 6),
            'curtailment_kw': round(curtailed_kw, 6),
            'unserved_load_kw': round(unserved_kw, 6),
            'soc_pct': round(self.soc, 6),
            'solar_origin_stored_kwh': round(self.pv_origin_kwh, 6),
            'device_limits': asdict(self.device_limits),
        }
        return self.last


def main() -> None:
    parser = argparse.ArgumentParser(description='GES/BESS bağımsız simülatörü')
    parser.add_argument('--plan', required=True, help='POST /optimize yanıtının JSON yolu')
    parser.add_argument('--battery', required=True, help='Batarya yapılandırması JSON yolu')
    parser.add_argument('--speed', type=float, default=3600, help='1 gerçek saniyede simüle edilen saniye')
    parser.add_argument('--step', type=float, default=60, help='Her döngüde simüle edilen saniye (varsayılan 60)')
    parser.add_argument('--out', default='simulation_results.jsonl')
    args = parser.parse_args()
    if args.speed <= 0 or args.step <= 0:
        parser.error('speed ve step pozitif olmalı')
    import time
    raw = json.loads(Path(args.plan).read_text(encoding='utf-8-sig'))
    plan = raw.get('optimization', raw)
    battery_data = json.loads(Path(args.battery).read_text(encoding='utf-8-sig'))
    battery_data = battery_data.get('battery', battery_data)
    engine = SimulatorEngine(plan, VirtualBattery(**battery_data))
    print(f'Simülasyon: {engine.now.isoformat()} → {engine.end.isoformat()} | hız x{args.speed:g}')
    with Path(args.out).open('w', encoding='utf-8') as file:
        while engine.now < engine.end:
            for record in engine.step(args.step):
                file.write(json.dumps(record, ensure_ascii=False) + '\n')
                if engine.now.minute == 0 and engine.now.second == 0:
                    print(f'{engine.now.isoformat()} | SOC %{engine.soc:.2f} | '
                          f'şebeke: {record["grid_export_kw"] - record["grid_import_kw"]:.2f} kW')
            time.sleep(args.step / args.speed)
    print('Simülasyon tamamlandı. Sonuç:', args.out)
    print(json.dumps({k: round(v, 4) for k, v in engine.totals.items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
