import { useEffect, useState } from 'react'
import { BatteryCharging, CloudSun, Play, Pause, Square, Radio, AlertTriangle, RefreshCw } from 'lucide-react'
import type { OptimizeResponse, Scenario } from './types'
import type { Plant } from './ems'
import { backend } from './api'

type WeatherHour={datetime:string;irradiance_wm2:number;temperature_c:number|null;cloud_cover_pct:number|null;conditions:string;icon:string}
type WeatherResponse={date:string;timezone:string;latitude:number;longitude:number;hours:WeatherHour[];source:string}
type RecordValue={simulated_at:string;pv_kw:number;load_kw:number;charge_kw:number;discharge_kw:number;grid_export_kw:number;grid_import_kw:number;curtailment_kw:number;soc_pct:number;ptf_tl_mwh:number;irradiance_wm2:number;temperature_c:number|null;cloud_cover_pct:number|null;conditions:string;action:string;reason:string}
type State={state:string;session_id?:string;date?:string;cursor?:number;total_steps?:number;speed?:number;installed_kwp?:number;latitude?:number;longitude?:number;soc_pct?:number;last_measurement:RecordValue|null;totals:Record<string,number>|null;net_with_battery_tl?:number;net_without_battery_tl?:number;incremental_battery_tl?:number;terminal_soc_difference_pct?:number;economic_warning?:string}
const fmt=(n?:number|null,d=1)=>typeof n==='number'&&Number.isFinite(n)?n.toLocaleString('tr-TR',{maximumFractionDigits:d,minimumFractionDigits:d}):'—'
const money=(n?:number)=>`${fmt(n,2)} TL`
const json=async<T,>(path:string,data?:unknown):Promise<T>=>{
  const r=await fetch('/api/digital-twin'+path,{method:data===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)})
  const result=await r.json().catch(()=>null)
  if(!r.ok)throw Error(typeof result?.detail==='string'?result.detail:JSON.stringify(result?.detail||`HTTP ${r.status}`))
  return result as T
}
function Field({label,value,onChange,step=1,min,unit}:{label:string;value:number;onChange:(v:number)=>void;step?:number;min?:number;unit?:string}){
  return <label className="twin-field"><span>{label}</span><div><input type="number" value={Number.isFinite(value)?value:''} step={step} min={min} onChange={e=>onChange(Number(e.target.value))}/>{unit&&<small>{unit}</small>}</div></label>
}
function Metric({label,value,description,accent}:{label:string;value:string;description?:string;accent?:boolean}){
 return <div className={'twin-metric'+(accent?' twin-accent':'')}><span>{label}</span><strong>{value}</strong>{description&&<small>{description}</small>}</div>
}
function WeatherTimeline({hours}:{hours:WeatherHour[]}){
  const max=Math.max(1,...hours.map(h=>h.irradiance_wm2))
  return <div className="twin-weather-hours">{hours.map((h,i)=><div className="twin-weather-hour" key={i} tabIndex={0} title={`${String(i).padStart(2,'0')}:00 | Işınım ${fmt(h.irradiance_wm2,0)} W/m² | ${h.conditions} | ${fmt(h.temperature_c)}°C | Bulut ${fmt(h.cloud_cover_pct,0)}%`}><div className="twin-weather-bar"><i style={{height:`${Math.max(2,h.irradiance_wm2/max*100)}%`}}/></div><b>{String(i).padStart(2,'0')}</b><div className="twin-tooltip"><strong>{String(i).padStart(2,'0')}:00 — {h.conditions}</strong><span>Işınım: {fmt(h.irradiance_wm2,0)} W/m²</span><span>Sıcaklık: {fmt(h.temperature_c)} °C</span><span>Bulut: %{fmt(h.cloud_cover_pct,0)}</span></div></div>)}</div>
}
export default function DigitalTwin({plan,scenario,plant}:{plan:OptimizeResponse|null;scenario:Scenario;plant:Plant}){
 const [date,setDate]=useState(scenario.start_date)
 const [fetchedPlan,setFetchedPlan]=useState<OptimizeResponse|null>(null)
 const [latitude,setLatitude]=useState(37.95)
 const [longitude,setLongitude]=useState(34.68)
 const [vcKey,setVcKey]=useState('')
 const [panelCount,setPanelCount]=useState(plant.panelCount)
 const [panelPower,setPanelPower]=useState(plant.panelPowerWp)
 const [siteLoad,setSiteLoad]=useState(scenario.site_load_kw)
 const [loadVariation,setLoadVariation]=useState(12)
 const [exportLimit,setExportLimit]=useState(scenario.grid_export_limit_kw)
 const [capacity,setCapacity]=useState(scenario.battery.capacity_kwh)
 const [soc,setSoc]=useState(scenario.battery.soc)
 const [minSoc,setMinSoc]=useState(scenario.battery.min_soc)
 const [maxSoc,setMaxSoc]=useState(scenario.battery.max_soc)
 const [batteryPower,setBatteryPower]=useState(scenario.battery.max_charge_kw)
 const [bmsLimit,setBmsLimit]=useState(scenario.battery.max_charge_kw)
 const [pcsLimit,setPcsLimit]=useState(scenario.battery.max_charge_kw)
 const [bmsChargeAllowed,setBmsChargeAllowed]=useState(true)
 const [bmsDischargeAllowed,setBmsDischargeAllowed]=useState(true)
 const [pcsOnline,setPcsOnline]=useState(true)
 const [cRate,setCRate]=useState(scenario.battery.max_charge_c)
 const [speed,setSpeed]=useState(3600)
 const [weather,setWeather]=useState<WeatherResponse|null>(null)
 const [state,setState]=useState<State|null>(null)
 const [history,setHistory]=useState<RecordValue[]>([])
 const [pending,setPending]=useState(false)
 const [error,setError]=useState('')
 const [connected,setConnected]=useState(false)
 const installed=panelCount*panelPower/1000
 const active=state?.state==='running'||state?.state==='paused'
 const current=state?.last_measurement
 const pricePlan=(fetchedPlan||plan)?.optimization.schedule||[]
 const matchingPrices=pricePlan.filter(h=>h.datetime.slice(0,10)===date)
 const hasPrices=matchingPrices.length===24
 useEffect(()=>{setPanelCount(plant.panelCount);setPanelPower(plant.panelPowerWp)},[plant.panelCount,plant.panelPowerWp])
 useEffect(()=>{let cancelled=false;const poll=()=>json<State>('/status').then(s=>{if(!cancelled)setState(s)}).catch(()=>{})
   poll();const id=window.setInterval(poll,3500);return()=>{cancelled=true;clearInterval(id)}},[])
 useEffect(()=>{let closed=false;let ws:WebSocket|null=null;let timer:number|undefined
   const open=()=>{if(closed)return;ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${import.meta.env.DEV?`${location.hostname}:8000`:location.host}/digital-twin/ws`);ws.onopen=()=>setConnected(true);ws.onmessage=e=>{try{const s=JSON.parse(e.data) as State;setState(s);if(s.last_measurement)setHistory(h=>h.length>2000?[...h.slice(-1500),s.last_measurement!]:[...h,s.last_measurement!])}catch{}};ws.onclose=()=>{setConnected(false);if(!closed)timer=window.setTimeout(open,2000)};ws.onerror=()=>ws?.close()}
   open();return()=>{closed=true;if(timer)clearTimeout(timer);ws?.close()}},[])
 const execute=async<T,>(fn:()=>Promise<T>)=>{setPending(true);setError('');try{return await fn()}catch(e){setError(e instanceof Error?e.message:String(e));return null}finally{setPending(false)}}
 const loadPrices=async()=>{const result=await execute(()=>backend.optimize({...scenario,start_date:date,end_date:date,site_load_kw:siteLoad,grid_export_limit_kw:exportLimit},{panelCount,panelPowerWp:panelPower}));if(result){const rows=result.optimization?.schedule||[];if(rows.filter(h=>h.datetime.slice(0,10)===date).length!==24){setError('Bu tarihte 24 adet kesinleşmiş EPİAŞ fiyatı yok.');return}setFetchedPlan(result)}}
 const loadWeather=()=>execute(()=>json<WeatherResponse>('/weather',{latitude,longitude,date,api_key:vcKey})).then(w=>{if(w)setWeather(w)})
 const start=async()=>{
   if(!hasPrices){setError('Önce Optimizasyon bölümünde seçilen gün için gerçek EPİAŞ PTF planı oluşturun. Aynı günün 24 fiyatı olmadan başlatılmaz.');return}
   if(!vcKey.trim()){setError('Visual Crossing API anahtarını girin.');return}
   const result=await execute(()=>json<{status:State;weather:WeatherResponse}>('/start',{
     date,visual_crossing_key:vcKey,price_schedule:matchingPrices,speed,step_seconds:60,
     plant:{panel_count:panelCount,panel_power_wp:panelPower,latitude,longitude,site_load_kw:siteLoad,site_load_variation_pct:loadVariation,export_limit_kw:exportLimit,performance_ratio:.85,temperature_coefficient:-.0035,solar_only_charging:true},
     battery:{capacity_kwh:capacity,soc,soh:scenario.battery.soh,min_soc:minSoc,max_soc:maxSoc,max_charge_kw:batteryPower,max_discharge_kw:batteryPower,max_charge_c:cRate,max_discharge_c:cRate,charge_efficiency:scenario.battery.charge_efficiency,discharge_efficiency:scenario.battery.discharge_efficiency,
     bms_charge_allowed:bmsChargeAllowed,bms_discharge_allowed:bmsDischargeAllowed,pcs_online:pcsOnline,bms_charge_limit_kw:bmsLimit, bms_discharge_limit_kw:bmsLimit,pcs_limit_kw:pcsLimit}
   }))
   if(result){setState(result.status);setWeather(result.weather);setHistory([])}
 }
 const command=(action:'pause'|'resume'|'stop')=>execute(()=>json<State>(`/${action}`,{})).then(s=>{if(s)setState(s)})
 const progress=state?.cursor&&state.total_steps?Math.round(state.cursor/state.total_steps*100):0
 return <div className="twin-page">
   <div className="twin-header"><div><span className="tiny-caps">SANAL SAHA / GERİ BESLEMELİ ENERJİ YÖNETİMİ</span><h1>Saha simülatörü</h1><p>Hava tahmininden sanal üretim hesaplanır. EMS, anlık üretim, yük, SOC ve EPİAŞ PTF değerlerine göre şarj/deşarj ve şebeke kararları verir.</p></div><span className={'twin-live '+(connected?'on':'')}><Radio size={15}/>{connected?'CANLI BAĞLANTI':'BAĞLANTI BEKLENİYOR'}</span></div>
   <div className="twin-notice"><AlertTriangle size={17}/> <span>Fiziksel saha ölçümü değil: Visual Crossing saatlik hava verisine dayalı deterministik sanal saha. Kontrolcü anlık karar verir; bu sürümde MILP her dakika yeniden çözülmez.</span></div>
   <div className="twin-setup">
     <div className="twin-section"><h2>01 / Saha ve konum</h2><div className="twin-fields"><Field label="Enlem" value={latitude} step={.0001} onChange={setLatitude}/><Field label="Boylam" value={longitude} step={.0001} onChange={setLongitude}/><Field label="Panel adedi" value={panelCount} min={1} onChange={setPanelCount}/><Field label="Panel gücü" value={panelPower} unit="Wp" onChange={setPanelPower}/><Field label="Tesis ortalama yükü" value={siteLoad} unit="kW" onChange={setSiteLoad}/><Field label="Yük değişkenliği" value={loadVariation} unit="%" onChange={setLoadVariation}/><Field label="Şebeke satış sınırı" value={exportLimit} unit="kW" onChange={setExportLimit}/></div><div className="twin-calculated">Hesaplanan GES kurulu gücü <strong>{fmt(installed,2)} kWp</strong><small>Panel sayısı × panel gücü; config.py değiştirilmez.</small></div></div>
     <div className="twin-section"><h2>02 / Batarya ve hava servisi</h2><div className="twin-fields"><Field label="Batarya kapasitesi" value={capacity} unit="kWh" onChange={setCapacity}/><Field label="Başlangıç SOC" value={soc} unit="%" onChange={setSoc}/><Field label="Minimum SOC" value={minSoc} unit="%" onChange={setMinSoc}/><Field label="Maksimum SOC" value={maxSoc} unit="%" onChange={setMaxSoc}/><Field label="Maks. şarj / deşarj" value={batteryPower} unit="kW" onChange={setBatteryPower}/><Field label="C-rate" value={cRate} step={.1} onChange={setCRate}/><Field label="BMS güç sınırı" value={bmsLimit} unit="kW" onChange={setBmsLimit}/><Field label="PCS güç sınırı" value={pcsLimit} unit="kW" onChange={setPcsLimit}/></div><div className="twin-toggles"><label><input type="checkbox" checked={bmsChargeAllowed} onChange={e=>setBmsChargeAllowed(e.target.checked)}/> BMS şarj izni</label><label><input type="checkbox" checked={bmsDischargeAllowed} onChange={e=>setBmsDischargeAllowed(e.target.checked)}/> BMS deşarj izni</label><label><input type="checkbox" checked={pcsOnline} onChange={e=>setPcsOnline(e.target.checked)}/> PCS çevrimiçi</label></div><label className="twin-secret"><span>Visual Crossing API anahtarı</span><input type="password" value={vcKey} onChange={e=>setVcKey(e.target.value)} placeholder="Hava verisi için API key" autoComplete="off"/></label><small className="twin-privacy">Anahtar yalnızca istekte kullanılır. Tarayıcı depolamasına veya veritabanına kaydedilmez.</small><button type="button" className="twin-outline" onClick={loadWeather} disabled={pending||active||!vcKey}><CloudSun size={17}/> Hava durumunu getir</button></div>
   </div>
   <section className="twin-section twin-wide"><div className="twin-section-title"><h2>03 / Hava durumu ve üretim koşulları</h2><span>{weather?`${weather.date} · ${weather.timezone}`:'Henüz veri istenmedi'}</span></div>{weather?<><div className="twin-weather-summary"><Metric label="Öğle ışınımı" value={`${fmt(weather.hours[12]?.irradiance_wm2,0)} W/m²`}/><Metric label="Öğle sıcaklığı" value={`${fmt(weather.hours[12]?.temperature_c)} °C`}/><Metric label="Öğle bulutluluk" value={`${fmt(weather.hours[12]?.cloud_cover_pct,0)} %`}/></div><WeatherTimeline hours={weather.hours}/><p className="twin-small">Saat çubuklarının üzerine gelerek ışınım, bulut ve sıcaklık bilgilerini incele.</p></>:<p className="twin-empty">Enlem, boylam ve Visual Crossing anahtarını girerek seçilen güne ait gerçek servis verilerini getir.</p>}</section>
   <section className="twin-section twin-wide"><div className="twin-section-title"><h2>04 / Simülasyon kontrolü</h2><span>Seçilen gün: {date} · PTF: {hasPrices?'24 saat hazır':'Plan yok'}</span></div><div className="twin-date-line"><label>Simülasyon günü <input type="date" value={date} disabled={active} onChange={e=>{setDate(e.target.value);setFetchedPlan(null);setWeather(null)}}/></label><button className="twin-outline" disabled={pending||active} onClick={loadPrices}><RefreshCw size={16}/> EPİAŞ fiyatlarını hazırla</button></div><p className="twin-small">EPİAŞ fiyatları önceki optimizasyon sonucundan veya 'EPİAŞ fiyatlarını hazırla' komutundan alınır. Komut mevcut /ems/optimize servisiyle fiyat planı oluşturur; sanal GES üretimini ise yalnızca burada seçtiğiniz konum ve panel bilgilerinden hesaplarız.</p><div className="twin-actions"><label>Hız <select value={speed} disabled={active} onChange={e=>setSpeed(Number(e.target.value))}>{[60,360,3600,7200].map(v=><option value={v} key={v}>×{v}</option>)}</select></label><button onClick={start} disabled={pending||active||!hasPrices}><Play size={17}/> Simülasyonu başlat</button><button className="twin-outline" onClick={()=>command(state?.state==='paused'?'resume':'pause')} disabled={pending||!active}>{state?.state==='paused'?<Play size={16}/>:<Pause size={16}/>} {state?.state==='paused'?'Devam et':'Duraklat'}</button><button className="twin-outline" onClick={()=>command('stop')} disabled={pending||!active}><Square size={16}/> Durdur</button></div>{!hasPrices&&<p className="twin-warning">EPİAŞ fiyat planı eksik. Önce Optimizasyon bölümünde aynı gün için hesaplama yap.</p>}{error&&<div className="twin-error" role="alert">{error}</div>}<div className="twin-progress"><div style={{width:`${progress}%`}}/></div><div className="twin-status"><span>Durum: <strong>{state?.state||'bekleniyor'}</strong></span><span>İlerleme: <strong>%{progress}</strong></span><span>Simülasyon saati: <strong>{current?.simulated_at?new Date(current.simulated_at).toLocaleTimeString('tr-TR',{timeZone:'Europe/Istanbul'}):'—'}</strong></span></div></section>
   <section className="twin-section twin-wide"><div className="twin-section-title"><h2>05 / Canlı saha ve batarya kararları</h2><span>{current?'Sanal veri · WebSocket':'Ölçüm bekleniyor'}</span></div><div className="twin-metrics"><Metric label="GES üretimi" value={`${fmt(current?.pv_kw)} kW`} description="Hava koşullarından hesaplanan"/><Metric label="Tesis yükü" value={`${fmt(current?.load_kw)} kW`}/><Metric label="Batarya SOC" value={`${fmt(current?.soc_pct)} %`} accent/><Metric label="Şebekeye satış" value={`${fmt(current?.grid_export_kw)} kW`}/><Metric label="Şebekeden alış" value={`${fmt(current?.grid_import_kw)} kW`}/><Metric label="PTF" value={`${fmt(current?.ptf_tl_mwh,0)} TL/MWh`}/></div><div className="twin-decision"><BatteryCharging size={23}/><div><b>{current?.action==='CHARGE'?'BATARYA ŞARJ EDİYOR':current?.action==='DISCHARGE'?'BATARYA DEŞARJ EDİYOR':'BATARYA BEKLİYOR'}</b><p>{current?.reason||'Simülasyon başladığında EMS kararı burada açıklanacak.'}</p><span>Şarj {fmt(current?.charge_kw)} kW · Deşarj {fmt(current?.discharge_kw)} kW</span></div></div></section>
   <section className="twin-section twin-wide"><div className="twin-section-title"><h2>06 / Ekonomik gerçekleşme</h2><span>EPİAŞ PTF ile teorik karşılaştırma</span></div><div className="twin-metrics twin-economy"><Metric label="Bataryalı net değer" value={money(state?.net_with_battery_tl)}/><Metric label="Bataryasız net değer" value={money(state?.net_without_battery_tl)}/><Metric label="Batarya farkı" value={money(state?.incremental_battery_tl)} accent/><Metric label="Satış geliri" value={money(state?.totals?.sales_tl)}/><Metric label="Alış maliyeti" value={money(state?.totals?.purchases_tl)}/><Metric label="Batarya yıpranması" value={money(state?.totals?.degradation_tl)}/></div><p className="twin-small">Başlangıç ve bitiş SOC farklıysa görülen ek değer kesin tasarruf değildir. Bataryanın kalan enerji değeri ayrıca değerlendirilmelidir. KDV, vergiler ve sözleşme mahsuplaşmaları dahil değildir.</p><div className="twin-totals"><span>Üretim: <b>{fmt(state?.totals?.pv_kwh)} kWh</b></span><span>Şarj: <b>{fmt(state?.totals?.charge_kwh)} kWh</b></span><span>Deşarj: <b>{fmt(state?.totals?.discharge_kwh)} kWh</b></span><span>Üretim kısıntısı: <b>{fmt(state?.totals?.curtailment_kwh)} kWh</b></span></div></section>
 </div>
}
