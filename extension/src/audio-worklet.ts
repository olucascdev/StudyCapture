/// <reference path="./audio-worklet.d.ts" />

class PcmCaptureProcessor extends AudioWorkletProcessor {
  private samples: number[] = [];
  private readIndex = 0;
  private position = 0;
  private sequence = 0;
  private stopping = false;
  private readonly blockSize = 80_000;
  constructor() {
    super();
    this.port.onmessage = (event) => { if (event.data?.type === "flush") { const remaining = this.samples.length - this.readIndex; if (remaining > 0) this.emit(remaining); this.stopping = true; this.port.postMessage({ type: "stopped", totalSamples: this.position }); } };
  }
  process(inputs: Float32Array[][]): boolean {
    const input = inputs[0]?.[0]; if (!input || this.stopping) return !this.stopping;
    for (const sample of input) this.samples.push(Math.max(-1, Math.min(1, sample)));
    while (this.samples.length - this.readIndex >= this.blockSize) this.emit(this.blockSize);
    return true;
  }
  private emit(count: number): void {
    const pcm = new ArrayBuffer(count * 2); const view = new DataView(pcm);
    for (let index = 0; index < count; index++) { const sample = this.samples[this.readIndex + index] ?? 0; view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true); }
    this.readIndex += count;
    if (this.readIndex === this.samples.length) { this.samples = []; this.readIndex = 0; }
    this.port.postMessage({ type: "block", sequence: this.sequence++, positionSamples: this.position, sampleCount: count, pcm }, [pcm]); this.position += count;
  }
}
registerProcessor("pcm-capture-processor", PcmCaptureProcessor);
