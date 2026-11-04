export interface DeltaChunk {
  turn: string;
  offset: number;
  seq: number;
  text: string;
}
export interface DeltaRecovery {
  held: Record<string, DeltaChunk[]>;
  replay: Record<string, DeltaChunk[]>;
  watermark: number;
}

export interface StreamingTextPart {
  turn: string;
  seq: number;
  text: string;
}
