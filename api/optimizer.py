
from datetime import datetime, date
from math import isfinite

import numpy as np
from scipy.optimize import linprog

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.battery import (
    BatteryConfig,
    calculate_effective_capacity
)
from api.pricing import get_pricing


router = APIRouter()


# Optimizasyon isteği
class OptimizationRequest(BaseModel):
    battery: BatteryConfig
    start_date: date | None = None
    end_date: date | None = None


# Matematiksel enerji optimizasyonu
def optimize_schedule_lp(
    hourly_data: list,
    battery: BatteryConfig
) -> dict:

    if not hourly_data:
        raise ValueError("Optimizasyon için saatlik veri bulunamadı")

    # Verileri tarih ve saate göre sırala
    hours = sorted(
        hourly_data,
        key=lambda item: datetime.fromisoformat(item["datetime"])
    )

    n = len(hours)

    generation = []
    prices = []

    for hour in hours:

        energy = float(hour["predicted_energy_kwh"])
        price = hour.get("price_tl_mwh")

        if price is None:
            raise ValueError(
                f"PTF bulunamadı: {hour['datetime']}"
            )

        price = float(price)

        if not isfinite(energy) or energy < 0:
            raise ValueError("Geçersiz GES üretim değeri")

        if not isfinite(price):
            raise ValueError("Geçersiz PTF değeri")

        generation.append(energy)
        prices.append(price)

    # Bataryanın efektif kapasitesi
    effective_capacity = calculate_effective_capacity(battery)

    # Başlangıç SOC üzerinde kullanılabilir ek kapasite
    extra_capacity_kwh = (
        effective_capacity
        * (battery.max_soc - battery.soc)
        / 100
    )

    # Batarya verimleri
    eta_c = battery.charge_efficiency
    eta_d = battery.discharge_efficiency

    # Karar değişkenleri:
    # charge[t]    -> Bataryaya verilen enerji
    # discharge[t] -> Bataryadan çıkan enerji
    # stored[t]    -> Başlangıç SOC üstündeki ek enerji

    # Amaç fonksiyonu
    # SciPy linprog minimizasyon yapar.
    # Deşarj gelirini negatif katsayıyla temsil ediyoruz.

    epsilon = 1e-7

    objective = (
        [price / 1000 + epsilon for price in prices]
        + [-price / 1000 + epsilon for price in prices]
        + [0.0] * n
    )

    bounds = []

    # Şarj kısıtları:
    # Yalnızca GES üretiminden şarj yapılabilir.
    # Maksimum şarj gücü aşılmaz.
    # Her zaman adımı 1 saat kabul edilir.

    for energy in generation:
        bounds.append((
            0,
            min(energy, battery.max_charge_kw)
        ))

    # Deşarj güç sınırları
    for _ in range(n):
        bounds.append((
            0,
            battery.max_discharge_kw
        ))

    # Başlangıç SOC üstündeki enerji sınırları
    for _ in range(n):
        bounds.append((
            0,
            extra_capacity_kwh
        ))

    # Enerji dengesi eşitlikleri
    A_eq = np.zeros((n + 1, 3 * n))
    b_eq = np.zeros(n + 1)

    for t in range(n):

        # stored[t] = stored[t-1]
        #             + charge[t] * eta_c
        #             - discharge[t] / eta_d

        A_eq[t, t] = -eta_c
        A_eq[t, n + t] = 1 / eta_d
        A_eq[t, 2 * n + t] = 1

        if t > 0:
            A_eq[t, 2 * n + t - 1] = -1

    # Simülasyon sonunda SOC başlangıç seviyesine döner
    A_eq[n, 3 * n - 1] = 1

    # Matematiksel optimizasyonu çalıştır
    solution = linprog(
        c=objective,
        A_eq=A_eq,
        b_eq=b_eq,
        bounds=bounds,
        method="highs"
    )

    if not solution.success:
        raise RuntimeError(
            f"Optimizasyon başarısız: {solution.message}"
        )

    # Optimizasyon sonuçlarını ayır
    charge = solution.x[:n]
    discharge = solution.x[n:2 * n]
    stored = solution.x[2 * n:]

    schedule = []

    total_revenue = 0.0
    baseline_revenue = 0.0

    previous_soc = battery.soc

    for t, hour in enumerate(hours):

        charge_energy = max(
            0.0,
            float(charge[t])
        )

        discharge_energy = max(
            0.0,
            float(discharge[t])
        )

        new_soc = (
            battery.soc
            + float(stored[t]) / effective_capacity * 100
        )

        # Aynı saatte hem şarj hem deşarj varsa kabul etme
        if charge_energy > 1e-6 and discharge_energy > 1e-6:
            raise RuntimeError(
                "Eş zamanlı şarj/deşarj tespit edildi. "
                "MILP kısıtı gerekli."
            )

        # Doğrudan değerlendirilebilecek GES enerjisi
        direct_energy = generation[t] - charge_energy

        # Bataryalı senaryo teorik geliri
        hourly_revenue = (
            (direct_energy + discharge_energy)
            * prices[t]
            / 1000
        )

        # Bataryasız senaryo teorik geliri
        baseline = (
            generation[t]
            * prices[t]
            / 1000
        )

        total_revenue += hourly_revenue
        baseline_revenue += baseline

        # İşlem türü
        if charge_energy > 1e-6:
            action = "CHARGE"

        elif discharge_energy > 1e-6:
            action = "DISCHARGE"

        else:
            action = "IDLE"

        schedule.append({
            "datetime": hour["datetime"],
            "action": action,
            "price_tl_mwh": prices[t],
            "generation_kwh": generation[t],
            "charge_energy_kwh": round(charge_energy, 3),
            "discharge_energy_kwh": round(discharge_energy, 3),
            "previous_soc": round(previous_soc, 3),
            "new_soc": round(new_soc, 3),
            "theoretical_revenue_tl": round(
                hourly_revenue, 2
            )
        })

        previous_soc = new_soc

    return {
        "method": "linear_programming_highs",
        "initial_soc": battery.soc,
        "final_soc": round(previous_soc, 3),
        "total_theoretical_revenue_tl": round(
            total_revenue, 2
        ),
        "direct_sale_revenue_tl": round(
            baseline_revenue, 2
        ),
        "estimated_revenue_difference_tl": round(
            total_revenue - baseline_revenue, 2
        ),
        "schedule": schedule
    }


# Gerçek EPİAŞ ve GES verileriyle optimizasyon endpoint'i
@router.post("/optimize")
def optimize(request: OptimizationRequest):

    # Gerçek API verilerini al
    pricing_data = get_pricing(
        start_date=request.start_date,
        end_date=request.end_date
    )

    # Matematiksel optimizasyon
    try:
        result = optimize_schedule_lp(
            hourly_data=pricing_data["hourly_data"],
            battery=request.battery
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
