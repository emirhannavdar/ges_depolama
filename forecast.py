import requests
from datetime import datetime, timedelta
from typing import Optional
from api.visual_crossing import get_havadurumu
from fastapi import APIRouter
from datetime import datetime

router = APIRouter()

#tahmini ges gücü hesaplama
def calculate_pv_power(
    installed_capacity_kwp: float,
    solar_radiation: float,
    performance_ratio: float = 0.85
) -> float:
    if installed_capacity_kwp <= 0:
        raise Exception("kurulu güç 0 dan büyük olmalı")

    if not 0 < performance_ratio <= 1:
        raise ValueError("performans oranı 0 ile 1 arasında olmalıdır")
    radiation = max(0.0, solar_radiation)

    power = (
        installed_capacity_kwp
        * radiation / 1000
        * performance_ratio
    )

    return round(min(power, installed_capacity_kwp), 3)


#gelen saatlik veriler işleyecek her saat için ilk fonksiyonu çağırması lazım
def generate_hourly_forecast(
        weather_data: dict,
        installed_capacity_kwp: float,
        performance_ratio: float = 0.85
) -> dict:

    if "days" not in weather_data and "data" in weather_data:
        weather_data = weather_data["data"]

    hourly_forecast = []
    daily_forecast = []

    for day in weather_data["days"]:
        daily_energy = 0.0

        for hour in day.get("hours", []):
            radiation = hour.get("solarradiation")

            if radiation is None:
                radiation = hour.get("solarRadiation")
            predicted_power = calculate_pv_power(
                installed_capacity_kwp,
                radiation,
                performance_ratio
            )

            predicted_energy = predicted_power * 1.0
            daily_energy += predicted_energy

            hourly_forecast.append({
                "datetime": f"{day['datetime']}T{hour['datetime']}",
                "solar_radiation": radiation,
                "temperature": hour.get("temp"),
                "cloud_cover": hour.get("cloudcover"),
                "predicted_power_kw": predicted_power,
                "predicted_energy_kwh": predicted_energy
            })
        daily_forecast.append({
            "date": day["datetime"],
            "predicted_energy_kwh": round(daily_energy, 3)
        })
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "installed_capacity_kwp": installed_capacity_kwp,
        "hourly_forecast": hourly_forecast,
        "daily_forecast": daily_forecast,
        "total_predicted_energy_kwh": round(
            sum(d["predicted_energy_kwh"] for d in daily_forecast), 3
        )
    }

@router.get('/forecast')
async def get_generation_forecast():
    weather_data = get_havadurumu(39.977069743745155,32.68781801235893)

    return generate_hourly_forecast(
        weather_data=weather_data,
        installed_capacity_kwp=39700
    )