import { useEffect, useRef } from 'react';
import { Button } from 'antd';

export function LazyLoadMore({ hasMore, loading, error, onLoad }: { hasMore: boolean; loading: boolean; error?: string; onLoad: () => void }) {
  const marker = useRef<HTMLDivElement>(null);
  const callback = useRef(onLoad); callback.current = onLoad;
  useEffect(() => {
    if (!hasMore || loading || error || !marker.current || typeof IntersectionObserver === 'undefined') return;
    const container = marker.current.closest('.lazy-scroll');
    const root = container && /auto|scroll/.test(getComputedStyle(container).overflowY) ? container : null;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) callback.current();
    }, { root, rootMargin: '80px' });
    observer.observe(marker.current);
    return () => observer.disconnect();
  }, [hasMore, loading, error]);
  if (!hasMore) return null;
  return <div ref={marker} className="lazy-load-more"><Button loading={loading} disabled={loading} onClick={onLoad}>{error ? '重试加载' : '加载更多'}</Button>{error && <p role="alert">{error}</p>}</div>;
}
