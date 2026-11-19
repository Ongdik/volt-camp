// 백엔드 API 호출. 오류는 {error: {code, message}}의 message만 화면에 쓴다 (상세 · 키 미노출).
// 기본은 같은 출처(serve.py가 /api를 백엔드로 넘긴다). 다른 주소를 쓰려면 window.VOLTCAMP_API 지정
export const API_BASE = (window.VOLTCAMP_API ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(code, message, status) { super(message); this.code = code; this.status = status; }
}

async function request(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("NETWORK", "백엔드 서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요.", 0);
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data?.error || {};
    throw new ApiError(err.code || "HTTP_" + res.status, err.message || "요청을 처리하지 못했어요.", res.status);
  }
  return data;
}

export const api = {
  health: () => request("/api/health"),
  chat: (message, sessionId) => request("/api/chat", { method: "POST", body: { message, session_id: sessionId } }),
  plans: () => request("/api/plans"),
  plan: (id) => request(`/api/plans/${id}`),
  createPlan: (draft) => request("/api/plans", { method: "POST", body: draft }),
  updatePlan: (id, changes) => request(`/api/plans/${id}`, { method: "PUT", body: changes }),
  deletePlan: (id) => request(`/api/plans/${id}`, { method: "DELETE" }),
  planCalendar: (id) => request(`/api/plans/${id}/calendar`),
  registerCalendar: (id) => request(`/api/plans/${id}/calendar`, { method: "POST" }),
  calendarEvents: (start, end) => request(`/api/calendar/events?start_at=${encodeURIComponent(start)}&end_at=${encodeURIComponent(end)}`),
  logs: () => request("/api/logs"),
  log: (sessionId) => request(`/api/logs/${encodeURIComponent(sessionId)}`),
  anomalies: (sessionId) => request(`/api/logs/${encodeURIComponent(sessionId)}/anomalies`),
  deleteLog: (sessionId) => request(`/api/logs/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
};
