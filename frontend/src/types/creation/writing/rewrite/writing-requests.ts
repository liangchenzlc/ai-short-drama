export type WritingTarget = 'script';

export interface ScriptSelection {
  start: number;
  end: number;
  text: string;
  source: string;
  top: number;
  left: number;
  bottom?: number;
}
