export type Phase = 'loading' | 'ready' | 'playing' | 'buffering' | 'error' | 'refreshing' | 'restoring';

export type Playback = {
  time: number;
  volume: number;
  muted: boolean;
  rate: number;
};
