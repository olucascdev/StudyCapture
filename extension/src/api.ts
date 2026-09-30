import type { Session } from "./types";

export const API_BASE = "http://127.0.0.1:8765/api/v1";

export async function getToken(): Promise<string> {
  const result = await chrome.storage.local.get("token");
  return String(result.token ?? "");
}

export async function setToken(token: string): Promise<void> {
  await chrome.storage.local.set({ token: token.trim() });
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const headers = new Headers(init.headers);
  headers.set("X-StudyCapture-Token", token);
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail ?? `Servidor respondeu ${response.status}`);
  return data as T;
}

export function health() { return request<{ ok: boolean; configured: boolean; groq: boolean }>("/health"); }
export function listFolders(parent = "") { return request<{ parent: string; folders: string[] }>(`/folders?parent=${encodeURIComponent(parent)}`); }
export function listSessions() { return request<{ sessions: Session[] }>("/sessions"); }
export function createSession(payload: { title: string; url: string; folder: string; language: string | null }) {
  return request<{ id: string; status: string }>("/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}
export function getSession(id: string) { return request<Session>(`/sessions/${encodeURIComponent(id)}`); }
export function finishSession(id: string, lastSequence: number, totalSamples: number) {
  return request<{ id: string; status: string }>(`/sessions/${encodeURIComponent(id)}/finish`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ last_sequence: lastSequence, total_samples: totalSamples }) });
}
export function retrySession(id: string) { return request<{ id: string; status: string }>(`/sessions/${encodeURIComponent(id)}/retry`, { method: "POST" }); }
export async function uploadBlock(block: { sessionId: string; sequence: number; positionSamples: number; sampleCount: number; checksum: string; audio: ArrayBuffer }) {
  const token = await getToken();
  const response = await fetch(`${API_BASE}/sessions/${encodeURIComponent(block.sessionId)}/blocks/${block.sequence}`, { method: "PUT", body: block.audio, headers: { "X-StudyCapture-Token": token, "Content-Type": "audio/wav", "X-Position-Samples": String(block.positionSamples), "X-Sample-Count": String(block.sampleCount), "X-Checksum": block.checksum } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail ?? `Upload falhou (${response.status})`);
  return data;
}
