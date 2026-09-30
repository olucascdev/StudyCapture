declare abstract class AudioWorkletProcessor {
  readonly port: MessagePort;
  abstract process(inputs: Float32Array[][]): boolean;
}

declare function registerProcessor(name: string, processor: new (...args: unknown[]) => AudioWorkletProcessor): void;
