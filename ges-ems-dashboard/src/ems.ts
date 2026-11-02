import type { Scenario } from './types'

// A price is never estimated: the UI only declares what EPİAŞ can have published.
export function turkeyClock() {
  const parts = new Intl.DateTimeFormat('en-GB', {timeZone:'Europe/Istanbul', year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',hourCycle:'h23'}).formatToParts(new Date())
  const get=(key:string)=>parts.find(p=>p.type===key)?.value||''
  return { today:`${get('year')}-${get('month')}-${get('day')}`, hour:Number(get('hour')) }
}
export function nextDay(day:string) {
  const d=new Date(day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+1);return d.toISOString().slice(0,10)
}
export function priceNotice(day:string) {
  const c=turkeyClock(), tomorrow=nextDay(c.today)
  if(day>tomorrow) return {level:'blocked' as const, text:'Bu tarih için EPİAŞ kesinleşmiş PTF yayımlamaz. Tahmin fiyatı üretmiyoruz.'}
  if(day===tomorrow && c.hour<14) return {level:'blocked' as const, text:'Yarının nihai PTF fiyatları genellikle Türkiye saatiyle 14.00’te açıklanır. Yayımlanmadan gerçek fiyatla optimizasyon yapılamaz.'}
  if(day===tomorrow) return {level:'ready' as const, text:'Yarının PTF fiyatlarının yayımlanmış olması beklenir; hesaplama sırasında EPİAŞ verisinin gerçekten mevcut olduğu doğrulanır.'}
  if(day<c.today) return {level:'history' as const, text:'Geçmiş PTF verisi istenebilir. Fakat hava tahmini servisi geçmişe dönük veri sağlamıyorsa geçmiş simülasyon hesaplanamaz.'}
  return {level:'ready' as const, text:'Bugünün kesinleşmiş PTF verileri ve GES üretim tahminiyle simülasyon hazırlanır.'}
}
export type Plant = { panelCount:number; panelPowerWp:number }
export function plantPower(p:Plant) {return p.panelCount*p.panelPowerWp/1000}
export function scenarioPayload(s:Scenario,p:Plant) {return {...s,pv_panel_count:p.panelCount,pv_panel_power_wp:p.panelPowerWp,installed_capacity_kwp:plantPower(p)}}
