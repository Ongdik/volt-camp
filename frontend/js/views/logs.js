// 실행 로그 화면: 세션 목록 · 상세(읽기 전용) · 삭제
import { api } from "../api.js";
import { $, esc, fmtDate, toast } from "../ui.js";
import { renderTimeline } from "./chat.js";

let selected = null;

export async function renderLogs(root) {
  root.innerHTML = `<div class="page"><h2>실행 로그</h2><div class="grid2"><div class="list" id="log-list"><div class="empty">불러오는 중…</div></div><div class="panel" id="log-detail"><div class="empty">세션을 고르면 이벤트를 발생 순서대로 보여 줘요. 실행 로그는 고칠 수 없어요.</div></div></div></div>`;
  let sessions;
  try { sessions = await api.logs(); } catch (e) { $("#log-list").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  $("#log-list").innerHTML = sessions.length ? sessions.map((s) => `
    <div class="row ${s.session_id === selected ? "active" : ""}" data-id="${esc(s.session_id)}">
      <b>${esc(s.session_id.slice(0, 12))}</b>
      ${s.replans ? `<span class="tag warn">재계획 ${s.replans}</span>` : ""}${s.errors ? `<span class="tag bad">오류 ${s.errors}</span>` : ""}
      <div class="when">${fmtDate(s.started_at)} · 턴 ${s.turns} · 이벤트 ${s.events} · Tool ${s.tool_calls}</div>
    </div>`).join("") : '<div class="empty">아직 실행 로그가 없어요.</div>';
  $("#log-list").onclick = (ev) => {
    const row = ev.target.closest(".row"); if (!row) return;
    selected = row.dataset.id; renderLogs(root);
  };
  if (selected) detail(root, selected);
}

async function detail(root, sessionId) {
  const box = $("#log-detail");
  try {
    const events = await api.log(sessionId);
    box.innerHTML = `<div class="actions" style="margin-top:0"><button class="danger" id="del-log">이 세션 로그 삭제</button></div>
      <h4>흐름</h4><div class="timeline" id="log-timeline"></div>
      <details><summary>원본 이벤트 ${events.length}건</summary><table><thead><tr><th>#</th><th>시각</th><th>종류</th><th>단계</th><th>내용</th></tr></thead><tbody>
      ${events.map((e) => `<tr><td>${e.seq}</td><td>${fmtDate(e.ts)}</td><td>${e.type}</td><td>${e.node || ""}</td><td class="payload">${esc(JSON.stringify(e.payload))}</td></tr>`).join("")}
      </tbody></table></details>`;
    renderTimeline(events, $("#log-timeline"));
    $("#del-log").onclick = async () => {
      try { await api.deleteLog(sessionId); selected = null; toast("로그를 삭제했어요", "ok"); renderLogs(root); }
      catch (e) { toast(e.message, "bad"); }
    };
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
