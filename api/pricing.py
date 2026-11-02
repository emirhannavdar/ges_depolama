from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from api.epias import post_PTF
from api.visual_crossing import get_havadurumu
from api.forecast import generate_hourly_forecast

from config import (
    INSTALLED_CAPACITY_KWP,
    ENLEM,
    BOYLAM
)

router = APIRouter()

TIMEZONE = ZoneInfo("Europe/Istanbul")

def normalize_price_data(epias_data: dict) -> list:     # EPIAS verilerini standart et
    if "data" in epias_data:
        epias_data = epias_data["data"]

    normalized_prices = []

    for item in epias_data["items"]:
        timestamp = datetime.fromisoformat(item["date"])

        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=TIMEZONE)

        normalized_prices.append({
            "datetime": timestamp.isoformat(),
            "price_tl_mwh": float(item["price"])
        })
    return normalized_prices

def calculate_energy_value(
        energy_kwh: float,
        price_tl_mwh: float,
) -> float:
    energy_mwh = energy_kwh / 1000

    return round (energy_mwh * price_tl_mwh, 2)

def match_price_and_generation(
        price_data: list,
        forecast_data: list
) -> list:
    price_lookup = {}

    for item in price_data:
        dt = datetime.fromisoformat(item["datetime"])
        price_lookup[dt] = item["price_tl_mwh"]

    result = []

    for item in forecast_data:
        dt = datetime.fromisoformat(item["datetime"])

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TIMEZONE)

        price = price_lookup.get(dt)

        energy = item["predicted_energy_kwh"]

        result.append({
            "datetime": dt.isoformat(),
            "predicted_energy_kwh": energy,
            "price_tl_mwh": price,
            "theoretical_value_tl": (
                calculate_energy_value(energy, price)
                if price is not None
                else None
            )
        })

    return result

@router.get('/pricing')
def get_pricing(
        start_date: date | None = None,
        end_date: date | None = None,
):
    today = datetime.now(TIMEZONE).date()
    if start_date is None:
        start_date = today

    if end_date is None:
        end_date = start_date + timedelta(days=1)

    if end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="Bitiş tarihi başlangıçtan önce olamaz.",
        )

    start_datetime = datetime.combine(
        start_date,
        datetime.min.time(),
        tzinfo=TIMEZONE
    ).isoformat(timespec="seconds")

    end_datetime = datetime.combine(
        end_date,
        datetime.min.time(),
        tzinfo=TIMEZONE
    ).isoformat(timespec="seconds")

    epias_data = post_PTF(      #epiasdan gerçek verileri çek
        startDate= start_datetime,
        endDate=end_datetime
    )

    prices = normalize_price_data(epias_data)

    weather_data = get_havadurumu(ENLEM, BOYLAM)

    forecast = generate_hourly_forecast(        # 3. Mevcut forecast.py fonksiyonunu kullan
        weather_data=weather_data,
        installed_capacity_kwp=INSTALLED_CAPACITY_KWP
    )

    matched_data = match_price_and_generation(
        price_data=prices,
        forecast_data=forecast["hourly_forecast"]
    )

    matched_data = [
        item for item in matched_data
        if start_date <=datetime.fromisoformat(
            item["datetime"]
        ).date() <= end_date
    ]

    total_value = sum(      # 6. Fiyatı mevcut saatlerin teorik değerini topla
        item["theoretical_value_tl"]
        for item in matched_data
        if item["theoretical_value_tl"] is not None
    )

    matched_hours = sum(
        item["price_tl_mwh"] is not None
        for item in matched_data
    )

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "installed_capacity_kwp": INSTALLED_CAPACITY_KWP,
        "total_hours": len(matched_data),
        "matched_hours": matched_hours,
        "total_theoretical_value_tl": round(total_value, 2),
        "hourly_data": matched_data,
        "epias_price_count": len(prices),
        "forecast_hour_count": len(forecast["hourly_forecast"]),
        "unmatched_hours": len(matched_data) - matched_hours
    }





















