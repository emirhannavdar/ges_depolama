from email import message
from logging import NullHandler

import requests

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import EPIAS_BASE_URI
from datetime import datetime, timezone, timedelta

epias = APIRouter()

TGT_TOKEN = None
tz = timezone(timedelta(hours=3))

def tarih_formatla(dt_obj):
    return dt_obj.isoformat(timespec='seconds')

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
    tarih = datetime.now(tz)
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
    tarih = datetime.now(tz)
    if onceki_gun:
        tarih = tarih - timedelta(days=1)

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

        return {
            "success": True,
            "Token": TGT_TOKEN,
            "message": "Token başarıyla kaydedildi."
        }
    except requests.exceptions.RequestException:
        raise HTTPException(
            status_code=502,
            detail="EPİAŞ token alınamadı."
        )

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

        return {
            "success": True,
            "data": data,
            "message": message
        }

    except HTTPException:
        raise

    except requests.exceptions.HTTPError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"EPİAŞ API hatası: HTTP {e.response.status_code}"
        )

    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=502,
        )

@epias.post('/PTF')
async def ptf(raw: EpiasPTF):
    try:
        data = post_PTF(raw.startDate)
        return {
            "success": True,
            "data": data,
            "message": "basarılı"
        }
    except HTTPException:
        raise

    except requests.exceptions.HTTPError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"EPİAŞ API hatası: HTTP {e.response.status_code}"
        )

    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=502,
        )