import { createSession, finishSession, getSession, health, listSessions, uploadBlock } from "./api";
import { backlogBytes, blocksForSession, putBlock, removeBlock } from "./capture-db";
import type { CaptureBlock } from "./types";

const MAX_BACKLOG = 512 * 1024 * 1024;
let offscreenReady: Promise<void> | undefined;
const captureState = new Map<string, { lastSequence: number; totalSamples: number }>();

async function configureSidePanel(): Promise<void> {
  if (!chrome.sidePanel) return;
  try {
    await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  } catch {
    // Navegadores Chromium antigos podem não implementar este comportamento.
  }
}

function base64ToArrayBuffer(value: string): ArrayBuffer {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
  return bytes.buffer;
}

async function ensureOffscreen(): Promise<void> {
  if (await chrome.offscreen.hasDocument()) return;
  if (!offscreenReady) {
    offscreenReady = chrome.offscreen.createDocument({ url: "offscreen.html", reasons: [chrome.offscreen.Reason.USER_MEDIA, chrome.offscreen.Reason.AUDIO_PLAYBACK], justification: "Capturar PCM da aba e manter o áudio audível durante a captura" }).finally(() => { offscreenReady = undefined; });
  }
  await offscreenReady;
}

async function sendBlock(block: CaptureBlock): Promise<void> {
  await putBlock(block);
  if (await backlogBytes() > MAX_BACKLOG) throw new Error("O backlog local excedeu 512 MiB");
  try { await uploadBlock(block); await removeBlock(block.sessionId, block.sequence); } catch { /* Persistido; o alarme tentará novamente. */ }
}

async function flushSession(sessionId: string): Promise<{ ok: boolean; error?: string }> {
  const blocks = await blocksForSession(sessionId);
  for (const block of blocks) {
    try { await uploadBlock(block); await removeBlock(block.sessionId, block.sequence); }
    catch { return { ok: false, error: "Não foi possível enviar todos os blocos ao servidor." }; }
  }
  const state = captureState.get(sessionId) ?? { lastSequence: -1, totalSamples: 0 };
  const serverSession = await getSession(sessionId).catch(() => undefined);
  if (serverSession?.last_sequence != null) state.lastSequence = Math.max(state.lastSequence, serverSession.last_sequence);
  if (serverSession?.total_samples) state.totalSamples = Math.max(state.totalSamples, serverSession.total_samples);
  if (blocks.length) {
    state.lastSequence = Math.max(state.lastSequence, blocks[blocks.length - 1].sequence);
    state.totalSamples = Math.max(state.totalSamples, ...blocks.map((block) => block.positionSamples + block.sampleCount));
  }
  captureState.set(sessionId, state);
  if (state.lastSequence < 0 || state.totalSamples <= 0) return { ok: false, error: "Nenhum bloco de áudio foi capturado. Verifique se a aba está reproduzindo áudio e tente novamente." };
  try { await finishSession(sessionId, state.lastSequence, state.totalSamples); }
  catch { return { ok: false, error: "O servidor rejeitou a finalização. A captura foi preservada para nova tentativa." }; }
  return { ok: true };
}

chrome.runtime.onInstalled.addListener(() => {
  void configureSidePanel();
  void chrome.alarms.create("retry-backlog", { periodInMinutes: 0.5 });
});
chrome.runtime.onStartup.addListener(() => { void configureSidePanel(); });
void configureSidePanel();
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "retry-backlog") return;
  const sessions = await listSessions().catch(() => ({ sessions: [] }));
  for (const session of sessions.sessions.filter((item) => ["capturando", "finalizando"].includes(item.status))) await flushSession(session.id);
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  void (async () => {
    try {
      if (message.type === "health") return sendResponse(await health());
      if (message.type === "create-session") return sendResponse(await createSession(message.payload));
      if (message.type === "start-capture") {
        await ensureOffscreen();
        const streamId = await new Promise<string>((resolve, reject) => chrome.tabCapture.getMediaStreamId({ targetTabId: message.tabId }, (id) => chrome.runtime.lastError ? reject(new Error(chrome.runtime.lastError.message)) : resolve(id)));
        captureState.set(message.sessionId, { lastSequence: -1, totalSamples: 0 });
        const response = await chrome.runtime.sendMessage({ type: "offscreen-start", streamId, sessionId: message.sessionId });
        if (!response?.ok) throw new Error(response?.error ?? "Não foi possível iniciar o áudio");
        return sendResponse({ ok: true });
      }
      if (message.type === "capture-block") {
        const block: CaptureBlock = { ...message, audio: base64ToArrayBuffer(message.audioBase64), storedAt: Date.now() };
        const current = captureState.get(block.sessionId) ?? { lastSequence: -1, totalSamples: 0 };
        current.lastSequence = Math.max(current.lastSequence, block.sequence); current.totalSamples = Math.max(current.totalSamples, block.positionSamples + block.sampleCount); captureState.set(block.sessionId, current);
        await sendBlock(block); return sendResponse({ ok: true });
      }
      if (message.type === "capture-stopped") {
        const state = captureState.get(message.sessionId) ?? { lastSequence: -1, totalSamples: message.totalSamples };
        state.totalSamples = Math.max(state.totalSamples, message.totalSamples); captureState.set(message.sessionId, state); return sendResponse(await flushSession(message.sessionId));
      }
      if (message.type === "stop-capture") { await ensureOffscreen(); const response = await chrome.runtime.sendMessage({ type: "offscreen-stop", sessionId: message.sessionId }); return sendResponse(response); }
      if (message.type === "get-session") return sendResponse(await getSession(message.sessionId));
      if (message.type === "retry-session") return sendResponse(await import("./api").then(({ retrySession }) => retrySession(message.sessionId)));
      return sendResponse({ ok: false, error: "Mensagem desconhecida" });
    } catch (caught) { sendResponse({ ok: false, error: caught instanceof Error ? caught.message : "Erro desconhecido" }); }
  })();
  return true;
});
