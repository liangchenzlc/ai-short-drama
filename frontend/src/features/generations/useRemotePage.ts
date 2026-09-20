import { useEffect, useRef, useState } from 'react';
import { isCancelled } from '../../api/http';
import type { Page } from '../../api/types/generations';
import { generationError } from './presentation';

export function useRemotePage<T, Q>(query: Q, loader: (query: Q, signal?: AbortSignal) => Promise<Page<T>>, poll?: (items: T[]) => boolean) {
  const key = JSON.stringify(query);
  const [result, setResult] = useState<{ key: string; data: Page<T> } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const controllerRef = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let active = false;
    setLoading(true); setError('');
    async function fetchPage() {
      try {
        const data = await loader(JSON.parse(key) as Q, controller.signal);
        if (controller.signal.aborted) return;
        active = poll?.(data.items) ?? false;
        setResult({ key, data }); setError('');
      } catch (cause) {
        if (controller.signal.aborted || isCancelled(cause)) return;
        setError(generationError(cause));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          if (active) timer = setTimeout(fetchPage, 4000);
        }
      }
    }
    void fetchPage();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [key, loader, poll, revision]);
  return { data: result?.key === key ? result.data : null, error, loading, refresh: () => { controllerRef.current?.abort(); setRevision((value) => value + 1); } };
}
