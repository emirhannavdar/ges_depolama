"""Completed virtual-field report, with source labels and no credential exposure."""
from __future__ import annotations

import io
from collections import Counter
from datetime import datetime, timezone
from typing import Any


def build_report(engine: Any) -> dict[str, Any]:
    if engine.state != 'completed' or len(engine.history) != engine.total_steps:
        raise ValueError('Rapor yalnızca 24 saatlik simülasyon tamamlandığında üretilir.')
    req = engine.config
    b, plant = req.battery, req.plant
    rows = engine.history
    dt = req.step_seconds / 3600
    hours = []
    for hr in range(24):
        slice_ = rows[hr * (3600 // req.step_seconds):(hr+1)*(3600 // req.step_seconds)]
        def energy(key: str) -> float:
            return round(sum(float(x.get(key,0))*dt for x in slice_), 3)
        hours.append({
            'hour': f'{hr:02d}:00',
            'pv_kwh': energy('pv_kw'), 'site_load_kwh': energy('load_kw'),
            'charge_kwh': energy('charge_kw'), 'discharge_kwh': energy('discharge_kw'),
            'charge_conversion_loss_kwh': round(energy('charge_kw')*(1-b.charge_efficiency),3),
            'discharge_conversion_loss_kwh': round(energy('discharge_kw')*(1/b.discharge_efficiency-1),3),
            'battery_conversion_loss_kwh': round(
                energy('charge_kw')*(1-b.charge_efficiency)
                + energy('discharge_kw')*(1/b.discharge_efficiency-1),3),
            'grid_import_kwh': energy('grid_import_kw'), 'grid_export_kwh': energy('grid_export_kw'),
            'curtailment_kwh': energy('curtailment_kw'),
            'ptf_tl_mwh': engine.prices[hr],
            'soc_end_pct': slice_[-1]['soc_pct'],
            'action': max(Counter(x['action'] for x in slice_).items(), key=lambda x: x[1])[0],
            'temperature_c': engine.weather['hours'][hr].get('temperature_c'),
            'cloud_cover_pct': engine.weather['hours'][hr].get('cloud_cover_pct'),
            'irradiance_wm2': engine.weather['hours'][hr].get('irradiance_wm2'),
        })
    summary = engine.record()
    totals = summary['totals']
    soc_gap = summary['terminal_soc_difference_pct']
    target_gap = summary['target_soc_gap_pp']
    warnings = []
    if b.allow_initial_reserve_discharge:
        warnings.append('Başlangıç SOC rezervi kullanılabilir; kaynağı bilinmiyor, GES üretimi olarak etiketlenmez. Şebekeden batarya şarjı yasak.')
    if abs(target_gap) > 0.05:
        warnings.append(f'HEDEF SOC SAĞLANAMADI: bitiş SOC hedefinden {target_gap:+.2f} yüzde puan saptı. Bu ekonomik fark tekrarlanabilir net kazanç olarak yorumlanamaz.')
    elif abs(soc_gap) > 0.05:
        warnings.append('Hedef SOC sağlandı ancak başlangıç SOC farklı: karşılaştırmada kalan enerji stoğunu ayrıca değerleyin.')
    if totals['unserved_kwh'] > 0.001:
        warnings.append('Şebekeden alış anlaşma gücü aşıldı: karşılanamayan tesis yükü var. Bu durumda net ekonomik sonuç kesinti maliyetini kapsamaz.')
    if totals.get('baseline_unserved_kwh', 0) > 0.001:
        warnings.append('Bataryasız referans senaryoda da karşılanamayan tesis yükü var; bataryalı karşılaştırma kesinti cezasını içermez.')
    if totals['curtailment_kwh'] > 0.001:
        warnings.append('GES enerjisinin bir kısmı kısıldı.')
    if abs(totals.get('site_energy_balance_residual_kwh', 0.0)) > 0.05:
        warnings.append(
            'ENERJİ DENGESİ UYARISI: saha girdileri ile karşılanan yük, satış, kısıntı, '
            'batarya dönüşüm kaybı ve depolanan enerji değişimi arasında '
            f"{totals['site_energy_balance_residual_kwh']:.3f} kWh fark var. Sonuç denetlenmeli."
        )
    if summary['incremental_battery_tl'] < 0:
        warnings.append('Bu senaryoda bataryanın ek ekonomik katkısı negatiftir; yıpranma ve fırsat maliyetini değerlendirin.')
    warnings.append('Tüm üretim ve batarya ölçümleri sanaldır; Visual Crossing meteoroloji verisi ve EPİAŞ PTF referansı kullanılır. Ticari fatura veya gerçek saha ölçümü değildir.')
    return {
        'schema_version': '1.0', 'kind': 'completed_virtual_field_simulation',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'session_id': engine.session_id, 'site_name': req.site_name,
        'date': req.date, 'state': engine.state,
        'location': {'latitude': plant.latitude,'longitude':plant.longitude,'timezone':engine.weather.get('timezone')},
        'site': {'installed_kwp':plant.installed_kwp,'panel_count':plant.panel_count,'panel_power_wp':plant.panel_power_wp,
                 'site_load_kw':plant.site_load_kw,'site_load_variation_pct':plant.site_load_variation_pct,
                 'export_limit_kw':plant.export_limit_kw,'import_limit_kw':plant.import_limit_kw},
        'battery': {'capacity_kwh':b.capacity_kwh,'soh_pct':b.soh,'initial_soc_pct':engine.initial_soc,
                    'final_soc_pct':summary['soc_pct'],'soc_difference_pp':soc_gap,
                    'target_final_soc_pct':engine.target_soc,'target_soc_gap_pp':target_gap,
                    'terminal_soc_met':summary['terminal_soc_met'],
                    'optimization_method':'ROLLING_HOURLY_MILP',
                    'reoptimization_count':summary['reoptimization_count'],
                    'min_soc_pct':b.min_soc,'max_soc_pct':b.max_soc,
                    'max_charge_kw':b.charge_limit(),'max_discharge_kw':b.discharge_limit(),
                    'charge_efficiency':b.charge_efficiency,'discharge_efficiency':b.discharge_efficiency,
                    'solar_origin_kwh_end':summary['solar_origin_stored_kwh'],
                    'initial_reserve_kwh_end':summary['initial_reserve_stored_kwh'],
                    'initial_reserve_discharge_enabled':b.allow_initial_reserve_discharge,
                    'max_charge_c':b.max_charge_c,'max_discharge_c':b.max_discharge_c,
                    'bms_charge_allowed':b.bms_charge_allowed,'bms_discharge_allowed':b.bms_discharge_allowed,
                    'pcs_online':b.pcs_online,'bms_charge_limit_kw':b.bms_charge_limit_kw,
                    'bms_discharge_limit_kw':b.bms_discharge_limit_kw,'pcs_limit_kw':b.pcs_limit_kw},
        'weather': {'source':engine.weather.get('source'),'sunrise':engine.weather.get('sunrise'),'sunset':engine.weather.get('sunset')},
        'market': {'source':'EPİAŞ PTF (supplied price_schedule)','min_ptf_tl_mwh':min(engine.prices),'max_ptf_tl_mwh':max(engine.prices)},
        'energy_origin': {'battery_charge_source':'GES surplus only', 'grid_to_battery_kwh':0, 'initial_soc_origin':'unknown; dispatch only if allow_initial_reserve_discharge is true', 'solar_origin_end_kwh':summary['solar_origin_stored_kwh']},
        'totals': totals,
        'energy_balance': {
            'input_kwh': round(totals['pv_kwh']+totals['import_kwh'],3),
            'served_load_kwh': totals['served_load_kwh'],
            'grid_export_kwh': totals['export_kwh'],
            'curtailment_kwh': totals['curtailment_kwh'],
            'battery_conversion_loss_kwh': totals['battery_conversion_loss_kwh'],
            'stored_energy_change_kwh': totals['stored_energy_change_kwh'],
            'output_and_storage_kwh': round(
                totals['served_load_kwh']+totals['export_kwh']+totals['curtailment_kwh']
                +totals['battery_conversion_loss_kwh']+totals['stored_energy_change_kwh'],3),
            'residual_kwh': totals['site_energy_balance_residual_kwh'],
            'formula': 'GES + şebeke alış = karşılanan tesis yükü + şebeke satışı + üretim kısıntısı + batarya dönüşüm kaybı + depolanan enerji değişimi'
        },
        'economics': {'sales_tl':round(totals['sales_tl'],2), 'purchases_tl':round(totals['purchases_tl'],2),
                      'degradation_tl':round(totals['degradation_tl'],2),
                      'degradation_tl_per_kwh':req.degradation_tl_per_kwh,
                      'low_price_fraction':req.low_price_fraction,
                      'high_price_fraction':req.high_price_fraction,
                      'net_with_battery_tl':summary['net_with_battery_tl'],
                      'net_without_battery_tl':summary['net_without_battery_tl'],
                      'incremental_battery_tl':summary['incremental_battery_tl']},
        'hourly':hours, 'warnings':warnings,
        'limits':'Local single-session in-memory record; restart of API loses this report. No real meters, contracts, taxes, trading fees or invoicing.'
    }


def create_pdf(report: dict[str, Any]) -> bytes:
    """Two-page A4 report; use ReportLab and bundled OS font for Turkish glyphs."""
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as exc:
        raise RuntimeError('PDF için reportlab kurulmalı: python -m pip install reportlab') from exc
    from pathlib import Path
    fontbase = Path(__file__).resolve().parent / 'report_fonts'
    regular = fontbase / 'DejaVuSans.ttf'
    bold = fontbase / 'DejaVuSans-Bold.ttf'
    if not regular.exists():
        candidates = [
            (Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')),
            (Path('C:/Windows/Fonts/arial.ttf'), Path('C:/Windows/Fonts/arialbd.ttf')),
            (Path('C:/Windows/Fonts/segoeui.ttf'), Path('C:/Windows/Fonts/segoeuib.ttf')),
        ]
        for r,bd in candidates:
            if r.exists() and bd.exists():
                regular, bold = r,bd
                break
    if regular.exists() and 'EMSFont' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('EMSFont',str(regular)))
        pdfmetrics.registerFont(TTFont('EMSFontBold',str(bold)))
    basefont = 'EMSFont' if regular.exists() else 'Helvetica'
    boldfont = 'EMSFontBold' if regular.exists() else 'Helvetica-Bold'
    output = io.BytesIO()
    pdf = canvas.Canvas(output,pagesize=A4, pageCompression=1)
    pdf.setTitle(f"ScadaWatt - {report['site_name']} - {report['date']} - Simülasyon Raporu")
    W,H = A4; ink='#18324A'; green='#087D68'; muted='#617482'; rule='#D5E0E3'
    def txt(x,y,t,size=9,color=ink,bold=False):
        pdf.setFont(boldfont if bold else basefont,size);pdf.setFillColor(color);pdf.drawString(x,y,str(t))
    def line(y):
        pdf.setStrokeColor(rule);pdf.line(42,y,W-42,y)
    def fmt(n,nd=1):
        return f'{float(n):,.{nd}f}'.replace(',','X').replace('.',',').replace('X','.')
    def header(page):
        pdf.setFillColor(ink);pdf.rect(0,H-105,W,105,fill=1,stroke=0)
        txt(42,H-40,'SCADAWATT  /  ENERGY DIGITAL TWIN',12,'#FFFFFF',True)
        txt(42,H-65,report['site_name'],15,'#FFFFFF',True)
        txt(42,H-86,f"{report['date']}  ·  24 saatlik sanal saha raporu",9,'#D2E6EA')
        txt(42,30,f"ID: {report['session_id'][:18]}...",7,muted)
        txt(W-80,30,f'{page} / 2',8,muted)
    def metric(x,y,label,value,accent=False):
        txt(x,y,label,8,muted)
        txt(x,y-23,value,15,green if accent else ink,True)
    header(1)
    e=report['economics'];t=report['totals'];b=report['battery'];st=report['site']
    txt(42,H-141,'01 / EKONOMİK SONUÇ',11,ink,True);line(H-151)
    metric(42,H-175,'Batarya ek katkısı',f"{fmt(e['incremental_battery_tl'],2)} TL",True)
    metric(320,H-175,'Bataryalı net değer',f"{fmt(e['net_with_battery_tl'],2)} TL")
    metric(42,H-235,'Bataryasız net değer',f"{fmt(e['net_without_battery_tl'],2)} TL")
    metric(320,H-235,'Batarya yıpranması',f"{fmt(e['degradation_tl'],2)} TL")
    y=H-310
    txt(42,y,'02 / GÜNLÜK ENERJİ DENGESİ',11,ink,True);line(y-10)
    fields=[('GES üretimi',t['pv_kwh']),('Tesis tüketimi',t['served_load_kwh']),
            ('Batarya şarjı',t['charge_kwh']),('Batarya deşarjı',t['discharge_kwh']),
            ('Batarya dönüşüm kaybı',t['battery_conversion_loss_kwh']),('Şebeke satışı',t['export_kwh']),
            ('Şebekeden alış',t['import_kwh']),('Üretim kısıntısı',t['curtailment_kwh']),
            ('Karşılanamayan yük',t['unserved_kwh'])]
    for i,(k,v) in enumerate(fields):
        yy=y-38-(i//3)*38;xx=42+(i%3)*180
        txt(xx,yy,k,7.5,muted);txt(xx,yy-15,f'{fmt(v,1)} kWh',10,ink,True)
    txt(42,y-153,f"Enerji dengesi farkı: {fmt(t['site_energy_balance_residual_kwh'],3)} kWh",7,green if abs(t['site_energy_balance_residual_kwh'])<=0.05 else '#B54732',True)
    y=H-535
    txt(42,y,'03 / SAHA & BATARYA AYARLARI',11,ink,True);line(y-10)
    rows=[f"Kurulu GES: {fmt(st['installed_kwp'])} kWp    |    Panel: {st['panel_count']} x {fmt(st['panel_power_wp'],0)} Wp",
          f"Batarya: {fmt(b['capacity_kwh'])} kWh    |    Şarj/deşarj: {fmt(b['max_charge_kw'],0)} / {fmt(b['max_discharge_kw'],0)} kW",
          f"SOC: %{fmt(b['initial_soc_pct'],2)} -> %{fmt(b['final_soc_pct'],2)} | Hedef: %{fmt(b['target_final_soc_pct'],2)} | SOH: %{fmt(b['soh_pct'],1)}",
          f"Yıpranma: {fmt(e['degradation_tl_per_kwh'],2)} TL/kWh    |    C-rate: {fmt(b['max_charge_c'],2)} / {fmt(b['max_discharge_c'],2)}",
          f"Alış anlaşma gücü: {fmt(st['import_limit_kw'],0) if st['import_limit_kw'] is not None else 'Sınırsız'} kW   |   Satış limiti: {fmt(st['export_limit_kw'],0)} kW"]
    for i,row in enumerate(rows): txt(42,y-37-i*24,row,8)
    txt(42,106,f"Gün doğumu: {report['weather']['sunrise'] or 'Bilinmiyor'} / Gün batımı: {report['weather']['sunset'] or 'Bilinmiyor'}",7,muted)
    txt(42,87,'Veri kaynakları: Visual Crossing hava verisi, EPİAŞ PTF; üretim ve enerji akışları simüledir.',7,muted)
    pdf.showPage();header(2)
    txt(42,H-137,'04 / SAATLİK AKIŞ',11,ink,True);line(H-150)
    columns=[(42,'Saat'),(92,'GES'),(148,'Yük'),(204,'Şarj'),(260,'Deşarj'),(316,'Kayıp'),(388,'PTF TL/MWh'),(520,'SOC %')]
    for x,label in columns: txt(x,H-172,label,7,muted,True)
    for i,row in enumerate(report['hourly']):
        yy=H-190-i*17
        if i%2==0:
            pdf.setFillColor('#F2F6F7');pdf.rect(40,yy-4,W-80,16,stroke=0,fill=1)
        vals=[row['hour'],fmt(row['pv_kwh'],1),fmt(row['site_load_kwh'],1),fmt(row['charge_kwh'],1),fmt(row['discharge_kwh'],1),fmt(row['battery_conversion_loss_kwh'],1),fmt(row['ptf_tl_mwh'],0),fmt(row['soc_end_pct'],1)]
        for (x,_),v in zip(columns,vals):txt(x,yy,str(v),7)
    base=H-625
    txt(42,base,'05 / DEĞERLENDİRME & UYARILAR',11,ink,True);line(base-10)
    cursor=base-30
    for warning in report['warnings']:
        # Line wrap using rendered width, not character count; fit within A4.
        words=warning.split();lines=[];current=''
        for word in words:
            cand=(current+' '+word).strip()
            if pdfmetrics.stringWidth(cand,basefont,7)>W-100 and current:
                lines.append(current);current=word
            else:current=cand
        if current:lines.append(current)
        for index,part in enumerate(lines):
            txt(48 if index else 42,cursor,('• ' if index==0 else '')+part,7,muted)
            cursor-=12
        cursor-=5
    pdf.save()
    return output.getvalue()
