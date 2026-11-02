
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


print("\n--- MILP ŞARJ/DEŞARJ KONTROLÜ ---")

for hour in result["schedule"]:
    charge = hour["charge_energy_kwh"]
    discharge = hour["discharge_energy_kwh"]

    assert not (charge > 1e-6 and discharge > 1e-6), (
        f"Eş zamanlı şarj/deşarj: {hour['datetime']}"
    )

assert result["method"] == (
    "mixed_integer_linear_programming_highs"
)

print("MILP kısıt testi başarılı!")



# ==========================================
# ZORUNLU GES ÜRETİM KISINTISI TESTİ
# ==========================================

print("\n--- ZORUNLU ÜRETİM KISINTISI TESTİ ---")

full_battery = BatteryConfig(
    capacity_kwh=100,
    soc=90,
    soh=100,
    min_soc=10,
    max_soc=90,
    max_charge_kw=50,
    max_discharge_kw=50,
    charge_efficiency=0.95,
    discharge_efficiency=0.95
)

curtailment_data = [
    {
        "datetime": "2026-11-02T12:00:00+03:00",
        "predicted_energy_kwh": 8,
        "price_tl_mwh": 1000
    }
]

curtailment_result = optimize_schedule_lp(
    hourly_data=curtailment_data,
    battery=full_battery,
    grid_export_limit_kw=5
)

hour = curtailment_result["schedule"][0]

print("GES üretimi:", hour["generation_kwh"], "kWh")
print("Batarya şarjı:", hour["charge_energy_kwh"], "kWh")
print("Şebekeye aktarım:", hour["grid_export_kwh"], "kWh")
print("Üretim kısıntısı:", hour["curtailment_kwh"], "kWh")
print("Son SOC:", hour["new_soc"], "%")

assert abs(hour["charge_energy_kwh"]) < 1e-6
assert abs(hour["discharge_energy_kwh"]) < 1e-6
assert abs(hour["grid_export_kwh"] - 5) < 1e-6
assert abs(hour["curtailment_kwh"] - 3) < 1e-6
assert abs(hour["new_soc"] - 90) < 1e-6

print("Zorunlu üretim kısıntısı testi başarılı!")



print("\n--- BATARYA YIPRANMA MALİYETİ TESTİ ---")

# Daha önce kullandığımız 3 saatlik örnek:
# 12:00 -> 10 kWh, 1000 TL/MWh
# 13:00 ->  0 kWh, 2000 TL/MWh
# 18:00 ->  0 kWh, 4000 TL/MWh

cost_result = optimize_schedule_lp(
    hourly_data=hourly_data,
    battery=battery,
    grid_export_limit_kw=50,
    degradation_cost_tl_per_kwh=0.50
)

print(
    "Brüt ekonomik fark:",
    cost_result["estimated_revenue_difference_tl"],
    "TL"
)
print(
    "Yıpranma maliyeti:",
    cost_result["total_degradation_cost_tl"],
    "TL"
)
print(
    "Maliyet sonrası fark:",
    cost_result["net_revenue_difference_tl"],
    "TL"
)

assert abs(
    cost_result["estimated_revenue_difference_tl"] - 26.10
) < 0.01

assert abs(
    cost_result["total_degradation_cost_tl"] - 4.51
) < 0.01

assert abs(
    cost_result["net_revenue_difference_tl"] - 21.59
) < 0.01

print("Yıpranma maliyeti testi başarılı!")


print("\n--- YÜKSEK YIPRANMA MALİYETİ TESTİ ---")

# Yıpranma maliyeti satış gelirinden yüksekse
# bataryanın gereksiz çevrim yapmamasını bekliyoruz.

high_cost_result = optimize_schedule_lp(
    hourly_data=hourly_data,
    battery=battery,
    grid_export_limit_kw=50,
    degradation_cost_tl_per_kwh=10.0
)

for hour in high_cost_result["schedule"]:
    assert abs(hour["charge_energy_kwh"]) < 1e-6
    assert abs(hour["discharge_energy_kwh"]) < 1e-6

assert abs(
    high_cost_result["net_revenue_difference_tl"]
) < 0.01

print("Yüksek maliyette gereksiz çevrim yapılmadı!")


print("\n--- C-RATE SINIRI TESTİ ---")

c_rate_battery = BatteryConfig(
    capacity_kwh=100,
    soc=50,
    soh=100,
    min_soc=10,
    max_soc=90,
    max_charge_kw=100,
    max_discharge_kw=100,
    max_charge_c=0.2,
    max_discharge_c=0.2,
    charge_efficiency=0.95,
    discharge_efficiency=0.95
)

