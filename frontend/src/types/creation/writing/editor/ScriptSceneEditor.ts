import type { ScriptSelection } from '@host/types/creation/writing/rewrite/writing-requests';

export type Highlight = Pick<ScriptSelection, 'start' | 'end' | 'source'>;
