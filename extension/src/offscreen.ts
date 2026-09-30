function pcmToWav(pcm: ArrayBuffer, sampleRate = 16_000): ArrayBuffer {
  const output = new ArrayBuffer(44 + pcm.byteLength); const view = new DataView(output);
  const write = (offset: number, value: string) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  write(0, "RIFF"); view.setUint32(4, 36 + pcm.byteLength, true); write(8, "WAVE"); write(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, "data"); view.setUint32(40, pcm.byteLength, true); new Uint8Array(output, 44).set(new Uint8Array(pcm)); return output;
}
async function checksum(data: ArrayBuffer): Promise<string> { const digest = await crypto.subtle.digest("SHA-256", data); return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join(""); }
function arrayBufferToBase64(data: ArrayBuffer): string { const bytes = new Uint8Array(data); let binary = ""; for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + 0x8000, bytes.length))); return btoa(binary); }

let captureContext: AudioContext | undefined; let playbackContext: AudioContext | undefined; let source: MediaStreamAudioSourceNode | undefined; let recorder: AudioWorkletNode | undefined; let stream: MediaStream | undefined; let activeSession = "";
let pendingBlockSends: Promise<void> = Promise.resolve();

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "offscreen-start") { void start(message.streamId, message.sessionId).then(() => sendResponse({ ok: true })).catch((error) => sendResponse({ ok: false, error: String(error) })); return true; }
  if (message.type === "offscreen-stop") {
    recorder?.port.postMessage({ type: "flush" });
    void new Promise((resolve) => setTimeout(resolve, 250)).then(async () => {
      await pendingBlockSends;
      stream?.getTracks().forEach((track) => track.stop()); await captureContext?.close(); await playbackContext?.close(); sendResponse({ ok: true });
    });
    return true;
  }
  return false;
});

async function start(streamId: string, sessionId: string): Promise<void> {
  activeSession = sessionId;
  pendingBlockSends = Promise.resolve();
  stream = await navigator.mediaDevices.getUserMedia({ audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } } } as MediaStreamConstraints);
  captureContext = new AudioContext({ sampleRate: 16_000 }); playbackContext = new AudioContext();
  await captureContext.audioWorklet.addModule(chrome.runtime.getURL("audio-worklet.js"));
  source = captureContext.createMediaStreamSource(stream); recorder = new AudioWorkletNode(captureContext, "pcm-capture-processor");
  const playbackSource = playbackContext.createMediaStreamSource(stream); playbackSource.connect(playbackContext.destination);
  const silentOutput = captureContext.createGain(); silentOutput.gain.value = 0; silentOutput.connect(captureContext.destination);
  source.connect(recorder); recorder.connect(silentOutput);
  recorder.port.onmessage = (event) => {
    if (event.data.type === "block") {
      const block = pendingBlockSends.then(async () => {
        const audio = pcmToWav(event.data.pcm);
        await chrome.runtime.sendMessage({ type: "capture-block", sessionId: activeSession, sequence: event.data.sequence, positionSamples: event.data.positionSamples, sampleCount: event.data.sampleCount, checksum: await checksum(audio), audioBase64: arrayBufferToBase64(audio) });
      });
      pendingBlockSends = block.catch(() => undefined);
    }
  };
  await captureContext.resume(); await playbackContext.resume();
}
