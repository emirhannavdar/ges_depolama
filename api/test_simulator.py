"""Run: python -m api.test_simulator"""
from datetime import datetime, timedelta, timezone
from api.simulator import SimulatorEngine, VirtualBattery


def battery(soc=50):
    return VirtualBattery(capacity_kwh=100, soc=soc, soh=100, min_soc=10,
                          max_soc=90, max_charge_kw=50, max_discharge_kw=50,
                          charge_efficiency=.95, discharge_efficiency=.95)


def plan(rows, soc=50, exp=5, imp=None):
    t0 = datetime(2026, 11, 2, 12, tzinfo=timezone(timedelta(hours=3)))
    schedule = []
    for i, item in enumerate(rows):
        pv, load, charge, discharge = item
        schedule.append(dict(datetime=(t0 + timedelta(hours=i)).isoformat(),
                             generation_kwh=pv, site_load_kwh=load,
                             charge_energy_kwh=charge,
                             discharge_energy_kwh=discharge,
                             curtailment_kwh=0, price_tl_mwh=1000))
    return dict(schedule=schedule, initial_soc=soc,
                grid_export_limit_kw=exp, grid_import_limit_kw=imp)


def almost(a, b):
    assert abs(a-b) < 1e-5, f'{a} != {b}'


def test_charge_discharge():
    s = SimulatorEngine(plan([(20, 5, 10, 0), (0, 8, 0, 8)]), battery())
    s.step(3600)
    almost(s.soc, 59.5)
    almost(s.totals['grid_export_kwh'], 5)
    s.step(3600)
    almost(s.totals['battery_discharge_kwh'], 8)
    almost(s.totals['grid_import_kwh'], 0)
    almost(s.soc, 59.5-8/.95)
    print('Şarj, deşarj, şebeke ve SOC testi başarılı')


def test_initial_energy_not_exported():
    s = SimulatorEngine(plan([(0, 0, 0, 5)]), battery())
    s.step(3600)
    almost(s.totals['grid_export_kwh'], 0)
    almost(s.soc, 50)
    s = SimulatorEngine(plan([(0, 5, 0, 5)]), battery())
    s.step(3600)
    almost(s.totals['battery_discharge_kwh'], 5)
    almost(s.totals['grid_import_kwh'], 0)
    print('Kaynağı bilinmeyen başlangıç enerjisi yalnızca tesis yükünü besledi')


def test_pcs_offline():
    s = SimulatorEngine(plan([(20, 0, 10, 0)]), battery())
    s.update_device_limits(pcs_online=False)
    s.step(3600)
    almost(s.totals['battery_charge_kwh'], 0)
    almost(s.totals['grid_export_kwh'], 5)
    almost(s.totals['curtailment_kwh'], 15)
    print('PCS çevrimdışı ve üretim kısıntısı testi başarılı')


def test_split_hours():
    s = SimulatorEngine(plan([(20, 5, 10, 0), (0, 8, 0, 8)]), battery())
    s.step(7200)
    almost(s.totals['battery_charge_kwh'], 10)
    almost(s.totals['battery_discharge_kwh'], 8)
    almost(s.soc, 59.5 - 8/.95)
    print('Saat sınırı geçişi testi başarılı')


if __name__ == '__main__':
    test_charge_discharge()
    test_initial_energy_not_exported()
    test_pcs_offline()
    test_split_hours()
    print('Tüm simülatör testleri başarılı!')
