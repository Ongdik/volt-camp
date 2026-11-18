// 공용 화면 도구
export const $ = (sel, root = document) => root.querySelector(sel);

export function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 답변용 아주 작은 마크다운: **굵게**, "- " 목록, 줄바꿈
export function md(text) {
  const lines = esc(text).split("\n");
  let html = "", inList = false;
  for (const raw of lines) {
    const line = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    const item = line.match(/^\s*[-*•]\s+(.*)$/);
    if (item) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${item[1]}</li>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    html += line.trim() ? `<div>${line}</div>` : "<div style='height:6px'></div>";
  }
  return html + (inList ? "</ul>" : "");
}

export function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

export const pct = (v) => (v == null ? "-" : `${Math.round(v * 100)}%`);
export const fmtDate = (iso) => (iso ? iso.replace("T", " ").slice(0, 16) : "-");
