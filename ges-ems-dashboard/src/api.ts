import type { OptimizeResponse, Scenario, Session, Status, Measurement } from './types'
import type { Plant } from './ems'
import {scenarioPayload} from './ems'

const ROOT = '/api'
export class ApiError extends Error {constructor(message:string,public status:number){super(message)}}
async function json<T>(path:string,init?:RequestInit):Promise<T>{
  let r:Response
  try {r=await fetch(ROOT+path,{...init,headers:{'Content-Type':'application/json',...(init?.headers||{})}})}
  catch {throw new ApiError('FastAPI erişilemiyor. 127.0.0.1:8000 bağlantısını kontrol edin.',0)}
  let body:any
  try {body=await r.json()}catch{body=null}
  if(!r.ok){const detail=body?.detail;throw new ApiError(typeof detail==='string'?detail:detail?JSON.stringify(detail):`HTTP ${r.status}`,r.status)}
  return body as T
}
const send=<T,>(path:string,method:string,data?:unknown)=>json<T>(path,{method,...(data!==undefined?{body:JSON.stringify(data)}:{})})
export type EpiasAuth = {configured:boolean; token_present:boolean; message:string}
export const backend={
  status:()=>json<Status>('/simulation/status'),
  sessions:()=>json<{sessions:Session[];persistence_enabled:boolean}>('/simulation/sessions'),
  history:(id:string,limit=500)=>json<{records:Measurement[]}>(`/simulation/history?session_id=${encodeURIComponent(id)}&limit=${limit}`),
  optimize:(scenario:Scenario,plant:Plant)=>send<OptimizeResponse>('/ems/optimize','POST',scenarioPayload(scenario,plant)),
  epiasStatus:()=>json<EpiasAuth>('/ems/epias/status'),
  epiasLogin:(username:string,password:string)=>send<EpiasAuth>('/ems/epias/login','POST',{username,password}),
  epiasLogout:()=>send<EpiasAuth>('/ems/epias/logout','POST'),
  start:(optimization:OptimizeResponse['optimization'],scenario:Scenario,speed:number,step_seconds=60)=>send<Status>('/simulation/start','POST',{optimization,battery:scenario.battery,speed,step_seconds}),
  action:(action:'pause'|'resume'|'stop')=>send<Status>(`/simulation/${action}`,'POST'),
  speed:(speed:number)=>send<Status>('/simulation/speed','PATCH',{speed}),
}
export function websocketUrl(){const explicit=import.meta.env.VITE_WS_URL?.trim();if(explicit)return explicit;const p=location.protocol==='https:'?'wss':'ws';return `${p}://${import.meta.env.DEV?`${location.hostname}:8000`:location.host}/simulation/ws`}
