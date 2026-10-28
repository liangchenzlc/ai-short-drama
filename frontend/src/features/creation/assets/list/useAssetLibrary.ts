import { projectAssetsApi } from '@host/api/creation/assets/management';
import { errorMessage } from '@host/api/http';
import type { AssetKind, AssetScope, LibraryAssetRead } from '@host/types/creation/assets/management';
import { useCallback, useEffect, useState } from 'react';
export function useAssetLibrary(scope: AssetScope, kind: AssetKind | 'all', query: string, offset = 0) {
  const [items, setItems] = useState<LibraryAssetRead[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    projectAssetsApi
      .list(
        scope,
        {
          kind: kind === 'all' ? undefined : kind,
          q: query.trim() || undefined,
          offset,
          limit: 20,
        },
        controller.signal,
      )
      .then((page) => {
        if (!controller.signal.aborted) {
          setItems(page.items);
          setTotal(page.total);
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [scope.kind, scope.projectId, kind, query, offset, revision]);
  return { items, total, loading, error, refresh };
}