c_rate_data = [
    {
        "datetime": "2026-11-02T12:00:00+03:00",
        "predicted_energy_kwh": 100,
        "price_tl_mwh": 1000
    },
    {
        "datetime": "2026-11-02T18:00:00+03:00",
        "predicted_energy_kwh": 0,
        "price_tl_mwh": 5000
    }
]

c_rate_result = optimize_schedule_lp(
    hourly_data=c_rate_data,
    battery=c_rate_battery,
    grid_export_limit_kw=200,
    degradation_cost_tl_per_kwh=0
)

print(
    "Uygulanan şarj sınırı:",
    c_rate_result["effective_max_charge_kw"],
    "kW"
)

print(
    "Uygulanan deşarj sınırı:",
    c_rate_result["effective_max_discharge_kw"],
    "kW"
)

for hour in c_rate_result["schedule"]:
    print(
        hour["datetime"],
        hour["action"],
        "Şarj:", hour["charge_energy_kwh"],
        "Deşarj:", hour["discharge_energy_kwh"]
    )

    assert hour["charge_energy_kwh"] <= 20.001
    assert hour["discharge_energy_kwh"] <= 20.001

assert c_rate_result["effective_max_charge_kw"] == 20
assert c_rate_result["effective_max_discharge_kw"] == 20

assert abs(
    c_rate_result["schedule"][0]["charge_energy_kwh"] - 20
) < 0.001

assert abs(
    c_rate_result["schedule"][1]["discharge_energy_kwh"] - 18.05
) < 0.001

assert abs(c_rate_result["final_soc"] - 50) < 0.001

print("C-rate sınırı testi başarılı!")


print("\n--- YIPRANMA MALİYETİ KARŞILAŞTIRMA TESTİ ---")

without_cost = optimize_schedule_lp(
    hourly_data=c_rate_data,
    battery=c_rate_battery,
    grid_export_limit_kw=200,
    degradation_cost_tl_per_kwh=0
)

with_cost = optimize_schedule_lp(
    hourly_data=c_rate_data,
    battery=c_rate_battery,
    grid_export_limit_kw=200,
    degradation_cost_tl_per_kwh=0.50
)

print(
    "Maliyetsiz net değer farkı:",
    without_cost["net_revenue_difference_tl"]
)

print(
    "Maliyetli net değer farkı:",
    with_cost["net_revenue_difference_tl"]
)

assert (
    with_cost["net_revenue_difference_tl"]
    <= without_cost["net_revenue_difference_tl"] + 0.01
)

print("Yıpranma maliyeti karşılaştırması başarılı!")



from api.optimizer import DeviceLimits

print("\n--- BMS / PCS GÜÇ SINIRI TESTİ ---")

device_test_data = [
    {
        "datetime": "2026-11-02T12:00:00+03:00",
        "predicted_energy_kwh": 30,
        "price_tl_mwh": 1000
    },
    {
        "datetime": "2026-11-02T18:00:00+03:00",
        "predicted_energy_kwh": 0,
        "price_tl_mwh": 5000
    }
]

device_test_battery = BatteryConfig(
    capacity_kwh=100,
    soc=50,
    soh=100,
    min_soc=10,
    max_soc=90,
    max_charge_kw=100,
    max_discharge_kw=100,
    max_charge_c=0.5,
    max_discharge_c=0.5,
    charge_efficiency=0.95,
    discharge_efficiency=0.95
)

limits = DeviceLimits(
    bms_charge_allowed=True,
    bms_discharge_allowed=True,
    bms_max_charge_kw=8,
    bms_max_discharge_kw=5,
    pcs_online=True,
    pcs_max_charge_kw=7,
    pcs_max_discharge_kw=6
)

device_result = optimize_schedule_lp(
    hourly_data=device_test_data,
    battery=device_test_battery,
    grid_export_limit_kw=50,
    degradation_cost_tl_per_kwh=0,
    device_limits=limits
)

assert device_result["effective_max_charge_kw"] == 7
assert device_result["effective_max_discharge_kw"] == 5

for hour in device_result["schedule"]:
    assert hour["charge_energy_kwh"] <= 7.001
    assert hour["discharge_energy_kwh"] <= 5.001

assert abs(
    device_result["schedule"][1]["discharge_energy_kwh"] - 5
) < 0.001

assert abs(device_result["final_soc"] - 50) < 0.001

print("BMS / PCS güç sınırı testi başarılı!")


print("\n--- PCS ÇEVRİMDIŞI TESTİ ---")

