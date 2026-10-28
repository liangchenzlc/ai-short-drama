import type { Layout } from '@host/types/creation/assembly/workbench/useAssemblyLayout';
import { useEffect, useState } from 'react';
const storageKey = 'short-drama:assembly-layout:v1';
const defaults = { media: true, playerHeight: 0, trackHeight: 152 };
function read(): Layout {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) ?? '{}');
    return {
      media: typeof saved.media === 'boolean' ? saved.media : true,
      playerHeight: Number.isFinite(saved.playerHeight) ? Math.max(0, Math.min(760, saved.playerHeight)) : 0,
      trackHeight: Number.isFinite(saved.trackHeight) ? Math.max(152, Math.min(360, saved.trackHeight)) : 152,
    };
  } catch {
    return defaults;
  }
}
export function useAssemblyLayout(focused: boolean) {
  const [layout, setLayout] = useState(read);
  const [viewportHeight, setViewportHeight] = useState(() => window.innerHeight);
  useEffect(() => {
    const resize = () => setViewportHeight(window.innerHeight);
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(storageKey, JSON.stringify(layout));
      } catch {
        /* Layout stays usable in restricted storage. */
      }
    }, 150);
    return () => clearTimeout(timer);
  }, [layout]);
  const playerMax = Math.max(240, Math.min(760, Math.round(viewportHeight * 0.72)));
  const automatic = focused
    ? Math.max(240, Math.min(460, viewportHeight * 0.36))
    : Math.max(280, Math.min(380, viewportHeight * 0.36));
  return {
    layout,
    playerMax,
    playerHeight: Math.round(Math.max(200, Math.min(playerMax, layout.playerHeight || automatic))),
    update: (patch: Partial<Layout>) => setLayout((value) => ({ ...value, ...patch })),
  };
}
