// 대화 화면: 채팅 + 추천 카드 + 실행 과정 패널
import { api } from "../api.js";
import { $, esc, fmtDate, md, pct, toast } from "../ui.js";

const EXAMPLES = [
  "내일 서울시청에서 가평으로 1박 차박 가. 77.4kWh에 지금 80%, 전기장판 밤새 쓰고 귀가할 때 30%는 남겨야 해",
  "내일 서울시청에서 가평 1박, 77.4kWh 45%, 전기히터 6시간이랑 전기장판 8시간, 귀가 30%, 17시까지 도착",
  "가평 근처 급속 충전기 지금 비어 있어?",
  "V2L이 뭐야?",
];

const state = { sessionId: sessionStorage.getItem("voltcamp.session") || null, busy: false, items: [] };

export function renderChat(root) {
  root.innerHTML = `
    <div class="chat-layout">
      <section class="chat-col">
        <div class="messages" id="messages"></div>
        <div class="examples" id="examples"></div>
        <form class="composer" id="composer">
          <textarea id="input" placeholder="출발 날짜 · 출발지 · 캠핑 지역 · 배터리 용량과 잔량 · 쓸 기기를 알려주세요"></textarea>
          <button class="primary" id="send">보내기</button>
          <button type="button" id="reset" title="새 대화">새 대화</button>
        </form>
      </section>
      <aside class="side-col">
        <h3>실행 과정</h3>
        <div class="timeline" id="timeline"><div class="empty">질문을 보내면 Agent가 거친 단계와 Tool 호출이 순서대로 보여요.</div></div>
      </aside>
    </div>`;
  $("#examples").innerHTML = EXAMPLES.map((e, i) => `<button data-i="${i}">${esc(e.slice(0, 28))}…</button>`).join("");
  $("#examples").onclick = (ev) => { const i = ev.target.dataset.i; if (i != null) { $("#input").value = EXAMPLES[i]; $("#input").focus(); } };
  $("#composer").onsubmit = (ev) => { ev.preventDefault(); send(); };
  $("#input").onkeydown = (ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); send(); } };
  $("#reset").onclick = () => { state.sessionId = null; state.items = []; sessionStorage.removeItem("voltcamp.session"); renderChat(root); };
  state.items.forEach(paint);
  if (!state.items.length) paint({ role: "bot", html: md("안녕하세요! 전기차 배터리로 차박 계획을 세워 드릴게요. 아래 예시를 눌러 시작해 보세요.") });
}

function paint(item) {
  const box = $("#messages");
  if (!box) return;
  const el = document.createElement("div");
  el.className = `msg ${item.role}`;
  el.innerHTML = item.html;
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
  if (item.after) item.after(el);
}

function push(item) { state.items.push(item); paint(item); }

async function send() {
  const input = $("#input");
  const text = input.value.trim();
  if (!text || state.busy) return;
  input.value = "";
  push({ role: "user", html: esc(text) });
  state.busy = true; $("#send").disabled = true;
  const typing = document.createElement("div");
  typing.className = "msg bot typing"; typing.textContent = "계획을 계산하고 있어요";
  $("#messages").appendChild(typing);
  try {
    const r = await api.chat(text, state.sessionId);
    state.sessionId = r.session_id; sessionStorage.setItem("voltcamp.session", r.session_id);
    typing.remove();
    push({ role: "bot", html: renderAnswer(r), after: (el) => bindSave(el, r) });
    renderTimeline(r.timeline);
    (r.tool_failures || []).forEach((f) => toast(`${toolName(f.tool)} 조회 실패: ${f.message}`, "bad"));
    if (r.saved_plan) toast(`계획을 저장했어요: ${r.saved_plan.title}`, "ok");
  } catch (e) {
    typing.remove();
    push({ role: "bot", html: `<span class="tag bad">오류</span> ${esc(e.message)}` });
  } finally {
    state.busy = false; const b = $("#send"); if (b) b.disabled = false;
  }
}

const STATUS = { OK: ["ok", "충족"], NEED_INFO: ["info", "정보 필요"], NO_OPTION: ["warn", "대안 없음"],
                 LIMIT_REACHED: ["warn", "재계획 상한"], PARTIAL: ["warn", "일부 실패"], ERROR: ["bad", "오류"] };

function renderAnswer(r) {
  const [cls, label] = STATUS[r.status] || ["info", r.status];
  let html = md(r.answer);
  (r.recommendations || []).forEach((rec, i) => { html += recCard(rec, i === 0); });
  if (r.excluded?.length) {
    html += `<details><summary>제외한 후보 ${r.excluded.length}곳</summary><ul>${r.excluded.map((e) =>
      `<li><b>${esc(e.campsite_name)}</b> — ${esc(e.reasons.join(" / "))}</li>`).join("")}</ul></details>`;
  }
  if (r.suggestions?.length) html += `<div class="card"><b>이렇게 바꿔 보세요</b><ul>${r.suggestions.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>`;
  if (r.assumptions?.length) html += `<details><summary>계산에 쓴 가정 ${r.assumptions.length}개</summary><ul>${r.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>`;
  if (r.plan_draft && !r.saved_plan) html += `<div class="actions"><button class="primary save-plan">이 계획 저장</button></div>`;
  (r.notices || []).forEach((n) => { html += `<div class="notice">⚠️ ${esc(n)}</div>`; });
  if (r.previous_warning) {
    const w = r.previous_warning;
    html += `<div class="notice">🩺 직전 실행에서 이상 징후: ${esc(w.findings.map((f) => f.detail).join(" / "))}${w.diagnosis ? ` — ${esc(w.diagnosis)}` : ""}</div>`;
  }
  const replans = r.replans?.length ? ` · 재계획 ${r.replans.length}회` : "";
  html += `<div class="meta"><span class="tag ${cls}">${label}</span> ${esc(r.intent || "")}${replans} · ${(r.elapsed_ms / 1000).toFixed(1)}초</div>`;
  return html;
}

function recCard(rec, best) {
  const c = rec.campsite, s = rec.soc, lim = rec.limits;
  const pos = (v) => Math.max(0, Math.min(100, v * 100));
  const electric = c.has_electric === true ? '<span class="tag ok">전기 사이트</span>'
    : c.has_electric === null ? '<span class="tag warn">전기 정보 없음</span>' : "";
  const unjudged = rec.unjudged?.includes("RAIN") ? '<span class="tag warn">비 여부 판단 불가</span>' : "";
  const rain = rec.rain_expected ? '<span class="tag info">비 예보</span>' : "";
  let charge = "";
  if (rec.charge_stop) {
    const cs = rec.charge_stop;
    charge = `<div class="charge">🔌 <b>${esc(cs.station.name)}</b>에서 ${cs.charge_kwh}kWh 충전 (약 ${cs.charge_minutes}분, ${cs.station.max_output_kw}kW)`
      + (cs.latest_arrival ? ` · 늦어도 <b>${cs.latest_arrival}</b>까지 충전소 도착` : "") + "</div>";
  }
  return `<div class="card">
    <h4>${best ? "🏕️ " : ""}${esc(c.name)} ${electric}${rain}${unjudged}</h4>
    <div class="sub">${esc(c.address)} · 편도 ${rec.route.distance_km}km${rec.route.duration_min ? ` · ${rec.route.duration_min}분` : ""} · 기온 보정 ×${rec.temp_factor}</div>
    <div class="soc" title="빨간 선: V2L 방전 제한, 주황 선: 귀가 목표">
      <div class="track"></div>
      <div class="line" style="left:${pos(lim.v2l_limit)}%"></div>
      <div class="line ret" style="left:${pos(lim.min_return)}%"></div>
      ${["start", "arrive", "after_camp", "return"].map((k) => `<div class="pt" style="left:${pos(s[k])}%;opacity:${k === "return" ? 1 : 0.55}"></div>`).join("")}
    </div>
    <div class="soc-legend"><span>출발<b>${pct(s.start)}</b></span><span>도착<b>${pct(s.arrive)}</b></span><span>캠핑 후<b>${pct(s.after_camp)}</b></span><span>귀가<b>${pct(s.return)}</b></span></div>
    ${charge}
    <div class="sub" style="margin-top:6px">주행 ${rec.e_drive_oneway_kwh}kWh(편도) · 캠핑 소비 ${rec.e_camp_kwh}kWh${c.reservation_url ? ` · <a href="${esc(c.reservation_url)}" target="_blank" rel="noopener">예약</a>` : ""}</div>
  </div>`;
}

function bindSave(el, r) {
  const btn = el.querySelector(".save-plan");
  if (!btn) return;
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const { plan, overlaps } = await api.createPlan(r.plan_draft);
      toast(`저장했어요: ${plan.title}` + (overlaps.length ? ` (기간 겹침 ${overlaps.length}건)` : ""), overlaps.length ? "" : "ok");
      btn.textContent = "저장됨";
    } catch (e) { toast(e.message, "bad"); btn.disabled = false; }
  };
}

const TOOL_NAMES = { search_campsites: "캠핑장", get_route: "길찾기", get_weather: "날씨", search_chargers: "충전소" };
const toolName = (t) => TOOL_NAMES[t] || t;
const NODE_NAMES = { analyze: "요청 분석", plan: "계획 수립", execute: "Tool 실행", judge: "제약 판단", replan: "재계획", respond: "응답 생성" };

export function renderTimeline(events, target = $("#timeline")) {
  if (!target) return;
  const results = Object.fromEntries(events.filter((e) => e.type === "TOOL_RESULT").map((e) => [e.payload.step_id, e.payload]));
  const rows = [];
  for (const e of events) {
    const p = e.payload || {};
    const seq = `<span class="seq">${e.seq}</span>`;
    if (e.type === "NODE_ENTER") rows.push(`<div class="ev node">${seq}<span class="body">▶ ${NODE_NAMES[e.node] || e.node}</span></div>`);
    else if (e.type === "LLM_DECISION") {
      const decision = Array.isArray(p.decision) ? p.decision.map(toolName).join(" → ") : p.decision;
      rows.push(`<div class="ev llm">${seq}<span class="body">${p.kind === "intent" ? "의도" : "계획"}: <b>${esc(decision)}</b>${p.by === "rule" ? '<span class="chip">규칙</span>' : '<span class="chip">LLM</span>'}<span class="why">${esc(p.reason)}</span></span></div>`);
    } else if (e.type === "TOOL_CALL") {
      const res = results[p.step_id] || {};
      const fail = res.ok === false;
      rows.push(`<div class="ev tool ${fail ? "fail" : ""}">${seq}<span class="body">${toolName(p.tool)} · ${esc(p.purpose)}`
        + `<span class="chip">${fail ? esc(res.error_code) : "ok"}</span><span class="chip">${res.elapsed_ms ?? "-"}ms</span>`
        + (p.from_cache ? '<span class="chip">캐시</span>' : "") + `<span class="why">${esc(res.summary || "")}</span></span></div>`);
    } else if (e.type === "REPLAN") {
      rows.push(`<div class="replan-mark">↻ 재계획 ${p.count}회차 · ${esc(p.strategy)}<br><small>${esc(p.reason)}</small></div>`);
    } else if (e.type === "JUDGE_RESULT") {
      rows.push(`<div class="ev">${seq}<span class="body">판정: ${(p.results || []).map((r) => `${esc(r.campsite)} <span class="tag ${r.status === "OK" ? "ok" : r.status === "FAIL" ? "bad" : "warn"}">${r.status}</span>`).join(" ")}</span></div>`);
    } else if (e.type === "TURN_END") rows.push(`<div class="ev node">${seq}<span class="body">■ 완료 (${p.status}, ${p.elapsed_ms}ms)</span></div>`);
  }
  target.innerHTML = rows.join("") || '<div class="empty">이벤트 없음</div>';
}

export { fmtDate };
