import type { AssetPromptReference } from '@host/types/creation/assets/images/asset-prompt-references';

export type Props = {
  id?: string;
  value: string;
  references: AssetPromptReference[];
  disabled?: boolean;
  maxLength?: number;
  onChange: (value: string) => void;
};

export type Mention = { start: number; end: number; query: string };

export type Selection = { start: number; end: number };

export type Position = {
  left: number;
  top: number;
  width: number;
  maxHeight: number;
  above: boolean;
};
