// 계획 관리 화면: 목록(겹침 표시) · 상세 · 수정 · 삭제
import { api } from "../api.js";
import { $, esc, fmtDate, pct, toast } from "../ui.js";

let selected = null;

function overlapIds(plans) {
  const ids = new Set();
  for (const a of plans) for (const b of plans) {
    if (a.id !== b.id && a.start_at < b.end_at && a.end_at > b.start_at) ids.add(a.id);
  }
  return ids;
}

export async function renderPlans(root) {
  root.innerHTML = `<div class="page"><h2>계획 관리</h2><div class="grid2"><div class="list" id="plan-list"><div class="empty">불러오는 중…</div></div><div class="panel" id="plan-detail"><div class="empty">왼쪽에서 계획을 고르세요.</div></div></div></div>`;
  let plans;
  try { plans = await api.plans(); } catch (e) { $("#plan-list").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const overlaps = overlapIds(plans);
  $("#plan-list").innerHTML = plans.length ? plans.map((p) => `
    <div class="row ${p.id === selected ? "active" : ""}" data-id="${p.id}">
      <b>${esc(p.title)}</b>${overlaps.has(p.id) ? '<span class="tag warn">기간 겹침</span>' : ""}
      <div class="when">${fmtDate(p.start_at)} ~ ${fmtDate(p.end_at)}</div>
    </div>`).join("") : '<div class="empty">저장된 계획이 없어요. 대화에서 계획을 세우고 저장해 보세요.</div>';
  $("#plan-list").onclick = (ev) => {
    const row = ev.target.closest(".row"); if (!row) return;
    selected = Number(row.dataset.id); renderPlans(root);
  };
  const plan = plans.find((p) => p.id === selected);
  if (plan) detail(root, plan, overlaps.has(plan.id));
}

function detail(root, p, overlapping) {
  const soc = p.expected_soc || {};
  const cs = p.charge_stop;
  $("#plan-detail").innerHTML = `
    <h3 style="margin-top:0">${esc(p.title)} ${overlapping ? '<span class="tag warn">다른 계획과 기간이 겹쳐요</span>' : ""}</h3>
    <div class="sub">${esc(p.campsite?.name)} · ${esc(p.campsite?.address || "")} · 출발지 ${esc(p.origin || "-")}</div>
    <div class="soc-legend" style="margin:12px 0"><span>출발<b>${esc(p.vehicle?.soc_start_pct)}%</b></span><span>도착<b>${pct(soc.arrive)}</b></span><span>캠핑 후<b>${pct(soc.after_camp)}</b></span><span>귀가<b>${pct(soc.return)}</b></span></div>
    ${cs ? `<div class="charge">🔌 ${esc(cs.station?.name)} · ${cs.charge_kwh}kWh · 약 ${cs.charge_minutes}분${cs.latest_arrival ? ` · 늦어도 ${cs.latest_arrival} 도착` : ""}</div>` : ""}
    <div class="sub" style="margin-top:8px">기기: ${(p.devices || []).map((d) => `${esc(d.name)} ${d.hours}시간`).join(", ") || "-"}</div>
    <div class="card" id="cal-box"><div class="sub">캘린더 등록 상태 확인 중…</div></div>
    <form class="form-grid" id="edit">
      <div class="full"><label>제목</label><input name="title" value="${esc(p.title)}" /></div>
      <div><label>출발</label><input type="datetime-local" name="start_at" value="${p.start_at.slice(0, 16)}" /></div>
      <div><label>귀가</label><input type="datetime-local" name="end_at" value="${p.end_at.slice(0, 16)}" /></div>
      <div class="full"><label>메모</label><textarea name="memo" rows="3">${esc(p.memo || "")}</textarea></div>
    </form>
    <div class="actions"><button class="danger" id="del">삭제</button><button class="primary" id="save">수정 저장</button></div>
    <div class="notice">생성 ${fmtDate(p.created_at)} · 수정 ${fmtDate(p.updated_at)} · 캠핑장 · 예상 잔량은 계산 결과라 여기서 고치지 않고, 대화에서 조건을 바꿔 다시 계산해요.</div>`;
  calendarBox(p);
  $("#save").onclick = async () => {
    const f = new FormData($("#edit"));
    try {
      const { overlaps } = await api.updatePlan(p.id, { title: f.get("title"), start_at: f.get("start_at"), end_at: f.get("end_at"), memo: f.get("memo") });
      toast(overlaps.length ? `수정했어요 (기간 겹침 ${overlaps.length}건)` : "수정했어요", overlaps.length ? "" : "ok");
      renderPlans(root);
    } catch (e) { toast(e.message, "bad"); }
  };
  $("#del").onclick = async () => {
    try { await api.deletePlan(p.id); selected = null; toast("삭제했어요", "ok"); renderPlans(root); }
    catch (e) { toast(e.message, "bad"); }
  };
}

const PROVIDER = { local: "VoltCamp 캘린더", google: "Google 캘린더" };

async function calendarBox(p) {
  const box = $("#cal-box");
  if (!box) return;
  let st;
  try { st = await api.planCalendar(p.id); } catch (e) { box.innerHTML = `<div class="sub">${esc(e.message)}</div>`; return; }
  const where = PROVIDER[st.provider] || st.provider;
  if (st.registered === null) {
    box.innerHTML = `<b>📅 ${esc(where)}</b><div class="sub">등록 여부를 확인하지 못했어요: ${esc(st.error)}</div>`;
    return;
  }
  if (st.registered) {
    const ev = st.event;
    const moved = ev.start_at !== p.start_at.slice(0, 16) || ev.end_at !== p.end_at.slice(0, 16);
    box.innerHTML = `<b>📅 ${esc(where)}에 등록됨</b> <span class="tag ok">등록</span>`
      + (moved ? ' <span class="tag warn">계획 기간이 바뀌어 일정과 달라요</span>' : "")
      + `<div class="sub">${esc(ev.title)} · ${fmtDate(ev.start_at)} ~ ${fmtDate(ev.end_at)}</div>`;
    return;
  }
  box.innerHTML = `<b>📅 ${esc(where)}</b><div class="sub">아직 등록하지 않았어요. 제목과 기간은 이 계획에 저장된 값으로 등록돼요.</div>`
    + '<div class="actions"><button class="primary" id="cal-reg">캘린더에 등록</button></div>';
  $("#cal-reg").onclick = async () => {
    $("#cal-reg").disabled = true;
    try {
      const r = await api.registerCalendar(p.id);
      toast(r.already_registered ? "이미 등록된 계획이에요" : `캘린더에 등록했어요: ${r.event.title}`, "ok");
      calendarBox(p);
    } catch (e) { toast(e.message, "bad"); $("#cal-reg").disabled = false; }
  };
}
