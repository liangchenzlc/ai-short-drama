export interface AgentSubject {
  type: 'asset' | 'shot';
  id: string;
  label: string;
  revision?: string;
}
