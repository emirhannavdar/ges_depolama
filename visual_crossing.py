import requests

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VISUAL_CROSSING_API_KEY, VISUAL_CROSSING_BASE_URI

visual_crossing = APIRouter()

class WeatherGet(BaseModel):
    enlem: int
    boylam: int

def get_havadurumu(enlem: float, boylam: float):
    url = (
        f"{VISUAL_CROSSING_BASE_URI} + {enlem}, {boylam}"
    )

    data = {
        "key" : VISUAL_CROSSING_API_KEY,
        "contentType" : "json",
        "unitGroup": "metric",
        "include": "days",
        "elements": "datetime,solarradiation,solarenergy",
        "lang": "tr",
    }


    response = requests.get(
        url,
        params = data,
    )


    response.raise_for_status()
    return response.json()

@visual_crossing.get("/weather")
def weather(
    enlem: float,
    boylam: float
):
    try:
        data = get_havadurumu(enlem, boylam)

        return {
            "success": True,
            "latitude": enlem,
            "longitude": boylam,
            "data": data
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )