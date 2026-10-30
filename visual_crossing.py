from datetime import datetime
from typing import Generic, Optional, TypeVar

import requests

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from config import VISUAL_CROSSING_API_KEY, VISUAL_CROSSING_BASE_URI
from epias import tz

visual_crossing = APIRouter()

T = TypeVar("T")

class RestApiWeather(BaseModel, Generic[T]):
    success : bool
    data: Optional[T] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz))
    errorCode: Optional[int] = None
    message: Optional[str] = None

    @classmethod
    def ok(cls, data: T, message: Optional[str]):
        return cls(success=True, data=data, message=message)

    @classmethod
    def error(cls, message: str, code: Optional[int] = None):
        return cls(success=False, data=None, errorCode=code, message=message)

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

        return RestApiWeather.ok(data=data, message="Basarili")

    except Exception as e:
        error_res = RestApiWeather.error(str(e), 401)
        raise HTTPException(status_code=401, detail=jsonable_encoder(error_res))