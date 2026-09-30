import "./popup.css";
import { createSession, listFolders, listSessions } from "./api";
import type { Session } from "./types";
import { formatDuration } from "./format";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const preparation = $("preparation"); const capturing = $("capturing"); const completion = $("completion");
const title = $("title") as HTMLInputElement; const folder = $("folder") as HTMLSelectElement; const language = $("language") as HTMLSelectElement;
const availability = $("availability"); const error = $("error"); const start = $("start") as HTMLButtonElement; const finish = $("finish") as HTMLButtonElement;
let active: { id: string; startedAt: number } | undefined; let timer: number | undefined; let poll: number | undefined;
function setError(message = "") { error.textContent = message; }
function show(state: "preparation" | "capturing" | "completion") { preparation.classList.toggle("hidden", state !== "preparation"); capturing.classList.toggle("hidden", state !== "capturing"); completion.classList.toggle("hidden", state !== "completion"); }
function escapeHtml(value: string) { return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[char] ?? char); }
function renderHistory(sessions: Session[]) { $("history-list").innerHTML = sessions.length ? sessions.slice(0, 5).map((session) => `<div class="history-item"><strong>${escapeHtml(session.title)}</strong><span>${session.status === "concluída" ? "salva" : session.status}</span></div>`).join("") : '<p class="muted">Nenhuma captura ainda.</p>'; }
async function loadFolderTree(parent = "", depth = 0): Promise<void> {
  if (depth > 7) return;
  const result = await listFolders(parent);
  for (const name of result.folders) {
    const path = parent ? `${parent}/${name}` : name;
    folder.add(new Option(`${"　".repeat(depth)}${name}`, path));
    await loadFolderTree(path, depth + 1);
  }
}
function monitorCapture() {
  if (timer) clearInterval(timer); if (poll) clearInterval(poll);
  timer = window.setInterval(() => { if (active) $("duration").textContent = formatDuration((Date.now() - active.startedAt) / 1000); }, 500);
  poll = window.setInterval(async () => { if (!active) return; const session = await chrome.runtime.sendMessage({ type: "get-session", sessionId: active.id }); if (session?.ok === false) return; $("blocks").textContent = String(session.block_count ?? 0); $("pending").textContent = String(Math.max(0, Math.round((Date.now() - active.startedAt) / 5000 - (session.block_count ?? 0)))); $("meter-fill").style.width = `${Math.min(95, 12 + (session.completed_windows ?? 0) * 8)}%`; }, 2000);
}
async function boot() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true }); title.value = tab?.title ?? "";
  try {
    const result = await chrome.runtime.sendMessage({ type: "health" }); if (!result.ok && result.error) throw new Error(result.error);
    $("signal").classList.add("good"); availability.textContent = result.groq ? "Servidor pronto · Groq configurada" : "Servidor online · falta configurar Groq"; start.disabled = !result.groq;
    await loadFolderTree();
    const sessions = (await listSessions()).sessions; renderHistory(sessions);
    const running = sessions.find((session) => session.status === "capturando");
    if (running) { active = { id: running.id, startedAt: running.created_at ? Date.parse(running.created_at) : Date.now() }; $("capture-title").textContent = running.title; show("capturing"); monitorCapture(); }
  } catch (caught) { $("signal").classList.add("bad"); availability.textContent = "Servidor indisponível"; setError(caught instanceof Error ? caught.message : "Verifique as configurações"); }
}

start.onclick = async () => { setError(""); start.disabled = true; try {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true }); if (!tab?.id) throw new Error("Não foi possível identificar a aba");
  const created = await createSession({ title: title.value, url: tab.url ?? "https://unknown.invalid", folder: folder.value, language: language.value || null }); const response = await chrome.runtime.sendMessage({ type: "start-capture", sessionId: created.id, tabId: tab.id }); if (!response.ok) throw new Error(response.error);
  active = { id: created.id, startedAt: Date.now() }; $("capture-title").textContent = title.value; show("capturing"); monitorCapture();
} catch (caught) { setError(caught instanceof Error ? caught.message : "Não foi possível começar"); start.disabled = false; } };

finish.onclick = async () => { if (!active) return; finish.disabled = true; try { const response = await chrome.runtime.sendMessage({ type: "stop-capture", sessionId: active.id }); if (!response?.ok) throw new Error(response?.error ?? "Não foi possível parar a captura"); if (timer) clearInterval(timer); if (poll) clearInterval(poll); show("completion"); await waitForCompletion(active.id); } catch (caught) { setError(caught instanceof Error ? caught.message : "Não foi possível finalizar"); finish.disabled = false; } };
async function waitForCompletion(id: string) { $("completion-title").textContent = "Processando transcrição"; for (let attempt = 0; attempt < 180; attempt++) { await new Promise((resolve) => setTimeout(resolve, 2000)); const session = await chrome.runtime.sendMessage({ type: "get-session", sessionId: id }); if (session.status === "concluída") { $("completion-title").textContent = "Nota salva no vault"; $("completion-copy").textContent = "A transcrição bruta está pronta no Obsidian."; $("note-path").textContent = session.note_path ?? ""; $("note-path").classList.remove("hidden"); $("open-note").classList.remove("hidden"); $("open-note").onclick = () => { if (session.note_path) window.open(`obsidian://open?file=${encodeURIComponent(session.note_path)}`, "_blank"); }; return; } if (session.transcription_status === "falhou") { $("completion-title").textContent = "Transcrição aguardando"; $("completion-copy").textContent = session.error ?? "A captura foi preservada. Tente novamente após corrigir a configuração."; return; } } }
$("new-capture").onclick = () => { active = undefined; start.disabled = false; show("preparation"); void boot(); }; $("settings").onclick = () => chrome.runtime.openOptionsPage(); void boot();
