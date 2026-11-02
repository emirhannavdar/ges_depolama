"""Local-only, single-user, forecast-driven virtual solar+BESS field.

Uses verified user-supplied Visual Crossing hourly records and EPİAŞ prices from an
existing /optimize response; no simulated pricing, no physical device IO.
Dynamically models power, SOC, limits and economic comparison each time slice.
Not a rolling MILP optimizer. No credentials are persisted.
"""
from __future__ import annotations

import asyncio
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from scipy.optimize import milp, Bounds, LinearConstraint
import numpy as np

router = APIRouter(prefix='/digital-twin', tags=['Sanal Saha / Digital Twin'])


def safe_num(value: Any, name: str, minimum: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name}: sayı bekleniyor') from exc
    if not math.isfinite(f) or f < minimum:
        raise ValueError(f'{name}: değer en az {minimum} olmalı')
    return f


class Plant(BaseModel):
    panel_count: int = Field(gt=0, le=5_000_000)
    panel_power_wp: float = Field(gt=0, le=2000)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    performance_ratio: float = Field(default=0.85, gt=0, le=1)
    temperature_coefficient: float = Field(default=-0.0035, ge=-0.01, le=0)
    site_load_kw: float = Field(ge=0)
    site_load_variation_pct: float = Field(default=0, ge=0, le=50)
    export_limit_kw: float = Field(ge=0)
    import_limit_kw: float | None = Field(default=None, ge=0, description="Şebekeden alış anlaşma gücü kW: yalnızca tesis tüketiminin çekiş sınırı, batarya şarj izni değildir.")
    solar_only_charging: bool = True

    @model_validator(mode="after")
    def require_solar_charging(self):
        if not self.solar_only_charging:
            raise ValueError("Bu EMS sürümünde batarya yalnızca GES fazlasından şarj olabilir; şebekeden şarj desteklenmez.")
        return self

    @property
    def installed_kwp(self) -> float:
        return self.panel_count * self.panel_power_wp / 1000


class Battery(BaseModel):
    capacity_kwh: float = Field(gt=0)
    soc: float = Field(ge=0, le=100)
    soh: float = Field(default=100, gt=0, le=100)
    min_soc: float = Field(default=10, ge=0, le=100)
    max_soc: float = Field(default=90, ge=0, le=100)
    max_charge_kw: float = Field(gt=0)
    max_discharge_kw: float = Field(gt=0)
    max_charge_c: float = Field(default=0.5, gt=0)
    max_discharge_c: float = Field(default=0.5, gt=0)
    charge_efficiency: float = Field(default=0.95, gt=0, le=1)
    discharge_efficiency: float = Field(default=0.95, gt=0, le=1)
    bms_charge_allowed: bool = True
    bms_discharge_allowed: bool = True
    pcs_online: bool = True
    allow_initial_reserve_discharge: bool = True
    bms_charge_limit_kw: float | None = Field(default=None, ge=0)
    bms_discharge_limit_kw: float | None = Field(default=None, ge=0)
    pcs_limit_kw: float | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def check(self):
        if not self.min_soc < self.max_soc or not self.min_soc <= self.soc <= self.max_soc:
            raise ValueError('SOC min / başlangıç / maks değerleri uyumsuz')
        return self

    @property
    def effective_kwh(self):
        return self.capacity_kwh * self.soh / 100

    def charge_limit(self):
        if not self.bms_charge_allowed or not self.pcs_online:
            return 0.0
        return min(self.max_charge_kw, self.max_charge_c*self.effective_kwh,
                   self.bms_charge_limit_kw if self.bms_charge_limit_kw is not None else math.inf,
                   self.pcs_limit_kw if self.pcs_limit_kw is not None else math.inf)

    def discharge_limit(self):
        if not self.bms_discharge_allowed or not self.pcs_online:
            return 0.0
        return min(self.max_discharge_kw, self.max_discharge_c*self.effective_kwh,
                   self.bms_discharge_limit_kw if self.bms_discharge_limit_kw is not None else math.inf,
                   self.pcs_limit_kw if self.pcs_limit_kw is not None else math.inf)


