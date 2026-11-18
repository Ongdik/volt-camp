// 해시 라우터 + 키 설정 배너
import { api } from "./api.js";
import { $ } from "./ui.js";
import { renderChat } from "./views/chat.js";
import { renderPlans } from "./views/plans.js";
import { renderLogs } from "./views/logs.js";

const routes = { chat: renderChat, plans: renderPlans, logs: renderLogs };

function route() {
  const name = (location.hash.replace("#/", "") || "chat").split("/")[0];
  const render = routes[name] || renderChat;
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  render($("#view"));
}

async function checkHealth() {
  const banner = $("#banner");
  try {
    const h = await api.health();
    const missing = [!h.llm && "LLM", !h.data_go_kr && "공공데이터포털", !h.kakao && "카카오"].filter(Boolean);
    banner.classList.toggle("hidden", missing.length === 0);
    banner.textContent = missing.length
      ? `API 키가 설정되지 않았어요: ${missing.join(", ")} — backend/.env를 확인하고 서버를 다시 시작해 주세요.` : "";
  } catch (e) {
    banner.classList.remove("hidden");
    banner.textContent = e.message;
  }
}

window.addEventListener("hashchange", route);
checkHealth();
route();
