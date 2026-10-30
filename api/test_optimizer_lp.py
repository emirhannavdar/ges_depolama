
from api.battery import BatteryConfig
from api.optimizer_lp import optimize_schedule_lp

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

result = optimize_schedule_lp(hourly_data, battery)

for hour in result["schedule"]:
    print(
        hour["datetime"],
        hour["action"],
        "SOC:", hour["previous_soc"],
        "->", hour["new_soc"]
    )

print("Bataryalı değer:", result["total_theoretical_revenue_tl"])
print("Doğrudan satış:", result["direct_sale_revenue_tl"])
print("Değer farkı:", result["estimated_revenue_difference_tl"])

assert abs(result["final_soc"] - 50) < 0.001
assert abs(result["estimated_revenue_difference_tl"] - 26.10) < 0.01

print("LP testi başarılı!")