class StartRequest(BaseModel):
    site_name: str = Field(default='Sanal GES Sahası', max_length=120)
    plant: Plant
    battery: Battery
    date: str
    visual_crossing_key: str | None = None
    price_schedule: list[dict[str, Any]] = Field(min_length=24, max_length=48)
    speed: float = Field(default=3600, ge=1, le=86400)
    step_seconds: int = Field(default=60, ge=10, le=900)
    degradation_tl_per_kwh: float = Field(default=0.5, ge=0)
    low_price_fraction: float = Field(default=0.35, gt=0, lt=1)
    high_price_fraction: float = Field(default=0.7, gt=0, lt=1)
    target_final_soc: float | None = Field(default=None, ge=0, le=100, description='Gün sonu SOC hedefi; belirtilmezse başlangıç SOC')

    @model_validator(mode='after')
    def target_in_range(self):
        if self.target_final_soc is not None and not self.battery.min_soc <= self.target_final_soc <= self.battery.max_soc:
            raise ValueError('Hedef bitiş SOC min ve maksimum SOC aralığında olmalı.')
        return self


class WeatherRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    date: str
    api_key: str | None = None


def _server_weather_key(provided: str | None = None) -> str:
    # Existing local backend already has Visual Crossing credentials; never return key to UI.
    if provided:
        return provided
    key = os.getenv('VISUAL_CROSSING_API_KEY', '')
    if not key:
        try:
            from config import VISUAL_CROSSING_API_KEY
            key = VISUAL_CROSSING_API_KEY
        except ImportError:
            pass
    if not key:
        raise HTTPException(503, 'Visual Crossing anahtarı backend config.py / ortam değişkeninde bulunamadı.')
    return key


def fetch_weather(latitude: float, longitude: float, day: str, api_key: str | None):
    try:
        datetime.strptime(day, '%Y-%m-%d')
    except ValueError as exc:
        raise HTTPException(422, 'Tarih YYYY-MM-DD biçiminde olmalı') from exc
    # Date bounds and lat/lon are validated. Key stays in-memory and outside URLs in logs.
    url = f'https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{latitude},{longitude}/{day}'
    try:
        response = requests.get(url, params={
            'key': _server_weather_key(api_key), 'unitGroup': 'metric', 'include': 'hours',
            'elements': 'datetime,datetimeEpoch,temp,cloudcover,solarradiation,conditions,icon,precip,sunrise,sunset',
            'contentType': 'json'
        }, timeout=22)
    except requests.RequestException as exc:
        raise HTTPException(502, 'Visual Crossing servisine ulaşılamadı') from exc
    if response.status_code in (400, 401, 403):
        raise HTTPException(502, 'Visual Crossing anahtarı, kota veya erişim hatası')
    if not response.ok:
        raise HTTPException(502, f'Visual Crossing HTTP {response.status_code}')
    try:
        payload = response.json()
        records = payload['days'][0]['hours']
        timezone_name = payload.get('timezone', '')
        if len(records) < 24:
            raise ValueError('Eksik saat')
        hours = []
        for hour in records:
            radiation = hour.get('solarradiation')
            if radiation is None:
                raise ValueError('solarradiation eksik')
            hours.append({
                'datetime': hour['datetime'],
                'irradiance_wm2': safe_num(radiation, 'solarradiation'),
                'temperature_c': float(hour['temp']) if hour.get('temp') is not None else None,
                'cloud_cover_pct': float(hour['cloudcover']) if hour.get('cloudcover') is not None else None,
                'conditions': hour.get('conditions') or '',
                'icon': hour.get('icon') or '',
            })
        return {'sunrise':payload['days'][0].get('sunrise'), 'sunset':payload['days'][0].get('sunset'), 'date': day, 'timezone': timezone_name, 'latitude': latitude,
                'longitude': longitude, 'hours': hours[:24], 'source': 'Visual Crossing Timeline hourly'}
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise HTTPException(502, 'Visual Crossing saatlik hava yanıtı eksik veya bozuk') from exc


