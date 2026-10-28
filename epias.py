import requests

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import EPIAS_BASE_URI

epias = APIRouter()

TGT_TOKEN = None

class EpiasTGTToken(BaseModel):
    username: str
    password: str


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


def post_KPTF():
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

    body = {
        "startDate": "2026-10-28T00:00:00+03:00",
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

@epias.post('/kpt')
async def kptf():
    try:
        data = post_KPTF()
        return {
            "success": True,
            "data": data
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