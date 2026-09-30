import { createSession, finishSession, getSession, health, interruptSession, listSessions, pauseSession, resumeSession, uploadBlock } from "./api";
import { backlogBytes, blocksForSession, putBlock, removeBlock } from "./capture-db";
import type { CaptureBlock } from "./types";

const MAX_BACKLOG = 512 * 1024 * 1024;
let offscreenReady: Promise<void> | undefined;
const captureState = new Map<string, { lastSequence: number; totalSamples: number }>();
const STATE_PREFIX = "capture-state:";
const FINALIZE_PREFIX = "finalize-request:";
const NOTIFIED_PREFIX = "completion-notified:";
const PAUSE_REASON_PREFIX = "pause-reason:";

type TransportState = { lastSequence: number; totalSamples: number };

async function readStoredState(sessionId: string): Promise<TransportState> {
  const stored = await chrome.storage.local.get(`${STATE_PREFIX}${sessionId}`);
  return (stored[`${STATE_PREFIX}${sessionId}`] as TransportState | undefined) ?? { lastSequence: -1, totalSamples: 0 };
}

async function saveState(sessionId: string, state: TransportState): Promise<void> {
  captureState.set(sessionId, state);
  await chrome.storage.local.set({ [`${STATE_PREFIX}${sessionId}`]: state });
}

async function hasFinalizeRequest(sessionId: string): Promise<boolean> {
  const stored = await chrome.storage.local.get(`${FINALIZE_PREFIX}${sessionId}`);
  return Boolean(stored[`${FINALIZE_PREFIX}${sessionId}`]);
}

async function saveFinalizeRequest(sessionId: string, state: TransportState): Promise<void> {
  await chrome.storage.local.set({ [`${FINALIZE_PREFIX}${sessionId}`]: state });
}

async function clearFinalizeRequest(sessionId: string): Promise<void> {
  await chrome.storage.local.remove(`${FINALIZE_PREFIX}${sessionId}`);
}

async function setPauseReason(sessionId: string, reason: "manual" | "video"): Promise<void> { await chrome.storage.local.set({ [`${PAUSE_REASON_PREFIX}${sessionId}`]: reason }); }
async function getPauseReason(sessionId: string): Promise<string | undefined> { const stored = await chrome.storage.local.get(`${PAUSE_REASON_PREFIX}${sessionId}`); return stored[`${PAUSE_REASON_PREFIX}${sessionId}`] as string | undefined; }
async function clearPauseReason(sessionId: string): Promise<void> { await chrome.storage.local.remove(`${PAUSE_REASON_PREFIX}${sessionId}`); }

function observeVideo(sessionId: string): void {
  const marker = `data-studycapture-${sessionId}`;
  const notify = (state: "paused" | "playing" | "ended") => { void chrome.runtime.sendMessage({ type: "video-state", sessionId, state }); };
  const attach = (video: HTMLVideoElement) => {
    if (video.getAttribute(marker) === "1") return;
    video.setAttribute(marker, "1");
    video.addEventListener("pause", () => notify(video.ended ? "ended" : "paused"));
    video.addEventListener("play", () => notify("playing"));
    video.addEventListener("ended", () => notify("ended"));
    if (video.paused) notify(video.ended ? "ended" : "paused");
  };
  document.querySelectorAll("video").forEach((video) => attach(video as HTMLVideoElement));
  const observer = new MutationObserver(() => document.querySelectorAll("video").forEach((video) => attach(video as HTMLVideoElement)));
  observer.observe(document.documentElement, { childList: true, subtree: true });
}