offline_result = optimize_schedule_lp(
    hourly_data=device_test_data,
    battery=device_test_battery,
    grid_export_limit_kw=50,
    degradation_cost_tl_per_kwh=0,
    device_limits=DeviceLimits(pcs_online=False)
)

assert offline_result["effective_max_charge_kw"] == 0
assert offline_result["effective_max_discharge_kw"] == 0

for hour in offline_result["schedule"]:
    assert abs(hour["charge_energy_kwh"]) < 1e-6
    assert abs(hour["discharge_energy_kwh"]) < 1e-6

print("PCS çevrimdışı testi başarılı!")



print("\n--- TESİS TÜKETİMİ / ENERJİ DENGESİ TESTİ ---")

load_test_data = [
    {
        "datetime": "2026-11-02T12:00:00+03:00",
        "predicted_energy_kwh": 20,
        "load_kwh": 5,
        "price_tl_mwh": 1000
    },
    {
        "datetime": "2026-11-02T18:00:00+03:00",
        "predicted_energy_kwh": 0,
        "load_kwh": 8,
        "price_tl_mwh": 4000
    },
    {
        "datetime": "2026-11-02T19:00:00+03:00",
        "predicted_energy_kwh": 0,
        "load_kwh": 0,
        "price_tl_mwh": 5000
    }
]

load_result = optimize_schedule_lp(
    hourly_data=load_test_data,
    battery=device_test_battery,
    grid_export_limit_kw=50,
    degradation_cost_tl_per_kwh=0
)

for hour in load_result["schedule"]:
    left = (
        hour["generation_kwh"]
        + hour["discharge_energy_kwh"]
        + hour["grid_import_kwh"]
    )

    right = (
        hour["site_load_kwh"]
        + hour["charge_energy_kwh"]
        + hour["grid_export_kwh"]
        + hour["curtailment_kwh"]
    )

    # JSON'daki enerji değerleri 3 basamağa yuvarlanıyor
    assert abs(left - right) < 0.005

    assert not (
        hour["grid_import_kwh"] > 0.001
        and hour["grid_export_kwh"] > 0.001
    )

    print(
        hour["datetime"],
        "| GES:", hour["generation_kwh"],
        "| Tüketim:", hour["site_load_kwh"],
        "| Şebekeden alış:", hour["grid_import_kwh"],
        "| Şebekeye satış:", hour["grid_export_kwh"]
    )

assert abs(
    load_result["total_site_load_kwh"] - 13
) < 0.001

print("Tesis enerji dengesi testi başarılı!")


print("\n--- ŞEBEKEDEN ALIŞ TESTİ ---")

import_test_data = [
    {
        "datetime": "2026-11-02T12:00:00+03:00",
        "predicted_energy_kwh": 8,
        "load_kwh": 3,
        "price_tl_mwh": 2000
    },
    {
        "datetime": "2026-11-02T18:00:00+03:00",
        "predicted_energy_kwh": 0,
        "load_kwh": 6,
        "price_tl_mwh": 3000
    }
]

import_result = optimize_schedule_lp(
    hourly_data=import_test_data,
    battery=device_test_battery,
    grid_export_limit_kw=5,
    degradation_cost_tl_per_kwh=0,
    device_limits=DeviceLimits(pcs_online=False)
)

first = import_result["schedule"][0]
second = import_result["schedule"][1]

assert abs(first["grid_export_kwh"] - 5) < 0.001
assert abs(second["grid_import_kwh"] - 6) < 0.001
assert abs(import_result["total_site_load_kwh"] - 9) < 0.001

print("Şebekeden alış testi başarılı!")



print("\n--- HEDEF SOC TESTİ ---")

target_soc_data = [
    {
        "datetime": "2026-11-02T12:00:00+03:00",
        "predicted_energy_kwh": 10,
        "load_kwh": 0,
        "price_tl_mwh": 1000
    },
    {
        "datetime": "2026-11-02T13:00:00+03:00",
        "predicted_energy_kwh": 0,
        "load_kwh": 0,
        "price_tl_mwh": 2000
    }
]

target_soc_result = optimize_schedule_lp(
    hourly_data=target_soc_data,
    battery=battery,
    grid_export_limit_kw=50,
    degradation_cost_tl_per_kwh=0,
    target_final_soc=55
)

print("Başlangıç SOC:", target_soc_result["initial_soc"])
print("Hedef SOC:", target_soc_result["target_final_soc"])
print("Bitiş SOC:", target_soc_result["final_soc"])

assert abs(target_soc_result["final_soc"] - 55) < 0.001

print("Hedef SOC testi başarılı!")
