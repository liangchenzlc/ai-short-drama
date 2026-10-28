export interface Preferences {
  enabled: boolean;
  ready: boolean;
  error: string;
  values: Record<string, string>;
  save: (key: string, value?: string) => Promise<void>;
}