async function notifyCompleted(sessions: Array<{ id: string; status: string; title: string; note_path?: string | null }>): Promise<void> {
  const stored = await chrome.storage.local.get(null);
  for (const session of sessions.filter((item) => item.status === "concluída" && item.note_path)) {
    const key = `${NOTIFIED_PREFIX}${session.id}`;
    if (stored[key]) continue;
    try {
      await chrome.notifications.create(`studycapture-${session.id}`, { type: "basic", iconUrl: chrome.runtime.getURL("icon.svg"), title: "StudyCapture", message: `Nota salva no Obsidian: ${session.title}` });
      await chrome.storage.local.set({ [key]: true });
    } catch {
      // A captura continua concluída mesmo quando as notificações do navegador estão bloqueadas.
    }
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

async function flushSession(sessionId: string, requestFinish = false): Promise<{ ok: boolean; queued?: boolean; error?: string }> {
  const blocks = await blocksForSession(sessionId);
  const storedState = await readStoredState(sessionId);
  const state = captureState.get(sessionId) ?? storedState;
  const serverSession = await getSession(sessionId).catch(() => undefined);
  if (serverSession?.last_sequence != null) state.lastSequence = Math.max(state.lastSequence, serverSession.last_sequence);
  if (serverSession?.expected_last_sequence != null) state.lastSequence = Math.max(state.lastSequence, serverSession.expected_last_sequence);
  if (serverSession?.total_samples) state.totalSamples = Math.max(state.totalSamples, serverSession.total_samples);
  if (serverSession?.received_samples) state.totalSamples = Math.max(state.totalSamples, serverSession.received_samples);
  if (blocks.length) {
    state.lastSequence = Math.max(state.lastSequence, blocks[blocks.length - 1].sequence);
    state.totalSamples = Math.max(state.totalSamples, ...blocks.map((block) => block.positionSamples + block.sampleCount));
  }
  await saveState(sessionId, state);
  const shouldFinish = requestFinish || serverSession?.status === "finalizando" || await hasFinalizeRequest(sessionId);
  if (shouldFinish && (state.lastSequence < 0 || state.totalSamples <= 0)) return { ok: false, error: "Nenhum bloco de áudio foi capturado. Verifique se a aba está reproduzindo áudio e tente novamente." };
  if (shouldFinish) {
    await saveFinalizeRequest(sessionId, state);
    if (serverSession && serverSession.status !== "finalizando" && serverSession.status !== "concluída") {
      try { await finishSession(sessionId, state.lastSequence, state.totalSamples); await clearFinalizeRequest(sessionId); }
      catch { return { ok: true, queued: true, error: "Finalização registrada; aguardando o servidor para enviar os blocos." }; }
    } else if (serverSession?.status === "concluída") {
      await clearFinalizeRequest(sessionId);
      return { ok: true };
    }
  }
  for (const block of blocks) {
    try { await uploadBlock(block); await removeBlock(block.sessionId, block.sequence); }
    catch { return shouldFinish ? { ok: true, queued: true, error: "Finalização registrada; enviando os blocos pendentes em segundo plano." } : { ok: false, error: "Não foi possível enviar todos os blocos ao servidor." }; }
  }
  return { ok: true, queued: shouldFinish };
}

function ensureRetryAlarm(): void { void chrome.alarms.create("retry-backlog", { periodInMinutes: 0.5 }); }
chrome.runtime.onInstalled.addListener(ensureRetryAlarm);
chrome.runtime.onStartup.addListener(ensureRetryAlarm);
ensureRetryAlarm();
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "retry-backlog") return;
  const sessions = await listSessions().catch(() => ({ sessions: [] }));
  for (const session of sessions.sessions.filter((item) => ["capturando", "pausada", "finalizando"].includes(item.status))) {
    await flushSession(session.id, session.status === "finalizando" || await hasFinalizeRequest(session.id));
  }
  await notifyCompleted(sessions.sessions);
});

