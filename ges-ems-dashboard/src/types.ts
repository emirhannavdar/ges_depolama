export type DeviceLimits = {
  bms_charge_allowed: boolean
  bms_discharge_allowed: boolean
  bms_max_charge_kw: number | null
  bms_max_discharge_kw: number | null
  pcs_online: boolean
  pcs_max_charge_kw: number | null
  pcs_max_discharge_kw: number | null
}
export type Battery = {
  capacity_kwh: number; soc: number; soh: number; min_soc: number; max_soc: number
  max_charge_kw: number; max_discharge_kw: number; charge_efficiency: number; discharge_efficiency: number
  max_charge_c: number; max_discharge_c: number
}
export type Measurement = {
  simulated_at: string; source?: string; ptf_tl_mwh?: number
  pv_kw: number; load_kw: number; charge_kw: number; discharge_kw: number
  grid_import_kw: number; grid_export_kw: number; curtailment_kw: number
  unserved_load_kw: number; soc_pct: number; device_limits?: DeviceLimits
}
export type Totals = {
  pv_generation_kwh: number; site_load_kwh: number; battery_charge_kwh: number
  battery_discharge_kwh: number; grid_import_kwh: number; grid_export_kwh: number
  curtailment_kwh: number; unserved_load_kwh: number
}
export type Status = {
  state: string; error: string | null; session_id: string | null; persistence_enabled: boolean
  speed: number; step_seconds: number; revision: number; simulated_at: string | null; end_at: string | null
  soc_pct: number | null; last_measurement: Measurement | null
  totals: Totals | null; retained_records: number; source: string
}
export type Hour = {
  datetime: string; action: 'CHARGE' | 'DISCHARGE' | 'IDLE'; price_tl_mwh: number
  import_price_tl_mwh?: number; generation_kwh: number; site_load_kwh: number
  charge_energy_kwh: number; discharge_energy_kwh: number; grid_export_kwh: number
  grid_import_kwh: number; previous_soc: number; new_soc: number
}
export type Optimization = {
  schedule: Hour[]; initial_soc: number; final_soc: number; target_final_soc?: number
  total_grid_export_kwh: number; total_grid_import_kwh: number; total_site_load_kwh: number
  net_revenue_difference_tl: number; net_theoretical_revenue_tl: number
  effective_max_charge_kw: number; effective_max_discharge_kw: number
  device_limits: DeviceLimits; [key: string]: unknown
}
export type OptimizeResponse = {pricing_summary: {start_date: string; end_date: string; matched_hours: number}; optimization: Optimization}
export type Session = {session_id: string; started_at: string; updated_at: string; state: string; simulated_at: string; end_at: string; totals: Totals; error: string | null}
export type Scenario = { battery: Battery; start_date: string; end_date: string; grid_export_limit_kw: number; site_load_kw: number; target_final_soc: number; device_limits: DeviceLimits }