def prices_for_day(rows: list[dict[str,Any]], day: str):
    matching = {}
    for row in rows:
        stamp = str(row.get('datetime', ''))
        if stamp[:10] != day:
            continue
        hour = int(stamp[11:13]) if len(stamp) >= 13 else -1
        if not 0 <= hour < 24:
            continue
        value = safe_num(row.get('price_tl_mwh'), 'EPİAŞ PTF')
        if hour in matching:
            raise HTTPException(422, 'EPİAŞ planında yinelenen saat')
        matching[hour] = value
    if len(matching) != 24:
        raise HTTPException(422, f'{day} için tam 24 saatlik gerçek EPİAŞ PTF gerekli; bulunan: {len(matching)}')
    return [matching[i] for i in range(24)]


def quantile(values: list[float], fraction: float):
    ordered = sorted(values)
    index = (len(ordered)-1)*fraction
    left = int(index)
    return ordered[left] + (ordered[min(left+1,len(ordered)-1)]-ordered[left])*(index-left)


class Engine:
    def __init__(self, config: StartRequest, weather: dict[str,Any]):
        self.config = config
        self.weather = weather
        self.prices = prices_for_day(config.price_schedule, config.date)
        self.start = datetime.strptime(config.date, '%Y-%m-%d').replace(tzinfo=ZoneInfo(weather.get('timezone') or 'Europe/Istanbul'))
        self.cursor = 0
        self.total_steps = 86400 // config.step_seconds
        # Require exact whole-day steps: no partial slices or mismatched totals.
        if 86400 % config.step_seconds:
            raise HTTPException(422, 'step_seconds 86400 sayısını tam bölmeli')
        self.soc = config.battery.soc
        self.initial_soc = self.soc
        self.target_soc = config.target_final_soc if config.target_final_soc is not None else config.battery.soc
        self.hour_plan = None
        self.hour_plan_index = -1
        self.reoptimization_count = 0
        self.plan_errors = []
        # Initial stored energy has UNKNOWN origin, and must never be relabelled as GES.
        # Its usability is explicitly controlled by the scenario input.
        # Grid-to-battery CHARGING remains prohibited.
        self.solar_origin_kwh = 0.0
        self.initial_reserve_kwh = max(0.0, (self.soc-config.battery.min_soc)/100*config.battery.effective_kwh)
        self.session_id = str(uuid.uuid4())
        self.state = 'running'
        self.low = quantile(self.prices, config.low_price_fraction)
        # A percentile can equal the day minimum when PTF contains long flat
        # periods; never interpret *every* hour as an expensive hour.
        self.high = max(quantile(self.prices, config.high_price_fraction), min(self.prices) + .65 * (max(self.prices)-min(self.prices)))
        self.has_price_spread = max(self.prices)-min(self.prices) > 1e-6
        self.expected_high_price = max(self.prices)
        self.total = {k:0.0 for k in (
            'pv_kwh','load_kwh','charge_kwh','discharge_kwh','import_kwh','export_kwh',
            'curtailment_kwh','unserved_kwh','charge_conversion_loss_kwh',
            'discharge_conversion_loss_kwh','sales_tl','purchases_tl','degradation_tl',
            'baseline_sales_tl','baseline_purchases_tl','baseline_unserved_kwh')}
        self.last = None
        self.history: list[dict[str,Any]] = []

    def record(self):
        # Charge/discharge are AC-side energy flows. Explicitly expose conversion
        # losses and the change in stored battery energy so the site-wide balance
        # can be audited without treating battery losses as missing energy.
        charge_loss = self.total['charge_conversion_loss_kwh']
        discharge_loss = self.total['discharge_conversion_loss_kwh']
        battery_loss = charge_loss + discharge_loss
        stored_delta = self.config.battery.effective_kwh * (self.soc-self.initial_soc) / 100
        served_load = max(0.0, self.total['load_kwh']-self.total['unserved_kwh'])
        balance_residual = (
            self.total['pv_kwh'] + self.total['import_kwh']
            - served_load - self.total['export_kwh'] - self.total['curtailment_kwh']
            - battery_loss - stored_delta
        )
        battery_internal_residual = (
            self.total['charge_kwh'] - self.total['discharge_kwh']
            - battery_loss - stored_delta
        )
        totals = {k:round(v,3) for k,v in self.total.items()}
        totals.update({
            'battery_conversion_loss_kwh':round(battery_loss,3),
            'stored_energy_change_kwh':round(stored_delta,3),
            'served_load_kwh':round(served_load,3),
            'site_energy_balance_residual_kwh':round(balance_residual,6),
            'battery_energy_balance_residual_kwh':round(battery_internal_residual,6),
        })
        return {
            'state':self.state,'session_id':self.session_id,
            'date':self.config.date, 'cursor':self.cursor,'total_steps':self.total_steps,
            'speed':self.config.speed,'step_seconds':self.config.step_seconds,
            'installed_kwp':self.config.plant.installed_kwp,
            'latitude':self.config.plant.latitude,'longitude':self.config.plant.longitude,
            'soc_pct':round(self.soc,5), 'last_measurement':self.last,
            'totals':totals,
            'net_with_battery_tl':round(self.total['sales_tl']-self.total['purchases_tl']-self.total['degradation_tl'],2),
            'net_without_battery_tl':round(self.total['baseline_sales_tl']-self.total['baseline_purchases_tl'],2),
            'incremental_battery_tl':round(self.total['sales_tl']-self.total['purchases_tl']-self.total['degradation_tl']-self.total['baseline_sales_tl']+self.total['baseline_purchases_tl'],2),
            'terminal_soc_difference_pct':round(self.soc-self.initial_soc,4),
            'target_final_soc_pct': self.target_soc,
            'target_soc_gap_pp': round(self.soc-self.target_soc,4),
            'terminal_soc_met': self.state == 'completed' and abs(self.soc-self.target_soc) <= 0.05,
            'optimization_method':'ROLLING_HOURLY_MILP',
            'reoptimization_count':self.reoptimization_count,
            'plan_errors':self.plan_errors[-4:],
            'solar_origin_stored_kwh':round(self.solar_origin_kwh,4),
            'initial_reserve_stored_kwh':round(self.initial_reserve_kwh,4),
            'initial_reserve_discharge_enabled':self.config.battery.allow_initial_reserve_discharge,
            'charging_source':'GES_ONLY',
            'grid_to_battery_kwh':0.0,
            'source':'VIRTUAL_FIELD_WEATHER_FORECAST_AND_REAL_EPIAS_PTF',
            'economic_warning':'PTF referans değeridir; ticari fatura değildir. Hedef bitiş SOC sağlanmadıysa ekonomik fark adil günlük kazanç değildir. Karşılanamayan yükün kesinti maliyeti hesaba katılmamıştır.'
        }

    def _hour_pv_kw(self, h: int, frac: float):
        p=self.config.plant
        weather=self.weather['hours']
        here=weather[h]
        # Piecewise-linear forecast from the hourly irradiance. Deterministic and
        # repeatable, not random measured radiation or sensor values.
        nxt=weather[min(h+1,23)]
        ghi=here['irradiance_wm2']*(1-frac)+nxt['irradiance_wm2']*frac
        t0=here['temperature_c'] if here['temperature_c'] is not None else 25.0
        t1=nxt['temperature_c'] if nxt['temperature_c'] is not None else t0
        temp=t0*(1-frac)+t1*frac
        # Simplified GHI-based, temperature-adjusted PV output model. No claim of
        # POA or real inverter AC efficiency beyond user-provided PR.
        factor=max(0,1+p.temperature_coefficient*(temp-25))
        return min(p.installed_kwp,max(0,p.installed_kwp*ghi/1000*p.performance_ratio*factor))

    def _hour_load_kw(self, elapsed: float) -> float:
        p = self.config.plant
        return max(0., p.site_load_kw*(1+p.site_load_variation_pct/100*(
            .65*math.sin(2*math.pi*(elapsed/86400)+.3)
            +.35*math.sin(2*math.pi*(elapsed/21600)))))

    def _forecast_hour(self, hour: int, current_hour: int, actual_pv: float | None, actual_load: float | None):
        # Plan from the CURRENT observed virtual PV and load for current hour.
        # Future uses the same independently configured weather + load simulation.
        if hour == current_hour and actual_pv is not None:
            return actual_pv, actual_load
        # Five point integration approximates the next hour's generated weather profile.
        pv = sum(self._hour_pv_kw(hour, (i+.5)/6) for i in range(6))/6
        load = sum(self._hour_load_kw(hour*3600+(i+.5)*600) for i in range(6))/6
        return pv, load

    def _optimize_remaining(self, now_hour:int, actual_pv:float, actual_load:float):
        """Receding-horizon MILP, hourly energy values in kWh, hard device/energy
        constraints, GES-only charging, binary charge/discharge and import/export modes.
        Terminal target uses expensive deviation slack rather than inventing energy.
        """
        b=self.config.battery
        plant=self.config.plant
        n=24-now_hour
        # For each hour: charge, discharge, import, export, curtail, unmet,
        # battery-mode, grid-mode. State-of-energy at end of each hour + terminal slacks.
        stride=8
        size=n*stride+n+2
        def idx(t,k):return t*stride+k
        def stored(t):return n*stride+t
        plus=size-2;minus=size-1
        c=np.zeros(size)
        lo=np.zeros(size)
        hi=np.full(size,np.inf)
        integers=np.zeros(size)
        rows=[];lbs=[];ubs=[]
        cap=b.effective_kwh
        charge_cap=b.charge_limit()
        discharge_cap=b.discharge_limit()
        imp_cap=plant.import_limit_kw if plant.import_limit_kw is not None else 1e9
        exp_cap=plant.export_limit_kw
        eta_c=b.charge_efficiency;eta_d=b.discharge_efficiency
        def constraint(items,lower,upper):
            row=np.zeros(size)
            for key,value in items: row[key]+=value
            rows.append(row);lbs.append(lower);ubs.append(upper)
        for t in range(n):
            hour=now_hour+t
            pv,load=self._forecast_hour(hour,now_hour,actual_pv,actual_load)
            price=self.prices[hour]/1000
            surplus=max(0,pv-load)
            hi[idx(t,0)]=min(charge_cap,surplus) # PV-only charging, even if grid exists
            hi[idx(t,1)]=discharge_cap
            hi[idx(t,2)]=imp_cap
            hi[idx(t,3)]=exp_cap
            hi[idx(t,4)]=max(pv,0) + discharge_cap
            hi[idx(t,5)]=max(load,0)+charge_cap
            hi[idx(t,6)]=hi[idx(t,7)]=1
            integers[idx(t,6)]=integers[idx(t,7)]=1
            lo[stored(t)]=cap*(max(b.min_soc,self.initial_soc) if not b.allow_initial_reserve_discharge else b.min_soc)/100
            hi[stored(t)]=cap*b.max_soc/100
            c[idx(t,1)]=self.config.degradation_tl_per_kwh
            c[idx(t,2)]=price
            c[idx(t,3)]=-price
            c[idx(t,4)]=1e-5
            c[idx(t,5)]=max(100.0,price+100.0) # unserved load much worse than arbitrage
            # Charge only in charging mode. Discharge only in opposite mode.
            constraint([(idx(t,0),1),(idx(t,6),-hi[idx(t,0)])],-np.inf,0)
            constraint([(idx(t,1),1),(idx(t,6),hi[idx(t,1)])],-np.inf,hi[idx(t,1)])
            constraint([(idx(t,2),1),(idx(t,7),-imp_cap)],-np.inf,0)
            constraint([(idx(t,3),1),(idx(t,7),exp_cap)],-np.inf,exp_cap)
            # PV + discharge + import + unserved == load + charge + export + curtail
            constraint([(idx(t,0),-1),(idx(t,1),1),(idx(t,2),1),
                        (idx(t,3),-1),(idx(t,4),-1),(idx(t,5),1)],load-pv,load-pv)
            prior=[(stored(t-1),-1)] if t else []
            stored_initial=self.soc/100*cap if t==0 else 0
            constraint([(stored(t),1),(idx(t,0),-eta_c),(idx(t,1),1/eta_d)]+prior,stored_initial,stored_initial)
        target=cap*self.target_soc/100
        constraint([(stored(n-1),1),(plus,-1),(minus,1)],target,target)
        # Strong target penalty, still let motor deliver loads and respect SOC physics.
        terminal_penalty=max(100.0, max(self.prices)/1000*20)
        c[plus]=c[minus]=terminal_penalty
        res=milp(c=c,integrality=integers,bounds=Bounds(lo,hi),
                 constraints=LinearConstraint(np.stack(rows),np.array(lbs),np.array(ubs)),
                 options={'time_limit':5.0,'mip_rel_gap':0.002})
        if res.x is None or res.status not in (0,1):
            raise RuntimeError('Kalan gün MILP çözülemedi: '+str(res.message))
        x=res.x
        return {
            'charge_kw':max(0,float(x[idx(0,0)])),
            'discharge_kw':max(0,float(x[idx(0,1)])),
            'target_terminal_soc_pct':self.target_soc,
            'predicted_terminal_soc_pct':round(float(x[stored(n-1)])/cap*100,4),
            'solver_status':int(res.status),
            'hour':now_hour
        }

    def step(self):
        if self.state!='running' or self.cursor>=self.total_steps:
            return self.record()
        seconds=self.config.step_seconds
        elapsed=self.cursor*seconds
        hour=elapsed//3600
        frac=(elapsed%3600)/3600
        dt=seconds/3600
        p=self.config.plant;b=self.config.battery
        pv=self._hour_pv_kw(hour,frac)
        load=self._hour_load_kw(elapsed)
        price=self.prices[hour]
        if self.hour_plan_index!=hour:
            try:
                self.hour_plan=self._optimize_remaining(hour,pv,load)
                self.hour_plan_index=hour
                self.reoptimization_count+=1
            except Exception as exc:
                # Never silently continue with a stale schedule.
                self.state='error'
                self.plan_errors.append(f'{hour:02d}:00 MILP: {str(exc)[:180]}')
                return self.record()
        plan=self.hour_plan
        surplus=max(0,pv-load)
        deficit=max(0,load-pv)
        cap=b.effective_kwh
        max_charge_by_soc=max(0,(b.max_soc/100*cap-self.soc/100*cap)/(b.charge_efficiency*dt))
        max_discharge_by_soc=max(0,(self.soc/100*cap-b.min_soc/100*cap)*b.discharge_efficiency/dt)
        usable=self.solar_origin_kwh+(self.initial_reserve_kwh if b.allow_initial_reserve_discharge else 0.0)
        max_discharge_by_origin=usable*b.discharge_efficiency/dt
        charge=min(plan['charge_kw'],surplus,b.charge_limit(),max_charge_by_soc)
        discharge=0.
        if charge<=1e-8:
            discharge=min(plan['discharge_kw'],b.discharge_limit(),max_discharge_by_soc,max_discharge_by_origin)
        # Contract-load safety override; reserve is dispatched only if configured.
        if charge<=1e-8 and p.import_limit_kw is not None and deficit>p.import_limit_kw:
            shortage=deficit-p.import_limit_kw
            discharge=max(discharge,min(shortage,b.discharge_limit(),max_discharge_by_soc,max_discharge_by_origin))
        # No synthetic energy: track GES stored charge and unknown-origin initial reserve separately.
        spent=discharge/b.discharge_efficiency*dt
        solar_spent=min(self.solar_origin_kwh,spent)
        reserve_spent=max(0,spent-solar_spent)
        self.solar_origin_kwh=max(0,self.solar_origin_kwh+charge*b.charge_efficiency*dt-solar_spent)
        self.initial_reserve_kwh=max(0,self.initial_reserve_kwh-reserve_spent)
        self.soc=min(b.max_soc,max(b.min_soc,self.soc+100*(charge*b.charge_efficiency-discharge/b.discharge_efficiency)*dt/cap))
        net=pv+discharge-load-charge
        export=min(max(0,net),p.export_limit_kw)
        imports=min(max(0,-net),p.import_limit_kw if p.import_limit_kw is not None else math.inf)
        curtailed=max(0,net-export)
        unmet=max(0,-net-imports)
        baseline_net=pv-load
        baseline_export=min(max(0,baseline_net),p.export_limit_kw)
        baseline_import=min(max(0,-baseline_net),p.import_limit_kw if p.import_limit_kw is not None else math.inf)
        self.total['baseline_unserved_kwh']+=max(0,-baseline_net-baseline_import)*dt
        for key,val in [('pv_kwh',pv),('load_kwh',load),('charge_kwh',charge),
                        ('discharge_kwh',discharge),('import_kwh',imports),('export_kwh',export),
                        ('curtailment_kwh',curtailed),('unserved_kwh',unmet)]:
            self.total[key]+=val*dt
        # AC-side energy entering/leaving the battery. The difference is split
        # into charge-side and discharge-side conversion losses.
        self.total['charge_conversion_loss_kwh'] += charge*(1-b.charge_efficiency)*dt
        self.total['discharge_conversion_loss_kwh'] += discharge*(1/b.discharge_efficiency-1)*dt
        self.total['sales_tl']+=export*dt*price/1000
        self.total['purchases_tl']+=imports*dt*price/1000
        self.total['degradation_tl']+=discharge*dt*self.config.degradation_tl_per_kwh
        self.total['baseline_sales_tl']+=baseline_export*dt*price/1000
        self.total['baseline_purchases_tl']+=baseline_import*dt*price/1000
        if charge>1e-8: reason='MILP: GES fazlası ile şarj; kalan günün PTF fiyatları ve bitiş SOC hedefi hesaba katıldı.'
        elif discharge>1e-8: reason='MILP: Tesis yükü / şebeke satışı için deşarj; kalan saatler ve bitiş SOC hedefi değerlendirildi.'
        else: reason='MILP: Mevcut SOC, enerji dengesi, fiyatlar ve gün sonu hedefi için bekleme.'
        w=self.weather['hours'][hour]
        self.last={
          'simulated_at':(self.start+timedelta(seconds=elapsed)).isoformat(),
          'pv_kw':round(pv,3),'load_kw':round(load,3),
          'charge_kw':round(charge,3),'discharge_kw':round(discharge,3),
          'grid_export_kw':round(export,3),'grid_import_kw':round(imports,3),
          'curtailment_kw':round(curtailed,3),'unserved_load_kw':round(unmet,3),
          'soc_pct':round(self.soc,4),'ptf_tl_mwh':price,
          'solar_origin_stored_kwh':round(self.solar_origin_kwh,4),
          'initial_reserve_stored_kwh':round(self.initial_reserve_kwh,4),
          'discharge_from_initial_reserve_kwh':round(reserve_spent,4),
          'charging_source':'GES' if charge>0 else 'NONE',
          'grid_to_battery_kw':0.,
          'discharge_to_site_kw':round(min(discharge,deficit),3),
          'discharge_to_grid_kw':round(max(0,discharge-min(discharge,deficit)),3),
          'irradiance_wm2':w['irradiance_wm2'],
          'temperature_c':w['temperature_c'],'cloud_cover_pct':w['cloud_cover_pct'],
          'conditions':w['conditions'],
          'action':'CHARGE' if charge>1e-8 else 'DISCHARGE' if discharge>1e-8 else 'IDLE',
          'reason':reason,'source':'VIRTUAL_FIELD_NOT_PHYSICAL_MEASUREMENTS',
          'target_final_soc_pct':self.target_soc,
          'reoptimized_at_hour':hour,
          'expected_terminal_soc_pct':plan['predicted_terminal_soc_pct']
        }
        self.cursor+=1
        if self.cursor>=self.total_steps:self.state='completed'
        self.history.append(self.last)
        return self.record()


