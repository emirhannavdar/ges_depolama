
from api.battery import (
    BatteryConfig,
    calculate_effective_capacity,
    calculate_usable_capacity,
    calculate_stored_energy,
    calculate_charge,
    calculate_discharge,
    simulate_battery
)

battery = BatteryConfig(
    capacity_kwh=100,
    soc=50,
    soh=90,
    min_soc=10,
    max_soc=90,
    max_charge_kw=50,
    max_discharge_kw=50,
    charge_efficiency=0.95,
    discharge_efficiency=0.95
)

print("Efektif kapasite:", calculate_effective_capacity(battery), "kWh")
print("Kullanılabilir kapasite:", calculate_usable_capacity(battery), "kWh")
print("Mevcut depolanan enerji:", calculate_stored_energy(battery), "kWh")

print("\n--- ŞARJ TESTİ ---")
charge_result = calculate_charge(
    battery=battery,
    requested_power_kw=20,
    duration_hours=1
)
print(charge_result)

print("\n--- DEŞARJ TESTİ ---")
discharge_result = calculate_discharge(
    battery=battery,
    requested_power_kw=20,
    duration_hours=1
)
print(discharge_result)

schedule = [
    {"action": "charge", "power_kw": 20, "duration_hours": 1},
    {"action": "charge", "power_kw": 5, "duration_hours": 1},
    {"action": "discharge", "power_kw": 20, "duration_hours": 1},
    {"action": "idle", "power_kw": 0, "duration_hours": 1},
]

simulation = simulate_battery(battery, schedule)

print("\n--- SAATLİK BATARYA SİMÜLASYONU ---")

for index, step in enumerate(simulation, start=1):
    print(
        f"Saat {index} | "
        f"İşlem: {step['action']} | "
        f"Önceki SOC: %{step['previous_soc']:.3f} | "
        f"Yeni SOC: %{step['new_soc']:.3f}"
    )

print("\n--- BATARYA SINIR TESTLERİ ---")

# Maksimum SOC testi
battery_high = battery.model_copy(update={"soc": 88})

charge_limit = calculate_charge(
    battery=battery_high,
    requested_power_kw=50,
    duration_hours=1
)

print("Maksimum SOC testi:", charge_limit)
assert charge_limit["new_soc"] <= 90

# Minimum SOC testi
battery_low = battery.model_copy(update={"soc": 12})

discharge_limit = calculate_discharge(
    battery=battery_low,
    requested_power_kw=50,
    duration_hours=1
)

print("Minimum SOC testi:", discharge_limit)
assert discharge_limit["new_soc"] >= 10

# Maksimum şarj gücü testi
battery_mid = battery.model_copy(update={"soc": 50})

power_limit = calculate_charge(
    battery=battery_mid,
    requested_power_kw=100,
    duration_hours=0.5
)

print("Şarj güç limiti testi:", power_limit)
assert power_limit["actual_power_kw"] <= battery_mid.max_charge_kw

print("Tüm sınır testleri başarılı!")