chrome.notifications.onClicked.addListener(async (notificationId) => {
  const sessionId = notificationId.replace("studycapture-", "");
  const session = await getSession(sessionId).catch(() => undefined);
  if (session?.note_path) await chrome.tabs.create({ url: `obsidian://open?file=${encodeURIComponent(session.note_path)}` });
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  void (async () => {
    try {
      if (message.type === "health") return sendResponse(await health());
      if (message.type === "create-session") return sendResponse(await createSession(message.payload));
      if (message.type === "start-capture") {
        try {
          await ensureOffscreen();
          const streamId = await new Promise<string>((resolve, reject) => chrome.tabCapture.getMediaStreamId({ targetTabId: message.tabId }, (id) => chrome.runtime.lastError ? reject(new Error(chrome.runtime.lastError.message)) : resolve(id)));
          captureState.set(message.sessionId, { lastSequence: -1, totalSamples: 0 });
          const response = await chrome.runtime.sendMessage({ type: "offscreen-start", streamId, sessionId: message.sessionId });
          if (!response?.ok) throw new Error(response?.error ?? "Não foi possível iniciar o áudio");
          await clearPauseReason(message.sessionId);
          await saveState(message.sessionId, { lastSequence: -1, totalSamples: 0 });
          await chrome.scripting.executeScript({ target: { tabId: message.tabId, allFrames: true }, func: observeVideo, args: [message.sessionId] }).catch(() => undefined);
          return sendResponse({ ok: true });
        } catch (caught) {
          await interruptSession(message.sessionId).catch(() => undefined);
          throw caught;
        }
      }
      if (message.type === "capture-block") {
        const block: CaptureBlock = { ...message, audio: base64ToArrayBuffer(message.audioBase64), storedAt: Date.now() };
        const current = captureState.get(block.sessionId) ?? { lastSequence: -1, totalSamples: 0 };
        current.lastSequence = Math.max(current.lastSequence, block.sequence); current.totalSamples = Math.max(current.totalSamples, block.positionSamples + block.sampleCount); captureState.set(block.sessionId, current);
        await saveState(block.sessionId, current);
        await sendBlock(block); return sendResponse({ ok: true });
      }
      if (message.type === "capture-stopped") {
        const state = captureState.get(message.sessionId) ?? { lastSequence: -1, totalSamples: message.totalSamples };
        state.totalSamples = Math.max(state.totalSamples, message.totalSamples); captureState.set(message.sessionId, state); return sendResponse(await flushSession(message.sessionId));
      }
      if (message.type === "stop-capture") {
        await ensureOffscreen();
        const response = await chrome.runtime.sendMessage({ type: "offscreen-stop", sessionId: message.sessionId });
        if (!response?.ok) return sendResponse(response ?? { ok: false, error: "Não foi possível parar a captura." });
        const result = await flushSession(message.sessionId, true); await clearPauseReason(message.sessionId); return sendResponse(result);
      }
      if (message.type === "pause-capture") {
        await ensureOffscreen();
        const response = await chrome.runtime.sendMessage({ type: "offscreen-pause" });
        if (!response?.ok) return sendResponse(response ?? { ok: false, error: "Não foi possível pausar a captura." });
        await setPauseReason(message.sessionId, "manual");
        return sendResponse(await pauseSession(message.sessionId));
      }
      if (message.type === "resume-capture") {
        await ensureOffscreen();
        const response = await chrome.runtime.sendMessage({ type: "offscreen-resume" });
        if (!response?.ok) return sendResponse(response ?? { ok: false, error: "Não foi possível retomar a captura." });
        const result = await resumeSession(message.sessionId); await clearPauseReason(message.sessionId); return sendResponse(result);
      }
      if (message.type === "video-state") {
        const session = await getSession(message.sessionId).catch(() => undefined);
        if (!session || !["capturando", "pausada"].includes(session.status)) return sendResponse({ ok: true });
        if (message.state === "paused" || message.state === "ended") {
          if (session.status === "capturando") {
            await ensureOffscreen();
            const response = await chrome.runtime.sendMessage({ type: "offscreen-pause" });
            if (response?.ok) { await setPauseReason(message.sessionId, "video"); await pauseSession(message.sessionId); }
          }
        } else if (message.state === "playing" && session.status === "pausada" && await getPauseReason(message.sessionId) === "video") {
          await ensureOffscreen();
          const response = await chrome.runtime.sendMessage({ type: "offscreen-resume" });
          if (response?.ok) { await resumeSession(message.sessionId); await clearPauseReason(message.sessionId); }
        }
        return sendResponse({ ok: true });
      }
      if (message.type === "queue-state") {
        const blocks = await blocksForSession(message.sessionId);
        return sendResponse({ pendingBlocks: blocks.length, pendingBytes: blocks.reduce((total, block) => total + block.audio.byteLength, 0), finalizeRequested: await hasFinalizeRequest(message.sessionId) });
      }
      if (message.type === "get-session") return sendResponse(await getSession(message.sessionId));
      if (message.type === "retry-session") return sendResponse(await import("./api").then(({ retrySession }) => retrySession(message.sessionId)));
      return sendResponse({ ok: false, error: "Mensagem desconhecida" });
    } catch (caught) { sendResponse({ ok: false, error: caught instanceof Error ? caught.message : "Erro desconhecido" }); }
  })();
  return true;
});
