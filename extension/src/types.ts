export type CaptureStatus = "capturando" | "finalizando" | "concluída" | "erro" | "interrompida";

export interface Session {
  id: string;
  title: string;
  url: string;
  folder: string;
  language: string | null;
  status: CaptureStatus;
  transcription_status: string;
  block_count: number;
  received_samples: number;
  completed_windows: number;
  total_samples: number;
  note_path?: string | null;
  error?: string | null;
  created_at?: string;
  last_sequence?: number | null;
}

export interface CaptureBlock {
  sessionId: string;
  sequence: number;
  positionSamples: number;
  sampleCount: number;
  checksum: string;
  audio: ArrayBuffer;
  storedAt: number;
}
