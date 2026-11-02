#!/usr/bin/env python3
"""ScadaWatt EMS /optimize response audit, independent of project dependencies.
Usage: python ems_ekonomik_denetim.py optimization_result.json
Never connects to a server or changes any files.
"""
import json
import math
import sys
from pathlib import Path


def number(obj, key):
    value = obj.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def fmt(value, unit=''):
    return ('veri yok' if value is None else f'{value:,.3f} {unit}'.strip())


def main(path):
    obj = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    opt = obj.get('optimization', obj)
    rows = opt.get('schedule', [])
    if not isinstance(rows, list) or not rows:
        raise ValueError('optimization.schedule dizisi bulunamadı veya boş')

    print('\n=== EMS OPTIMIZASYON DENETIMI ===')
    print('Saat sayisi:', len(rows))
    pricing = obj.get('pricing_summary', {})
    print('Istenen donem:', pricing.get('start_date', '?'), '-', pricing.get('end_date', '?'))
    print('Plan baslangici:', rows[0].get('datetime', '?'))
    print('Plan son saati:', rows[-1].get('datetime', '?'))
    print('Etkin sarj limiti:', fmt(number(opt, 'effective_max_charge_kw'), 'kW'))
    print('Etkin desarj limiti:', fmt(number(opt, 'effective_max_discharge_kw'), 'kW'))
    print('Cihaz limitleri:', json.dumps(opt.get('device_limits', {}), ensure_ascii=False))

    mapped = [
        ('GES uretimi', 'generation_kwh', 'kWh'),
        ('Tesis tuketimi', 'site_load_kwh', 'kWh'),
        ('Batarya sarj', 'charge_energy_kwh', 'kWh'),
        ('Batarya desarj', 'discharge_energy_kwh', 'kWh'),
        ('Sebeke satis', 'grid_export_kwh', 'kWh'),
        ('Sebeke alis', 'grid_import_kwh', 'kWh'),
        ('Uretim kisintisi', 'curtailment_kwh', 'kWh'),
        ('Satis geliri', 'grid_export_revenue_tl', 'TL'),
        ('Alis maliyeti', 'grid_import_cost_tl', 'TL'),
        ('Yipranma maliyeti', 'degradation_cost_tl', 'TL'),
    ]
    print('\n-- Saatlik plan toplamlari --')
    totals = {}
    for label, key, unit in mapped:
        vals = [number(row, key) for row in rows]
        total = sum(x for x in vals if x is not None) if any(x is not None for x in vals) else None
        totals[key] = total
        print(f'{label:22}: {fmt(total, unit)}')

    print('\n-- API ekonomik ozeti --')
    keys = [
        ('Batisiz referans', 'direct_sale_revenue_tl'),
        ('Bataryali net', 'net_theoretical_revenue_tl'),
        ('Ek katkı', 'net_revenue_difference_tl'),
        ('Satis geliri', 'total_grid_export_revenue_tl'),
        ('Alis maliyeti', 'total_grid_import_cost_tl'),
        ('Yipranma maliyeti', 'total_degradation_cost_tl'),
        ('Toplam desarj', 'total_discharge_energy_kwh'),
    ]
    for label, key in keys:
        print(f'{label:22}: {fmt(number(opt, key), "kWh" if "kwh" in key else "TL")}')

    issues = []
    cautions = []
    revenue = number(opt, 'total_grid_export_revenue_tl')
    cost = number(opt, 'total_grid_import_cost_tl')
    wear = number(opt, 'total_degradation_cost_tl')
    net = number(opt, 'net_theoretical_revenue_tl')
    baseline = number(opt, 'direct_sale_revenue_tl')
    delta = number(opt, 'net_revenue_difference_tl')
    if None not in (revenue, cost, wear, net) and abs((revenue - cost - wear) - net) > 0.08:
        issues.append(f'Net ekonomik deger tutarsiz: {revenue-cost-wear:.3f} TL hesaplandi, API {net:.3f} TL diyor')
    if None not in (net, baseline, delta) and abs((net - baseline) - delta) > 0.08:
        issues.append(f'Ek katkı tutarsiz: {net-baseline:.3f} TL hesaplandi, API {delta:.3f} TL diyor')

    for key, total_key in [
        ('grid_export_kwh', 'total_grid_export_kwh'),
        ('grid_import_kwh', 'total_grid_import_kwh'),
        ('discharge_energy_kwh', 'total_discharge_energy_kwh'),
        ('grid_export_revenue_tl', 'total_grid_export_revenue_tl'),
        ('grid_import_cost_tl', 'total_grid_import_cost_tl'),
        ('degradation_cost_tl', 'total_degradation_cost_tl'),
    ]:
        s = totals[key]
        t = number(opt, total_key)
        if s is not None and t is not None and abs(s-t) > max(.1, abs(t)*.0002):
            issues.append(f'{key} saatlik toplami ({s:.3f}) ile {total_key} ({t:.3f}) uyusmuyor')

    if None not in (wear, number(opt, 'degradation_cost_tl_per_kwh')):
        rate = number(opt, 'degradation_cost_tl_per_kwh')
        if rate > 0:
            print(f'\nYipranmadan tahmini desarj: {wear/rate:,.3f} kWh (katsayi {rate} TL/kWh)')
    limit = number(opt, 'grid_export_limit_kw')
    e_charge = number(opt, 'effective_max_charge_kw')
    e_discharge = number(opt, 'effective_max_discharge_kw')
    for i, row in enumerate(rows):
        ts = row.get('datetime', f'#{i}')
        pv = number(row, 'generation_kwh')
        load = number(row, 'site_load_kwh')
        ch = number(row, 'charge_energy_kwh')
        dis = number(row, 'discharge_energy_kwh')
        imp = number(row, 'grid_import_kwh')
        exp = number(row, 'grid_export_kwh')
        cut = number(row, 'curtailment_kwh')
        vals = (pv, load, ch, dis, imp, exp, cut)
        if all(v is not None for v in vals):
            residual = pv + dis + imp - load - ch - exp - cut
            if abs(residual) > max(0.1, abs(pv)*.00005):
                issues.append(f'{ts}: enerji dengesi hatasi: {residual:.3f} kWh')
            if ch > 1e-4 and dis > 1e-4:
                issues.append(f'{ts}: ayni saatte sarj ve desarj var')
            if imp > 1e-4 and exp > 1e-4:
                issues.append(f'{ts}: ayni saatte sebeke alis ve satis var')
            if limit is not None and exp > limit + 0.1:
                issues.append(f'{ts}: sebeke satis limiti asildi {exp:.2f}>{limit:.2f}')
            if e_charge is not None and ch > e_charge + .1:
                issues.append(f'{ts}: sarj gucu limiti asildi {ch:.2f}>{e_charge:.2f}')
            if e_discharge is not None and dis > e_discharge + .1:
                issues.append(f'{ts}: desarj gucu limiti asildi {dis:.2f}>{e_discharge:.2f}')
            if ch > max(0, pv - load) + .1:
                cautions.append(f'{ts}: sarj, GES fazlasindan buyuk ({ch:.3f} > {max(0,pv-load):.3f} kWh); solar_only kuralini kontrol et')
        if ch is not None and ch > .01 or dis is not None and dis > .01:
            print('  AKSIYON', ts, 'sarj',fmt(ch,'kWh'), 'desarj',fmt(dis,'kWh'), 'SOC',row.get('previous_soc'),'=>',row.get('new_soc'), 'PTF',fmt(number(row,'price_tl_mwh'),'TL/MWh'))

    if len(rows) != 24:
        cautions.append(f'Plan {len(rows)} saat; tek gun testinde 24 saat beklenir (yaz saati gecisleri haric)')
    if totals['discharge_energy_kwh'] is not None and totals['discharge_energy_kwh'] <= 500:
        cautions.append('Bataryanin gunluk desarji <= 500 kWh; 100 MWh batarya icin cok dusuk. Kapasite ve BMS/PCS limitlerini dogrula.')
    print('\n-- SONUC --')
    print('Hata:', len(issues), '| Uyari:', len(cautions))
    for s in issues[:30]: print('HATA:', s)
    for s in cautions[:30]: print('UYARI:', s)
    if not issues:
        print('Temel aritmetik/enerji denge kontrolleri gecti; bu, fiziksel modelin ve optimum kararlarin tam dogrulandigi anlamina GELMEZ.')
    return 1 if issues else 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('Kullanim: python ems_ekonomik_denetim.py optimization_result.json')
        sys.exit(2)
    try:
        sys.exit(main(sys.argv[1]))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print('HATA:', exc, file=sys.stderr)
        sys.exit(2)
