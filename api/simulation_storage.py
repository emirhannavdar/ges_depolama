"""Optional PostgreSQL persistence for the virtual EMS simulator.

Configure SIMULATION_DATABASE_URL (postgresql://...). No sample/live prices
are generated. This only saves supplied scenario metadata and simulated data.
"""
from __future__ import annotations

import json
import os
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS simulation_sessions (
  session_id UUID PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  state TEXT NOT NULL,
  simulated_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  speed DOUBLE PRECISION NOT NULL,
  step_seconds DOUBLE PRECISION NOT NULL,
  source TEXT NOT NULL,
  battery JSONB NOT NULL,
  optimization JSONB NOT NULL,
  totals JSONB NOT NULL DEFAULT '{}'::jsonb,
  error TEXT
);
CREATE TABLE IF NOT EXISTS simulation_measurements (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id UUID NOT NULL REFERENCES simulation_sessions(session_id),
  simulated_at TIMESTAMPTZ NOT NULL,
  slice_seconds DOUBLE PRECISION NOT NULL,
  measurement JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_simulation_measurements_session_time
  ON simulation_measurements(session_id, simulated_at);
"""


class SimulationStorage:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn if dsn is not None else os.getenv('SIMULATION_DATABASE_URL', '').strip()
        self._psycopg = None
        if self.dsn:
            try:
                import psycopg
            except ImportError as exc:
                raise RuntimeError('PostgreSQL için pip install "psycopg[binary]" çalıştırın') from exc
            self._psycopg = psycopg
            with psycopg.connect(self.dsn) as conn:
                conn.execute(SCHEMA)

    @property
    def enabled(self) -> bool:
        return bool(self.dsn)

    def _connection(self):
        if not self.enabled:
            raise RuntimeError('SIMULATION_DATABASE_URL tanımlanmamış')
        return self._psycopg.connect(self.dsn)

    def create_session(self, session_id: str, snapshot: dict[str, Any], battery: dict, optimization: dict):
        if not self.enabled:
            return
        with self._connection() as conn:
            conn.execute('''
                INSERT INTO simulation_sessions
                (session_id, state, simulated_at, end_at, speed, step_seconds, source, battery, optimization, totals, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
            ''', (session_id, snapshot['state'], snapshot['simulated_at'], snapshot['end_at'],
                  snapshot['speed'], snapshot['step_seconds'], snapshot['source'],
                  json.dumps(battery), json.dumps(optimization), json.dumps(snapshot['totals']), snapshot['error']))

    def commit(self, session_id: str, records: list[dict], snapshot: dict[str, Any]):
        if not self.enabled:
            return
        with self._connection() as conn:
            if records:
                with conn.cursor() as cursor:
                    cursor.executemany('''
                        INSERT INTO simulation_measurements (session_id, simulated_at, slice_seconds, measurement)
                        VALUES (%s, %s, %s, %s::jsonb)
                    ''', [(session_id, record['simulated_at'], record['slice_seconds'], json.dumps(record))
                          for record in records])
            conn.execute('''
                UPDATE simulation_sessions SET updated_at=now(), state=%s, simulated_at=%s,
                       speed=%s, step_seconds=%s, totals=%s::jsonb, error=%s
                WHERE session_id=%s
            ''', (snapshot['state'], snapshot['simulated_at'], snapshot['speed'],
                  snapshot['step_seconds'], json.dumps(snapshot['totals']), snapshot['error'], session_id))

    def history(self, session_id: str, limit: int) -> list[dict]:
        with self._connection() as conn:
            rows = conn.execute('''
                SELECT measurement FROM (
                    SELECT id, measurement FROM simulation_measurements
                    WHERE session_id=%s ORDER BY id DESC LIMIT %s
                ) recent ORDER BY id ASC
            ''', (session_id, limit)).fetchall()
        return [row[0] for row in rows]

    def sessions(self, limit: int = 20) -> list[dict]:
        with self._connection() as conn:
            rows = conn.execute('''
                SELECT session_id::text, started_at, updated_at, state, simulated_at, end_at, totals, error
                FROM simulation_sessions ORDER BY started_at DESC LIMIT %s
            ''', (limit,)).fetchall()
        return [dict(session_id=r[0], started_at=r[1].isoformat(), updated_at=r[2].isoformat(),
                     state=r[3], simulated_at=r[4].isoformat(), end_at=r[5].isoformat(),
                     totals=r[6], error=r[7]) for r in rows]
