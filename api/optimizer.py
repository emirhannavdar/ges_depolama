
from datetime import datetime, date
from math import isfinite

import numpy as np
from scipy.optimize import linprog

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.battery import (
    BatteryConfig,
    calculate_effective_capacity
)
from api.pricing import get_pricing

from config import (
    GRID_EXPORT_ENABLED,
    GRID_EXPORT_LIMIT_KW,
    BATTERY_DEGRADATION_COST_TL_PER_KWH,
    SITE_DEFAULT_LOAD_KW,
    GRID_IMPORT_ENABLED,
    GRID_IMPORT_LIMIT_KW
)


router = APIRouter()


class DeviceLimits(BaseModel):
    # BMS çalışma izinleri
    bms_charge_allowed: bool = True
    bms_discharge_allowed: bool = True

    # BMS anlık güç sınırları (kW)
    bms_max_charge_kw: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False
    )
    bms_max_discharge_kw: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False
    )

    # PCS çalışma durumu
    pcs_online: bool = True

    # PCS anlık güç sınırları (kW)
    pcs_max_charge_kw: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False
    )
    pcs_max_discharge_kw: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False
    )


class OptimizationRequest(BaseModel):
    battery: BatteryConfig
    start_date: date | None = None
    end_date: date | None = None

    # Tesisin şebekeye verebileceği maksimum güç
    # None = eski modeldeki gibi sınır uygulanmaz
    grid_export_limit_kw: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False
    )

    device_limits: DeviceLimits = Field(
        default_factory=DeviceLimits
    )

    site_load_kw: float = Field(
        default=SITE_DEFAULT_LOAD_KW,
        ge=0,
        allow_inf_nan=False
    )

    load_profile_kwh: dict[str, float] = Field(
        default_factory=dict
    )

    target_final_soc: float | None = Field(
        default=None,
        ge = 0,
        le=100,
        allow_inf_nan=False
    )


