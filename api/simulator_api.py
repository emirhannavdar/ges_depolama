"""Thread-based single-session FastAPI control for the existing simulator.

No market prices are fetched or synthesized here. Start requires a previously
calculated optimization plan (ideally from the EPİAŞ-backed /optimize API).
One session per API process; deploy with one Uvicorn worker for this version.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.simulator import SimulatorEngine, VirtualBattery
from api.simulation_storage import SimulationStorage

router = APIRouter(prefix="/simulation", tags=["simulation"])


class StartSimulation(BaseModel):
    optimization: dict[str, Any]
    battery: dict[str, Any]
    speed: float = Field(default=3600, gt=0, le=86400, allow_inf_nan=False)
    step_seconds: float = Field(default=60, gt=0, le=3600, allow_inf_nan=False)


class SpeedUpdate(BaseModel):
    speed: float = Field(gt=0, le=86400, allow_inf_nan=False)


class DeviceLimitsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bms_charge_allowed: bool | None = None
    bms_discharge_allowed: bool | None = None
    bms_max_charge_kw: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    bms_max_discharge_kw: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    pcs_online: bool | None = None
    pcs_max_charge_kw: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    pcs_max_discharge_kw: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class SimulationController:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.wakeup = threading.Event()
        self.engine: SimulatorEngine | None = None
        self.thread: threading.Thread | None = None
        self.state = "not_started"
        self.speed = 3600.0
        self.step_seconds = 60.0
        self.error: str | None = None
        self.records: deque[dict[str, Any]] = deque(maxlen=5000)
        self.generation = 0
        self.session_id: str | None = None
        self.storage: SimulationStorage | None = None
        self.pending: list[dict[str, Any]] = []
        self.revision = 0

    def start(self, request: StartSimulation) -> dict[str, Any]:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise ValueError("Çalışan simülasyonu önce durdurun")
            engine = SimulatorEngine(
                request.optimization,
                VirtualBattery(**request.battery),
            )
            # Validate DB connectivity/schema before modifying the active session.
            storage = SimulationStorage()
            self.generation += 1
            generation = self.generation
            self.session_id = str(uuid.uuid4())
            self.storage = storage
            self.pending.clear()
            self.engine = engine
            self.speed = request.speed
            self.step_seconds = request.step_seconds
            self.records.clear()
            self.error = None
            self.state = "running"
            self.revision += 1
            self.wakeup.clear()
            # Persist the session BEFORE its worker starts.
            self.storage.create_session(self.session_id, self.snapshot(),
                                        request.battery, request.optimization)
            self.thread = threading.Thread(
                target=self._loop,
                args=(generation,),
                daemon=True,
                name="ges-simulator",
            )
            self.thread.start()
            return self.snapshot()

    def _loop(self, generation: int) -> None:
        while True:
            with self.lock:
                if generation != self.generation or self.state in ("stopped", "completed", "error"):
                    return
                if self.state == "paused":
                    delay = None
                else:
                    try:
                        assert self.engine is not None
                        if self.engine.now >= self.engine.end:
                            self.state = "completed"
                            self._persist()
                            return
                        records = self.engine.step(self.step_seconds)
                        self.records.extend(records)
                        self.pending.extend(records)
                        self.revision += 1
                        if self.engine.now >= self.engine.end:
                            self.state = "completed"
                        # Batch DB writes rather than one connection per simulated minute.
                        if len(self.pending) >= 10 or self.state == "completed":
                            self._persist()
                        if self.state == "completed":
                            return
                        delay = self.step_seconds / self.speed
                    except Exception as exc:
                        self.state = "error"
                        self.error = str(exc)
                        self.revision += 1
                        # Keep uncommitted data for diagnostics; don't silently discard.
                        try:
                            self._persist()
                        except Exception:
                            pass
                        return
            self.wakeup.wait(timeout=delay)
            self.wakeup.clear()

    def _persist(self) -> None:
        """Called only while holding the controller lock."""
        if self.storage is not None and self.session_id is not None:
            self.storage.commit(self.session_id, self.pending, self.snapshot())
        self.pending.clear()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            engine = self.engine
            return {
                "state": self.state,
                "session_id": self.session_id,
                "persistence_enabled": bool(self.storage and self.storage.enabled),
                "revision": self.revision,
                "error": self.error,
                "speed": self.speed,
                "step_seconds": self.step_seconds,
                "simulated_at": engine.now.isoformat() if engine else None,
                "end_at": engine.end.isoformat() if engine else None,
                "soc_pct": round(engine.soc, 6) if engine else None,
                "last_measurement": dict(engine.last) if engine and engine.last else None,
                "totals": {k: round(v, 6) for k, v in engine.totals.items()} if engine else None,
                "retained_records": len(self.records),
                "source": "SIMULATED_FROM_FORECAST_AND_OPTIMIZATION_PLAN",
            }

    def set_state(self, action: str) -> dict[str, Any]:
        with self.lock:
            if self.engine is None:
                raise ValueError("Henüz simülasyon başlatılmadı")
            if action == "pause" and self.state == "running":
                self.state = "paused"
            elif action == "resume" and self.state == "paused":
                self.state = "running"
            elif action == "stop" and self.state in ("running", "paused"):
                self.state = "stopped"
            else:
                raise ValueError(f"{self.state} durumunda {action} işlemi yapılamaz")
            self.revision += 1
            self._persist()
            self.wakeup.set()
            return self.snapshot()

    def set_speed(self, value: float) -> dict[str, Any]:
        with self.lock:
            if self.state not in ("running", "paused"):
                raise ValueError("Hız yalnızca aktif simülasyonda değiştirilebilir")
            self.speed = value
            self.revision += 1
            self._persist()
            self.wakeup.set()
            return self.snapshot()

    def set_device_limits(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.state not in ("running", "paused"):
                raise ValueError("Cihaz limitleri yalnızca aktif simülasyonda değiştirilebilir")
            if not values:
                raise ValueError("Değiştirilecek cihaz limiti belirtilmedi")
            assert self.engine is not None
            self.engine.update_device_limits(**values)
            self.revision += 1
            self._persist()
            return self.snapshot()

    def history(self, limit: int = 100, session_id: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            target = session_id or self.session_id
            if self.storage and self.storage.enabled and target:
                # Flush buffered measurements before reading the history.
                if target == self.session_id and self.pending:
                    self._persist()
                return self.storage.history(target, limit)
            if session_id and session_id != self.session_id:
                raise ValueError("Eski oturumlar yalnızca PostgreSQL ile okunabilir")
            return list(self.records)[-limit:]


controller = SimulationController()


def _run(action):
    try:
        return action()
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/start")
def simulation_start(request: StartSimulation):
    return _run(lambda: controller.start(request))


@router.get("/status")
def simulation_status():
    return controller.snapshot()


@router.post("/pause")
def simulation_pause():
    return _run(lambda: controller.set_state("pause"))


@router.post("/resume")
def simulation_resume():
    return _run(lambda: controller.set_state("resume"))


@router.post("/stop")
def simulation_stop():
    return _run(lambda: controller.set_state("stop"))


@router.patch("/speed")
def simulation_speed(request: SpeedUpdate):
    return _run(lambda: controller.set_speed(request.speed))


@router.patch("/device-limits")
def simulation_limits(request: DeviceLimitsUpdate):
    return _run(lambda: controller.set_device_limits(request.model_dump(exclude_unset=True)))


@router.get("/history")
def simulation_history(limit: int = 100, session_id: str | None = None):
    if not 1 <= limit <= 5000:
        raise HTTPException(status_code=422, detail="limit 1-5000 arasında olmalı")
    return _run(lambda: {"records": controller.history(limit, session_id)})


@router.get("/sessions")
def simulation_sessions(limit: int = 20):
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="limit 1-100 arasında olmalı")
    with controller.lock:
        if not controller.storage or not controller.storage.enabled:
            return {"sessions": [], "persistence_enabled": False}
        return {"sessions": controller.storage.sessions(limit), "persistence_enabled": True}


@router.websocket("/ws")
async def simulation_websocket(websocket: WebSocket):
    """Read-only dashboard stream. No browser-generated measurements."""
    await websocket.accept()
    previous = None
    try:
        while True:
            status = controller.snapshot()
            marker = (status["session_id"], status["revision"])
            if marker != previous:
                await websocket.send_json(status)
                previous = marker
            # Async polling does not block the simulation's worker thread.
            await asyncio.sleep(0.25)
    except (WebSocketDisconnect, RuntimeError):
        return
