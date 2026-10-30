import pandas as pd
import pvlib
from zoneinfo import ZoneInfo

from api.visual_crossing import get_havadurumu
from fastapi import APIRouter
from datetime import datetime, timedelta
from config import INSTALLED_CAPACITY_KWP, ENLEM, BOYLAM, PERFORMANCE_RATIO, KATSAYI, PANEL_TILT, PANEL_AZIMUTH, ALBEDO

router = APIRouter()

#tahmini ges gücü hesaplama
def calculate_pv_power(
    installed_capacity_kwp: float,
    solar_radiation: float,
    performance_ratio: float = PERFORMANCE_RATIO
) -> float:
    if installed_capacity_kwp <= 0:
        raise Exception("kurulu güç 0 dan büyük olmalı")

    if not 0 < performance_ratio <= 1:
        raise ValueError("performans oranı 0 ile 1 arasında olmalıdır")
    radiation = max(0.0, solar_radiation)

    power = (
        installed_capacity_kwp
        * radiation / KATSAYI # KATSAYI 1000 w/m^2 olaark referans alındı
        * performance_ratio
    )

    return round(min(power, installed_capacity_kwp), 3)

def calculate_poa_irradiance(
    ghi: float,
    datetime_str: str
) -> float: # panel yüzeyine gelen güneş ışınımını hesaplar
    if ghi <= 0:
        return 0.0

    timestamp = pd.Timestamp(datetime_str)

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(
            ZoneInfo("Europe/Istanbul")
        )

    timestamp = timestamp - timedelta(minutes=30)

    solar_position = pvlib.solarposition.get_solarposition(
        time=pd.DatetimeIndex([timestamp]),
        latitude=ENLEM,
        longitude=BOYLAM,
    )

    solar_zenith = float(
        solar_position["zenith"].iloc[0]
    )

    solar_azimuth = float(
        solar_position["azimuth"].iloc[0]
    )

    irradiance = pvlib.irradiance.erbs(  # GHI verisinden dnı ve dhı tahmin etmek
        ghi=ghi,
        zenith=solar_zenith,
        datetime_or_doy=timestamp.dayofyear
    )

    poa = pvlib.irradiance.get_total_irradiance(  # panel düzlemine gelen ışınım
        surface_tilt=PANEL_TILT,
        surface_azimuth=PANEL_AZIMUTH,
        solar_zenith=solar_zenith,
        solar_azimuth=solar_azimuth,
        dni=irradiance["dni"],
        ghi=ghi,
        dhi=irradiance["dhi"],
        albedo=ALBEDO
    )

    return round(max(0.0, float(poa["poa_global"])), 3)



#gelen saatlik veriler işleyecek her saat için ilk fonksiyonu çağırması lazım
def generate_hourly_forecast(
        weather_data: dict,
        installed_capacity_kwp: float,
        performance_ratio: float = PERFORMANCE_RATIO
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

            if radiation is None:
                continue

            hour_datetime = f"{day['datetime']}T{hour['datetime']}" # saat bilgisini olustururu

            poa_radiation = calculate_poa_irradiance(   # panel eğimi ve yönüne göre ışınım hesaplar
                ghi=radiation,
                datetime_str=hour_datetime
            )


            predicted_power = calculate_pv_power(
                installed_capacity_kwp=installed_capacity_kwp,
                solar_radiation=poa_radiation,
                performance_ratio=performance_ratio
            )

            predicted_energy = predicted_power * 1.0  # burda kwh dönüşümü yapılıyor
            daily_energy += predicted_energy

            hourly_forecast.append({
                "datetime": hour_datetime,
                "solar_radiation": radiation,
                "poa_radiation": poa_radiation,
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
        "panel_egimi": PANEL_TILT,
        "panel_yonu": PANEL_AZIMUTH,
        "yüzey_yansitici": ALBEDO,
        "installed_capacity_kwp": installed_capacity_kwp,
        "hourly_forecast": hourly_forecast,
        "daily_forecast": daily_forecast,
        "total_predicted_energy_kwh": round(
            sum(d["predicted_energy_kwh"] for d in daily_forecast), 3
        )
    }

@router.get('/forecast')
def get_generation_forecast():
    weather_data = get_havadurumu(ENLEM,BOYLAM)

    return generate_hourly_forecast(
        weather_data=weather_data,
        installed_capacity_kwp=INSTALLED_CAPACITY_KWP
    )