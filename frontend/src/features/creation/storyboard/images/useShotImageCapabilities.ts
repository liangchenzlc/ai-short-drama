import { aiModelConfigs } from '@host/api/ai-config/management';
import { AI_CONFIGS_CHANGED } from '@host/features/ai-config/catalog/config-events';
import type { ImageCapabilities } from '@host/types/creation/storyboard/images/shot-image-workflow';
import type { MutableRefObject } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';

/** 模型变更使准备中的生成失效，避免用旧能力提交新模型请求。 */
export function useShotImageCapabilities(selection: string, preparationEpoch: MutableRefObject<number>) {
  const [resolvedImageModel, setResolvedImageModel] = useState<{
    selection: string;
    id: string | undefined;
  }>({ selection: selection, id: undefined });
  const [resolvingImageModel, setResolvingImageModel] = useState(true);
  const [capabilityRevision, setCapabilityRevision] = useState(0);
  const capabilityRequest = useRef<AbortController | null>(null);
  const [capabilityResult, setCapabilityResult] = useState<{
    modelId: string;
    revision: number;
    capabilities: ImageCapabilities | null;
  } | null>(null);
  const imageModelId = resolvedImageModel.selection === selection ? resolvedImageModel.id : undefined;
  const capabilitiesReady =
    !resolvingImageModel &&
    capabilityResult?.modelId === imageModelId &&
    capabilityResult?.revision === capabilityRevision;
  const imageCapabilities = capabilitiesReady ? capabilityResult.capabilities : null;
  const capabilitiesLoading = resolvingImageModel || (!!imageModelId && !capabilitiesReady);
  const onResolvedImageModel = useCallback(
    (id: string | undefined) => {
      capabilityRequest.current?.abort();
      setCapabilityResult(null);
      setResolvedImageModel({ selection: selection, id });
      setResolvingImageModel(false);
      setCapabilityRevision((revision) => revision + 1);
    },
    [selection],
  );
  const refreshCapabilities = useCallback(() => {
    preparationEpoch.current++;
    capabilityRequest.current?.abort();
    setCapabilityResult(null);
    setCapabilityRevision((revision) => revision + 1);
  }, []);
  useEffect(() => {
    const refresh = () => {
      refreshCapabilities();
      setResolvingImageModel(true);
    };
    window.addEventListener(AI_CONFIGS_CHANGED, refresh);
    window.addEventListener('focus', refresh);
    return () => {
      window.removeEventListener(AI_CONFIGS_CHANGED, refresh);
      window.removeEventListener('focus', refresh);
    };
  }, [refreshCapabilities]);
  useEffect(() => {
    if (!imageModelId || resolvingImageModel) return;
    const controller = new AbortController();
    capabilityRequest.current = controller;
    aiModelConfigs
      .capabilities(imageModelId, controller.signal)
      .then((capabilities) => {
        if (!controller.signal.aborted)
          setCapabilityResult({
            modelId: imageModelId,
            revision: capabilityRevision,
            capabilities,
          });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setCapabilityResult({
            modelId: imageModelId,
            revision: capabilityRevision,
            capabilities: null,
          });
      });
    return () => controller.abort();
  }, [imageModelId, resolvingImageModel, capabilityRevision]);
  return {
    imageModelId,
    imageCapabilities,
    capabilitiesLoading,
    onResolvedImageModel,
    refreshCapabilities,
  };
}
