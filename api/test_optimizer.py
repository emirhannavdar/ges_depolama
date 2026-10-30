
from api.optimizer import optimize_schedule_lp
from api.battery import BatteryConfig


battery = BatteryConfig(
    capacity_kwh=100,
    soc=50,
    soh=100,
    min_soc=10,
    max_soc=90,
    max_charge_kw=50,
    max_discharge_kw=50,
    charge_efficiency=0.95,
    discharge_efficiency=0.95
)

hourly_data = [
    {
        "datetime": "2026-10-31T12:00:00+03:00",
        "predicted_energy_kwh": 10,
        "price_tl_mwh": 1000
    },
    {
        "datetime": "2026-10-31T13:00:00+03:00",
        "predicted_energy_kwh": 0,
        "price_tl_mwh": 2000
    },
    {
        "datetime": "2026-10-31T18:00:00+03:00",
        "predicted_energy_kwh": 0,
        "price_tl_mwh": 4000
    }
]

result = optimize_schedule_lp(
    hourly_data=hourly_data,
    battery=battery
)

for hour in result["schedule"]:
    print(
        f"{hour['datetime']} | "
        f"{hour['action']} | "
        f"SOC: %{hour['previous_soc']} -> "
        f"%{hour['new_soc']}"
    )

print(
    "Doğrudan satış değeri:",
    result["direct_sale_revenue_tl"],
    "TL"
)

print(
    "Bataryalı teorik değer:",
    result["total_theoretical_revenue_tl"],
    "TL"
)

print(
    "Tahmini değer farkı:",
    result["estimated_revenue_difference_tl"],
    "TL"
)
