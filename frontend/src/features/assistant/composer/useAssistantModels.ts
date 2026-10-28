import { assistantModelsApi } from '@host/api/ai-config/assistant/models';
import { errorMessage } from '@host/api/http';
import type { AgentModel } from '@host/types/assistant/engine';
import { useEffect, useState } from 'react';

export function useAssistantModels(initialModelId?: string) {
  const [models, setModels] = useState<AgentModel[]>([]);
  const [modelId, setModelId] = useState<string | undefined>(initialModelId);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState('');
  const [modelRevision, setModelRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setModelsLoading(true);
    setModelsError('');
    assistantModelsApi
      .models(controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        setModels(page.items);
        setModelId((old) =>
          page.items.some((item) => item.id === old) ? old : (page.preferred_id ?? page.items[0]?.id),
        );
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setModelsError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setModelsLoading(false);
      });
    return () => controller.abort();
  }, [modelRevision]);
  return { models, modelId, setModelId, modelsLoading, modelsError, setModelRevision };
}
