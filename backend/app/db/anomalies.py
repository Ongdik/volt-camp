"""이상 징후 판정 · 진단 저장소. 판정(규칙)과 진단(LLM)은 다른 칸에 둔다."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.database import Database


class AnomalyRepository:
    def __init__(self, db: Database):
        self.db = db

    def save_verdict(self, session_id: str, turn_id: str, verdict: str, findings: list[dict[str, Any]]) -> None:
        with self.db.tx() as c:
            c.execute("INSERT OR REPLACE INTO anomalies (session_id, turn_id, verdict, findings_json, diagnosis_status,"
                      " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                      (session_id, turn_id, verdict, json.dumps(findings, ensure_ascii=False),
                       "PENDING" if verdict == "WARN" else "NONE",
                       datetime.now(timezone.utc).isoformat(timespec="seconds")))

    def save_diagnosis(self, turn_id: str, diagnosis: str | None, status: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE anomalies SET diagnosis = ?, diagnosis_status = ? WHERE turn_id = ?",
                      (diagnosis, status, turn_id))

    def for_session(self, session_id: str) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM anomalies WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["findings"] = json.loads(d.pop("findings_json"))
            out.append(d)
        return out

    def latest_warning(self, session_id: str) -> dict[str, Any] | None:
        warns = [a for a in self.for_session(session_id) if a["verdict"] == "WARN"]
        return warns[-1] if warns else None

    def delete_session(self, session_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM anomalies WHERE session_id = ?", (session_id,))
