import { useEffect, useState } from 'react';
import { agentsApi } from '../../api/modules/agents';
import { errorMessage } from '../../api/http';
import type { AgentAvailability } from '../../api/types/agents';

export function useAgentAvailability() {
  const [status, setStatus] = useState<AgentAvailability | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    agentsApi.status(controller.signal).then(next => {
      if (!controller.signal.aborted) setStatus(next);
    }).catch(cause => {
      if (!controller.signal.aborted) { setStatus(null); setError(errorMessage(cause)); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision]);
  const available = !loading && !!status?.enabled && !!status?.schema_ready;
  const reason = loading ? '正在检查 Agent 创作状态…' : error ? '暂时无法检查 Agent 创作状态'
    : !status?.enabled ? 'Agent 创作暂未启用' : !status.schema_ready ? 'Agent 创作正在准备，请稍后重试' : '';
  return { status, loading, error, available, reason, refresh: () => setRevision(value => value + 1) };
}
