from typing import TypeVar, Generic, Optional

import requests

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from config import EPIAS_BASE_URI
from datetime import datetime, timezone, timedelta

epias = APIRouter()

TGT_TOKEN = None
tz = timezone(timedelta(hours=3))

def tarih_formatla(dt_obj):
    return dt_obj.isoformat(timespec="seconds")

T = TypeVar("T")

class RestApiEpias(BaseModel, Generic[T]):
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


class EpiasTGTToken(BaseModel):
    username: str
    password: str

class EpiasPTF(BaseModel):
    startDate: str

def post_TGT_token(username: str, password: str):
    url = "https://giris.epias.com.tr/cas/v1/tickets"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/plain"
    }

    payload = {
        "username": username,
        "password": password
    }

    response = requests.post(
        url,
        headers=headers,
        data=payload,
        timeout=30
    )

    response.raise_for_status()

    token = response.text.strip()
    if not token.startswith("TGT-"):
        raise ValueError("EPİAŞ geçerli bir TGT döndürmedi.")
    return token


def post_PTF(startDate: str):
    if not TGT_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Önce TGT token alınmalı."
        )

    url = (
        EPIAS_BASE_URI + "data/mcp"
    )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "TGT": TGT_TOKEN
    }
    tarih = datetime.now(tz).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )
    body = {
        "startDate": startDate,
        "endDate": tarih_formatla(tarih)
    }

    response = requests.post(
        url,
        headers=headers,
        json=body,
        timeout=30
    )

    response.raise_for_status()
    return response.json()

def post_KPTF(onceki_gun = False):
    if not TGT_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Önce TGT token alınmalı."
        )

    url = (
        EPIAS_BASE_URI + "data/interim-mcp"
    )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "TGT": TGT_TOKEN
    }
    tarih = datetime.now(tz).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    if onceki_gun:
        tarih -= timedelta(days=1)

    body = {
        "startDate": tarih_formatla(tarih)
    }

    response = requests.post(
        url,
        headers=headers,
        json=body,
        timeout=30
    )

    response.raise_for_status()
    return response.json()

@epias.post('/token')
async def tgt_token(raw: EpiasTGTToken):
    global TGT_TOKEN
    try:
        TGT_TOKEN = post_TGT_token(raw.username, raw.password)

        return RestApiEpias.ok(data=TGT_TOKEN,message="basarılı")
    except Exception as e:
        error_res = RestApiEpias.error(f"Yanlıs parametre hatası: {str(e)}", 401)
        raise HTTPException(status_code=401, detail=jsonable_encoder(error_res))

#Burası kesinleşmemiş piyasa takas fiyaının alındığı yer /kpt
@epias.post('/KPTF')
async def kptf():
    try:
        tarih = datetime.now(tz)
        data = post_KPTF()
        message = f"{tarih} tarihindeki veriler."
        if not data.get("items"):
            data = post_KPTF(onceki_gun=True)
            message = f"{tarih - timedelta(days=1)} tarihine ait eski veriler."

        return RestApiEpias.ok(data=data,message=message)

    except HTTPException:
        error_res = RestApiEpias.error(
            message="Önce TGT Token alınmalı",
            code=401
        )
        raise HTTPException(status_code=401, detail=jsonable_encoder(error_res))

    except requests.exceptions.HTTPError as e:
        error_res = RestApiEpias.error(
            message=f"EPİAŞ API Hatası: {e.response.text}",
            code=e.response.status_code
        )
        raise HTTPException(
            status_code=502,
            detail=jsonable_encoder(error_res)
        )
    except requests.exceptions.RequestException as e:
        error_res = RestApiEpias.error(
            message=f"EPİAŞ bağlantı hatası: {str(e)}",
            code=502
        )
        raise HTTPException(
            status_code=502,
            detail=jsonable_encoder(error_res)
        )

@epias.post('/PTF')
async def ptf(raw: EpiasPTF):
    try:
        data = post_PTF(raw.startDate)
        return RestApiEpias.ok(data=data,message=f"PTF: {raw.startDate}")
    except Exception as e:
        error_res = RestApiEpias.error(f"TGT Hatası: {str(e)}", 401)
        raise HTTPException(status_code=401, detail=jsonable_encoder(error_res))