import type { CaptureBlock } from "./types";

let database: Promise<IDBDatabase> | undefined;
function openDatabase(): Promise<IDBDatabase> {
  if (database) return database;
  database = new Promise((resolve, reject) => {
    const request = indexedDB.open("studycapture", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("blocks", { keyPath: ["sessionId", "sequence"] });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return database;
}
export async function putBlock(block: CaptureBlock): Promise<void> { const db = await openDatabase(); await new Promise<void>((resolve, reject) => { const request = db.transaction("blocks", "readwrite").objectStore("blocks").put(block); request.onsuccess = () => resolve(); request.onerror = () => reject(request.error); }); }
export async function removeBlock(sessionId: string, sequence: number): Promise<void> { const db = await openDatabase(); await new Promise<void>((resolve, reject) => { const request = db.transaction("blocks", "readwrite").objectStore("blocks").delete([sessionId, sequence]); request.onsuccess = () => resolve(); request.onerror = () => reject(request.error); }); }
export async function blocksForSession(sessionId: string): Promise<CaptureBlock[]> { const db = await openDatabase(); return new Promise((resolve, reject) => { const request = db.transaction("blocks", "readonly").objectStore("blocks").getAll(); request.onsuccess = () => resolve((request.result as CaptureBlock[]).filter((block) => block.sessionId === sessionId).sort((a, b) => a.sequence - b.sequence)); request.onerror = () => reject(request.error); }); }
export async function backlogBytes(): Promise<number> { const db = await openDatabase(); return new Promise((resolve, reject) => { const request = db.transaction("blocks", "readonly").objectStore("blocks").getAll(); request.onsuccess = () => resolve((request.result as CaptureBlock[]).reduce((total, block) => total + block.audio.byteLength, 0)); request.onerror = () => reject(request.error); }); }
