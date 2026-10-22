import { useCallback, useEffect, useRef, useState } from 'react';
import { imageGroups, type ImageGroup, type ImageTargetKind } from '../../api/modules/image-groups';
import { errorMessage } from '../../api/http';

export function useImageGroup(kind: ImageTargetKind, targetId: string, onArtworkChanged: () => void) {
  const [group, setGroup] = useState<ImageGroup | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  const scope = `${kind}:${targetId}`;
  const currentScope = useRef(scope); currentScope.current = scope;
  const callback = useRef(onArtworkChanged); callback.current = onArtworkChanged;
  const primary = useRef<string | null | undefined>(undefined);
  const receive = useCallback((next: ImageGroup | null) => {
    const nextPrimary = next?.items.find(item => item.output_index === next.primary_index)?.media_id ?? null;
    if (primary.current !== undefined && primary.current !== nextPrimary) callback.current();
    primary.current = nextPrimary;
    setGroup(next); setError('');
  }, []);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const ticket = ++sequence.current;
    try {
      const next = await imageGroups.current(kind, targetId, signal);
      if (!signal?.aborted && currentScope.current === scope && ticket === sequence.current) { receive(next); return next; }
    } catch (cause) {
      if (!signal?.aborted && currentScope.current === scope && ticket === sequence.current) setError(`图片读取失败：${errorMessage(cause)}`);
    } finally { if (!signal?.aborted && currentScope.current === scope && ticket === sequence.current) setLoading(false); }
    return undefined;
  }, [kind, targetId, scope, receive]);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    primary.current = undefined; setGroup(null); setLoading(true);
    async function poll() {
      const next = await refresh(controller.signal);
      if (!controller.signal.aborted) timer = setTimeout(() => void poll(), next?.status === 'generating' ? 1800 : 8000);
    }
    void poll();
    const focus = () => { void refresh(controller.signal); };
    window.addEventListener('focus', focus);
    return () => { controller.abort(); clearTimeout(timer); sequence.current++; window.removeEventListener('focus', focus); };
  }, [refresh]);
  return { group, loading, error, refresh, receive };
}
