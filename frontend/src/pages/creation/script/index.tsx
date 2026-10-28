import { ScriptWorkspace } from '@host/features/creation/writing/editor/ScriptWorkspace';
import type { WritingSession } from '@host/features/creation/writing/sessions/writing-session';
import type { CreationPreferences } from '@host/types/creation/workspace/model';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import type { WritingSnapshot } from '@host/types/creation/writing/sessions/writing-session';
import './index.css';
export function SourceStage({
  value,
  onChange,
  writing,
  readOnly,
  projectId,
  writingSession,
  accountId,
  registerBarrier,
}: {
  value: CreationPreferences;
  writing: WritingSnapshot;
  readOnly: boolean;
  onChange: (next: CreationPreferences) => void;
  projectId: string;
  writingSession: WritingSession;
  accountId?: string;
  registerBarrier?: (barrier: NavigationBarrier | null) => void;
}) {
  return (
    <ScriptWorkspace
      projectId={projectId}
      accountId={accountId}
      writing={writing}
      session={writingSession}
      readOnly={readOnly}
      registerBarrier={registerBarrier}
      model={/^\d+$/.test(value.models.script) ? value.models.script : ''}
      onModel={(model) => onChange({ ...value, models: { ...value.models, script: model } })}
    />
  );
}
