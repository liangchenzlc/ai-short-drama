import { projectAssetsApi } from '@host/api/creation/assets/management';
import { errorMessage } from '@host/api/http';
import type { LibraryAssetRead } from '@host/types/creation/assets/management';
import { useEffect, useState } from 'react';
async function loadAllAssets(projectId: string, signal: AbortSignal) {
  const scope = { kind: 'project' as const, projectId };
  const items: LibraryAssetRead[] = [];
  let offset = 0;
  let total = 1;
  while (offset < total) {
    const page = await projectAssetsApi.list(scope, { offset, limit: 100 }, signal);
    items.push(...page.items);
    total = page.total;
    offset += page.items.length;
    if (!page.items.length) break;
  }
  return items;
}

export function useStoryboardAssets(projectId: string, refreshToken: number) {
  const [assets, setAssets] = useState<LibraryAssetRead[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(true);
  const [assetsError, setAssetsError] = useState('');
  const [assetsRevision, setAssetsRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setAssetsLoading(true);
    setAssetsError('');
    loadAllAssets(projectId, controller.signal)
      .then((library) => {
        if (!controller.signal.aborted) setAssets(library);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setAssetsError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setAssetsLoading(false);
      });
    return () => controller.abort();
  }, [projectId, assetsRevision, refreshToken]);
  return {
    assets,
    setAssets,
    assetsLoading,
    setAssetsLoading,
    assetsError,
    setAssetsError,
    setAssetsRevision,
  };
}