_engine:Engine | None=None
_task:asyncio.Task | None=None
_clients:set[WebSocket]=set()
_lock=asyncio.Lock()


async def broadcast(data:dict[str,Any]):
    for ws in list(_clients):
        try:await ws.send_json(data)
        except Exception:_clients.discard(ws)


async def run_engine(engine:Engine):
    try:
        while _engine is engine and engine.state in ('running','paused'):
            if engine.state=='paused':
                await asyncio.sleep(0.15)
                continue
            engine.step()
            await broadcast(engine.record())
            if engine.state=='completed':break
            await asyncio.sleep(max(0.01,engine.config.step_seconds/engine.config.speed))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        engine.state='error'
        engine.last={'reason':f'Simülasyon hatası: {type(exc).__name__}'}
        await broadcast(engine.record())


@router.post('/preview')
async def preview(req:StartRequest):
    # Validate both externally sourced data and the physical model before previewing.
    w=await asyncio.to_thread(fetch_weather,req.plant.latitude,req.plant.longitude,req.date,req.visual_crossing_key)
    engine=Engine(req,w)
    hourly=[]
    for i in range(engine.total_steps):
        snapshot=engine.step()
        if (i+1)*req.step_seconds % 3600 == 0:
            hourly.append({**(engine.last or {}), 'hour':((i+1)*req.step_seconds//3600)-1,
                           'cumulative_incremental_tl':snapshot['incremental_battery_tl']})
    return {'weather':w,'hours':hourly,'summary':engine.record(),
            'note':'Önizleme, aynı günün aynı hava ve PTF verileriyle oluşturulan sanal tahmindir. Gerçek sayaç kaydı değildir.'}


@router.post('/weather')
async def weather(req:WeatherRequest):
    return await asyncio.to_thread(fetch_weather,req.latitude,req.longitude,req.date,req.api_key)


@router.post('/start')
async def start(req:StartRequest):
    global _engine,_task
    async with _lock:
        if _engine and _engine.state in ('running','paused'):
            raise HTTPException(409,'Sanal saha çalışıyor; önce durdurun')
        prices_for_day(req.price_schedule,req.date)
        w=await asyncio.to_thread(fetch_weather,req.plant.latitude,req.plant.longitude,req.date,req.visual_crossing_key)
        _engine=Engine(req,w)
        _task=asyncio.create_task(run_engine(_engine))
        return {'status':_engine.record(),'weather':w}


@router.get('/status')
def status():
    return _engine.record() if _engine else {'state':'not_started','last_measurement':None,'totals':None}


@router.get('/report.json')
def report_json(session_id: str):
    from api.digital_twin_report import build_report
    if _engine is None or _engine.session_id != session_id:
        raise HTTPException(404, 'Oturum bulunamadı; sunucu yeniden başlamış olabilir.')
    if _engine.state != 'completed':
        raise HTTPException(409, 'PDF/JSON raporu ancak 24 saat tamamlandığında oluşturulabilir.')
    return build_report(_engine)


@router.get('/report.pdf')
def report_pdf(session_id: str):
    from api.digital_twin_report import build_report, create_pdf
    if _engine is None or _engine.session_id != session_id:
        raise HTTPException(404, 'Oturum bulunamadı; sunucu yeniden başlamış olabilir.')
    if _engine.state != 'completed':
        raise HTTPException(409, 'PDF/JSON raporu ancak 24 saat tamamlandığında oluşturulabilir.')
    try:
        content = create_pdf(build_report(_engine))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(content, media_type='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="scadawatt_saha_raporu_{_engine.config.date}_{_engine.session_id[:8]}.pdf"',
        'Cache-Control':'no-store'
    })


@router.get('/history')
def history(limit:int=500):
    if not _engine:return {'records':[]}
    return {'records':_engine.history[-max(1,min(limit,5000)):]}


@router.post('/{action}')
async def action(action:str):
    if action not in ('pause','resume','stop'):
        raise HTTPException(404,'Bilinmeyen komut')
    if not _engine:raise HTTPException(409,'Simülatör başlamadı')
    if action=='pause' and _engine.state=='running':_engine.state='paused'
    elif action=='resume' and _engine.state=='paused':_engine.state='running'
    elif action=='stop' and _engine.state in ('running','paused'):_engine.state='stopped'
    else:raise HTTPException(409,f'{_engine.state} durumunda {action} uygulanamaz')
    await broadcast(_engine.record())
    return _engine.record()


@router.websocket('/ws')
async def ws(websocket:WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    try:
        await websocket.send_json(status())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)