def optimize_schedule_lp(
    hourly_data: list,
    battery: BatteryConfig,
    grid_export_limit_kw: float | None = None,
    degradation_cost_tl_per_kwh: float | None = None,
    device_limits: DeviceLimits | None = None,
    target_final_soc: float |None = None
) -> dict:

    if not hourly_data:
        raise ValueError("Optimizasyon için saatlik veri bulunamadı")

    if target_final_soc is None:
        target_final_soc = battery.soc

    if not (
        battery.soc <= target_final_soc <= battery.max_soc
    ):
        raise ValueError(
            "Hedef SOC, başlangıç SOC ile maksimum SOC "
            "arasında olmalıdır"
        )

    # Şebeke politikaları
    if grid_export_limit_kw is None:
        grid_export_limit_kw = GRID_EXPORT_LIMIT_KW

    if not GRID_EXPORT_ENABLED:
        grid_export_limit_kw = 0.0

    if grid_export_limit_kw is not None:
        if not isfinite(grid_export_limit_kw) or grid_export_limit_kw < 0:
            raise ValueError("Geçersiz şebeke satış sınırı")

    import_limit_kw = GRID_IMPORT_LIMIT_KW

    if not GRID_IMPORT_ENABLED:
        import_limit_kw = 0.0

    if import_limit_kw is not None:
        if not isfinite(import_limit_kw) or import_limit_kw < 0:
            raise ValueError("Geçersiz şebeke alış sınırı")

    if degradation_cost_tl_per_kwh is None:
        degradation_cost_tl_per_kwh = (
            BATTERY_DEGRADATION_COST_TL_PER_KWH
        )

    if (
        not isfinite(degradation_cost_tl_per_kwh)
        or degradation_cost_tl_per_kwh < 0
    ):
        raise ValueError("Geçersiz yıpranma maliyeti")

    # Saatlik veriler
    hours = sorted(
        hourly_data,
        key=lambda x: datetime.fromisoformat(x["datetime"])
    )

    n = len(hours)
    generation = []
    loads = []
    prices = []
    import_prices = []

    for hour in hours:
        energy = float(hour["predicted_energy_kwh"])
        load = float(hour.get("load_kwh", 0))
        price_value = hour.get("price_tl_mwh")

        if price_value is None:
            raise ValueError(
                f"PTF bulunamadı: {hour['datetime']}"
            )

        price = float(price_value)
        import_price = float(
            hour.get("import_price_tl_mwh", price)
        )

        if not isfinite(energy) or energy < 0:
            raise ValueError("Geçersiz GES üretimi")

        if not isfinite(load) or load < 0:
            raise ValueError("Geçersiz tesis tüketimi")

        if not isfinite(price) or not isfinite(import_price):
            raise ValueError("Geçersiz elektrik fiyatı")

        generation.append(energy)
        loads.append(load)
        prices.append(price)
        import_prices.append(import_price)

    # Batarya kapasitesi
    effective_capacity = calculate_effective_capacity(battery)

    if effective_capacity <= 0:
        raise ValueError("Efektif batarya kapasitesi geçersiz")

    extra_capacity_kwh = (
        effective_capacity * (battery.max_soc - battery.soc) / 100
    )

    eta_c = battery.charge_efficiency
    eta_d = battery.discharge_efficiency

    if not (0 < eta_c <= 1 and 0 < eta_d <= 1):
        raise ValueError("Geçersiz batarya verimleri")

    # Batarya ve C-rate güç sınırları
    effective_max_charge_kw = battery.max_charge_kw
    effective_max_discharge_kw = battery.max_discharge_kw

    if battery.max_charge_c is not None:
        effective_max_charge_kw = min(
            effective_max_charge_kw,
            effective_capacity * battery.max_charge_c
        )

    if battery.max_discharge_c is not None:
        effective_max_discharge_kw = min(
            effective_max_discharge_kw,
            effective_capacity * battery.max_discharge_c
        )

    # BMS / PCS sınırları
    if device_limits is None:
        device_limits = DeviceLimits()

    if not device_limits.bms_charge_allowed:
        effective_max_charge_kw = 0.0

    if not device_limits.bms_discharge_allowed:
        effective_max_discharge_kw = 0.0

    if device_limits.bms_max_charge_kw is not None:
        effective_max_charge_kw = min(
            effective_max_charge_kw,
            device_limits.bms_max_charge_kw
        )

    if device_limits.bms_max_discharge_kw is not None:
        effective_max_discharge_kw = min(
            effective_max_discharge_kw,
            device_limits.bms_max_discharge_kw
        )

    if not device_limits.pcs_online:
        effective_max_charge_kw = 0.0
        effective_max_discharge_kw = 0.0

    if device_limits.pcs_max_charge_kw is not None:
        effective_max_charge_kw = min(
            effective_max_charge_kw,
            device_limits.pcs_max_charge_kw
        )

    if device_limits.pcs_max_discharge_kw is not None:
        effective_max_discharge_kw = min(
            effective_max_discharge_kw,
            device_limits.pcs_max_discharge_kw
        )

    # Değişkenler, her grup n uzunluğunda:
    # 0: charge
    # 1: discharge
    # 2: stored
    # 3: battery_mode
    # 4: curtailment
    # 5: grid_export
    # 6: grid_import
    # 7: grid_mode

    epsilon = 1e-7

    # Amaç: satış - alış - yıpranma maliyetini maksimize etmek
    # linprog minimizasyon yaptığı için işaretler ters.
    objective = (
        [epsilon] * n
        + [degradation_cost_tl_per_kwh + epsilon] * n
        + [0.0] * n
        + [0.0] * n
        + [epsilon] * n
        + [-p / 1000 for p in prices]
        + [p / 1000 for p in import_prices]
        + [0.0] * n
    )

    bounds = []
    max_charge_by_hour = []
    max_discharge_by_hour = []
    max_export_by_hour = []
    max_import_by_hour = []

    for t in range(n):
        # Önce tesis tüketimini karşılayan GES enerjisini ayır.
        # Batarya yalnızca kalan GES üretiminden şarj olur.
        solar_surplus = max(0.0, generation[t] - loads[t])

        max_charge_by_hour.append(
            min(solar_surplus, effective_max_charge_kw)
        )
        max_discharge_by_hour.append(
            effective_max_discharge_kw
        )

        export_max = generation[t] + effective_max_discharge_kw

        if grid_export_limit_kw is not None:
            export_max = min(export_max, grid_export_limit_kw)

        max_export_by_hour.append(export_max)

        import_max = loads[t]

        if import_limit_kw is not None:
            import_max = min(import_max, import_limit_kw)

        max_import_by_hour.append(import_max)

    # charge
    bounds.extend((0, x) for x in max_charge_by_hour)

    # discharge
    bounds.extend((0, x) for x in max_discharge_by_hour)

    # stored
    bounds.extend((0, extra_capacity_kwh) for _ in range(n))

    # battery_mode
    bounds.extend((0, 1) for _ in range(n))

    # curtailment
    bounds.extend((0, generation[t]) for t in range(n))

    # grid_export
    bounds.extend((0, x) for x in max_export_by_hour)

    # grid_import
    bounds.extend((0, x) for x in max_import_by_hour)

    # grid_mode
    bounds.extend((0, 1) for _ in range(n))

    # Eşitlikler:
    # n adet batarya enerji dengesi
    # 1 adet final SOC koşulu
    # n adet tesis enerji dengesi
    A_eq = np.zeros((2 * n + 1, 8 * n))
    b_eq = np.zeros(2 * n + 1)

    for t in range(n):
        # stored[t] = stored[t-1]
        # + eta_c * charge[t]
        # - discharge[t] / eta_d
        A_eq[t, t] = -eta_c
        A_eq[t, n + t] = 1 / eta_d
        A_eq[t, 2 * n + t] = 1

        if t > 0:
            A_eq[t, 2 * n + t - 1] = -1

        # Tesis enerji dengesi:
        # generation + discharge + grid_import
        # = load + charge + export + curtailment
        row = n + 1 + t

        A_eq[row, t] = 1
        A_eq[row, n + t] = -1
        A_eq[row, 4 * n + t] = 1
        A_eq[row, 5 * n + t] = 1
        A_eq[row, 6 * n + t] = -1

        b_eq[row] = generation[t] - loads[t]

    # Son SOC, kullanıcının hedeflediği değere eşit
    A_eq[n, 3 * n - 1] = 1

    b_eq[n] = (
            effective_capacity
            * (target_final_soc - battery.soc)
            / 100
    )

    # Her saat için 5 adet eşitsizlik
    A_ub = np.zeros((5 * n, 8 * n))
    b_ub = np.zeros(5 * n)

    for t in range(n):
        row = 5 * t

        # Batarya şarj modu
        A_ub[row, t] = 1
        A_ub[row, 3 * n + t] = -max_charge_by_hour[t]

        # Batarya deşarj modu
        A_ub[row + 1, n + t] = 1
        A_ub[row + 1, 3 * n + t] = max_discharge_by_hour[t]
        b_ub[row + 1] = max_discharge_by_hour[t]

        # Batarya şarjı + GES kısıntısı <= GES üretimi
        A_ub[row + 2, t] = 1
        A_ub[row + 2, 4 * n + t] = 1
        b_ub[row + 2] = generation[t]

        # Şebeke satış modu
        # export <= max_export * grid_mode
        A_ub[row + 3, 5 * n + t] = 1
        A_ub[row + 3, 7 * n + t] = -max_export_by_hour[t]

        # Şebeke alış modu
        # import <= max_import * (1 - grid_mode)
        A_ub[row + 4, 6 * n + t] = 1
        A_ub[row + 4, 7 * n + t] = max_import_by_hour[t]
        b_ub[row + 4] = max_import_by_hour[t]

    # Tam sayı olanlar battery_mode ve grid_mode
    integrality = np.array(
        [0] * (3 * n)
        + [1] * n
        + [0] * (3 * n)
        + [1] * n
    )

    solution = linprog(
        c=objective,
        A_ub=A_ub,
        b_ub=b_ub,
        A_eq=A_eq,
        b_eq=b_eq,
        bounds=bounds,
        integrality=integrality,
        method="highs"
    )

    if not solution.success:
        raise RuntimeError(
            f"Optimizasyon başarısız: {solution.message}"
        )

    charge = solution.x[:n]
    discharge = solution.x[n:2*n]
    stored = solution.x[2*n:3*n]
    curtailment = solution.x[4*n:5*n]
    grid_export = solution.x[5*n:6*n]
    grid_import = solution.x[6*n:7*n]

    schedule = []

    total_export = 0.0
    total_import = 0.0
    total_load = 0.0
    total_curtailment = 0.0
    total_discharge = 0.0

    total_export_revenue = 0.0
    total_import_cost = 0.0
    total_degradation_cost = 0.0
    baseline_net_value = 0.0

    previous_soc = battery.soc

    for t, hour in enumerate(hours):
        c = max(0.0, float(charge[t]))
        d = max(0.0, float(discharge[t]))
        curtailed = max(0.0, float(curtailment[t]))
        exported = max(0.0, float(grid_export[t]))
        imported = max(0.0, float(grid_import[t]))

        new_soc = (
            battery.soc
            + float(stored[t]) / effective_capacity * 100
        )

        if c > 1e-6 and d > 1e-6:
            raise RuntimeError("Eş zamanlı şarj/deşarj")

        if exported > 1e-6 and imported > 1e-6:
            raise RuntimeError("Eş zamanlı şebeke alış/satış")

        balance_error = (
            generation[t] + d + imported
            - loads[t] - c - exported - curtailed
        )

        if abs(balance_error) > 1e-5:
            raise RuntimeError("Tesis enerji dengesi hatası")

        export_revenue = exported * prices[t] / 1000
        import_cost = imported * import_prices[t] / 1000
        degradation_cost = d * degradation_cost_tl_per_kwh

        gross_value = export_revenue - import_cost
        net_value = gross_value - degradation_cost

        # Bataryasız referans: önce öz tüketim
        baseline_export = max(0.0, generation[t] - loads[t])
        baseline_import = max(0.0, loads[t] - generation[t])

        if grid_export_limit_kw is not None:
            baseline_export = min(
                baseline_export, grid_export_limit_kw
            )

        if not GRID_IMPORT_ENABLED:
            if baseline_import > 1e-6:
                raise ValueError(
                    "Bataryasız referans için şebekeden alış gerekli"
                )

        if import_limit_kw is not None:
            if baseline_import > import_limit_kw + 1e-6:
                raise ValueError(
                    "Bataryasız referans alış limitini aşıyor"
                )

        if prices[t] < 0:
            baseline_export = 0.0

        baseline_hour_value = (
            baseline_export * prices[t] / 1000
            - baseline_import * import_prices[t] / 1000
        )

        total_export += exported
        total_import += imported
        total_load += loads[t]
        total_curtailment += curtailed
        total_discharge += d

        total_export_revenue += export_revenue
        total_import_cost += import_cost
        total_degradation_cost += degradation_cost
        baseline_net_value += baseline_hour_value

        if c > 1e-6:
            action = "CHARGE"
        elif d > 1e-6:
            action = "DISCHARGE"
        else:
            action = "IDLE"

        schedule.append({
            "datetime": hour["datetime"],
            "action": action,
            "price_tl_mwh": prices[t],
            "import_price_tl_mwh": import_prices[t],
            "generation_kwh": round(generation[t], 3),
            "site_load_kwh": round(loads[t], 3),
            "charge_energy_kwh": round(c, 3),
            "discharge_energy_kwh": round(d, 3),
            "curtailment_kwh": round(curtailed, 3),
            "grid_export_kwh": round(exported, 3),
            "grid_import_kwh": round(imported, 3),
            "previous_soc": round(previous_soc, 3),
            "new_soc": round(new_soc, 3),
            "grid_export_revenue_tl": round(export_revenue, 2),
            "grid_import_cost_tl": round(import_cost, 2),
            "theoretical_revenue_tl": round(gross_value, 2),
            "degradation_cost_tl": round(degradation_cost, 2),
            "net_theoretical_revenue_tl": round(net_value, 2)
        })

        previous_soc = new_soc

    gross_value = total_export_revenue - total_import_cost
    net_value = gross_value - total_degradation_cost

    return {
        "method": "mixed_integer_linear_programming_highs",
        "grid_export_limit_kw": grid_export_limit_kw,
        "grid_import_limit_kw": import_limit_kw,
        "initial_soc": battery.soc,
        "final_soc": round(previous_soc, 3),
        "total_site_load_kwh": round(total_load, 3),
        "total_grid_export_kwh": round(total_export, 3),
        "total_grid_import_kwh": round(total_import, 3),
        "total_curtailment_kwh": round(total_curtailment, 3),
        "total_discharge_energy_kwh": round(total_discharge, 3),
        "total_grid_export_revenue_tl": round(
            total_export_revenue, 2
        ),
        "total_grid_import_cost_tl": round(
            total_import_cost, 2
        ),
        "total_theoretical_revenue_tl": round(gross_value, 2),
        "direct_sale_revenue_tl": round(baseline_net_value, 2),
        "estimated_revenue_difference_tl": round(
            gross_value - baseline_net_value, 2
        ),
        "degradation_cost_tl_per_kwh": degradation_cost_tl_per_kwh,
        "total_degradation_cost_tl": round(
            total_degradation_cost, 2
        ),
        "net_theoretical_revenue_tl": round(net_value, 2),
        "net_revenue_difference_tl": round(
            net_value - baseline_net_value, 2
        ),
        "effective_max_charge_kw": round(
            effective_max_charge_kw, 3
        ),
        "effective_max_discharge_kw": round(
            effective_max_discharge_kw, 3
        ),
        "device_limits": device_limits.model_dump(),
        "schedule": schedule,
        "target_final_soc": target_final_soc
    }

@router.post("/optimize")
def optimize(request: OptimizationRequest):

    pricing_data = get_pricing(
        start_date=request.start_date,
        end_date=request.end_date
    )

    hourly_data = []

    for hour in pricing_data["hourly_data"]:
        item = hour.copy()

        load = request.load_profile_kwh.get(
            item["datetime"],
            request.site_load_kw
        )

        item["load_kwh"] = load
        hourly_data.append(item)

    try:
        result = optimize_schedule_lp(
            hourly_data=hourly_data,
            battery=request.battery,
            grid_export_limit_kw=request.grid_export_limit_kw,
            device_limits=request.device_limits,
            target_final_soc=request.target_final_soc
        )

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error)
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error)
        ) from error

    return {
        "pricing_summary": {
            "start_date": pricing_data["start_date"],
            "end_date": pricing_data["end_date"],
            "matched_hours": pricing_data["matched_hours"]
        },
        "optimization": result
    }
