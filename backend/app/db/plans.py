"""차박 계획 저장소. 기간이 겹치는 계획이 있어도 저장은 하고, 겹친 계획을 함께 돌려준다."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.database import Database

JSON_FIELDS = {"campsite": "campsite_json", "vehicle": "vehicle_json", "devices": "devices_json",
               "charge_stop": "charge_stop_json", "expected_soc": "expected_soc_json", "evidence": "evidence_json"}
EDITABLE = {"title", "start_at", "end_at", "devices", "memo", "origin"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_period(start_at: str, end_at: str) -> None:
    if datetime.fromisoformat(end_at) <= datetime.fromisoformat(start_at):
        raise ValueError("귀가 일시는 출발 일시보다 뒤여야 합니다")


class PlanRepository:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(row) -> dict[str, Any]:
        d = dict(row)
        for key, col in JSON_FIELDS.items():
            raw = d.pop(col)
            d[key] = json.loads(raw) if raw else None
        return d

    def overlaps(self, start_at: str, end_at: str, exclude_id: int | None = None) -> list[dict[str, Any]]:
        """[start, end) 구간이 겹치는 계획. 귀가 시각 = 다음 출발 시각처럼 맞닿기만 하면 겹침이 아니다."""
        with self.db.tx() as c:
            rows = c.execute("SELECT id, title, start_at, end_at FROM plans WHERE start_at < ? AND end_at > ?"
                             " AND id != ? ORDER BY start_at", (end_at, start_at, exclude_id or -1)).fetchall()
        return [dict(r) for r in rows]

    def create(self, draft: dict[str, Any], session_id: str | None = None) -> tuple[dict[str, Any], list[dict]]:
        _check_period(draft["start_at"], draft["end_at"])
        overlaps = self.overlaps(draft["start_at"], draft["end_at"])
        now = _now()
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO plans (title, campsite_json, origin, start_at, end_at, vehicle_json, devices_json,"
                " charge_stop_json, expected_soc_json, evidence_json, memo, session_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (draft["title"], json.dumps(draft["campsite"], ensure_ascii=False), draft.get("origin"),
                 draft["start_at"], draft["end_at"], json.dumps(draft["vehicle"], ensure_ascii=False),
                 json.dumps(draft.get("devices", []), ensure_ascii=False),
                 json.dumps(draft.get("charge_stop"), ensure_ascii=False) if draft.get("charge_stop") else None,
                 json.dumps(draft.get("expected_soc"), ensure_ascii=False) if draft.get("expected_soc") else None,
                 json.dumps(draft.get("evidence"), ensure_ascii=False) if draft.get("evidence") else None,
                 draft.get("memo", ""), session_id, now, now))
            plan_id = cur.lastrowid
        return self.get(plan_id), overlaps

    def latest_for_session(self, session_id: str) -> dict[str, Any] | None:
        """이 대화에서 마지막으로 저장한 계획 (캘린더 등록 대상)."""
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM plans WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        return self._row(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM plans ORDER BY start_at, id").fetchall()
        return [self._row(r) for r in rows]

    def get(self, plan_id: int) -> dict[str, Any] | None:
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return self._row(row) if row else None

    def update(self, plan_id: int, changes: dict[str, Any]) -> tuple[dict[str, Any], list[dict]] | None:
        current = self.get(plan_id)
        if current is None:
            return None
        changes = {k: v for k, v in changes.items() if k in EDITABLE and v is not None}
        start_at, end_at = changes.get("start_at", current["start_at"]), changes.get("end_at", current["end_at"])
        _check_period(start_at, end_at)
        sets, values = [], []
        for key, value in changes.items():
            if key in JSON_FIELDS:
                sets.append(f"{JSON_FIELDS[key]} = ?")
                values.append(json.dumps(value, ensure_ascii=False))
            else:
                sets.append(f"{key} = ?")
                values.append(value)
        sets.append("updated_at = ?")
        values.append(_now())
        with self.db.tx() as c:
            c.execute(f"UPDATE plans SET {', '.join(sets)} WHERE id = ?", (*values, plan_id))
        return self.get(plan_id), self.overlaps(start_at, end_at, exclude_id=plan_id)

    def delete(self, plan_id: int) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM plans WHERE id = ?", (plan_id,)).rowcount > 0
