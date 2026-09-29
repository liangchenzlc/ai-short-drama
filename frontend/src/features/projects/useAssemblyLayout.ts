import { useEffect, useState } from 'react';

const storageKey = 'short-drama:assembly-layout:v1';
const defaults = { media: true, playerHeight: 0, trackHeight: 152 };
type Layout = typeof defaults;
function read(): Layout {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) ?? '{}');
    return {
      media: typeof saved.media === 'boolean' ? saved.media : true,
      playerHeight: Number.isFinite(saved.playerHeight) ? Math.max(0, Math.min(760, saved.playerHeight)) : 0,
      trackHeight: Number.isFinite(saved.trackHeight) ? Math.max(152, Math.min(360, saved.trackHeight)) : 152,
    };
  } catch { return defaults; }
}
export function useAssemblyLayout(focused: boolean) {
  const [layout, setLayout] = useState(read);
  const [viewport, setViewport] = useState(() => ({ width: window.innerWidth, height: window.innerHeight }));
  useEffect(() => {
    const resize = () => setViewport({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => {
      try { localStorage.setItem(storageKey, JSON.stringify(layout)); } catch { /* Layout stays usable in restricted storage. */ }
    }, 150);
    return () => clearTimeout(timer);
  }, [layout]);
  const playerMax = Math.max(240, Math.min(760, Math.round(viewport.height * .72)));
  const automatic = focused ? Math.max(240, Math.min(460, viewport.height * .36)) : Math.max(280, Math.min(380, viewport.height * .36));
  return {
    layout, desktop: viewport.width > 760, playerMax,
    playerHeight: Math.round(Math.max(200, Math.min(playerMax, layout.playerHeight || automatic))),
    update: (patch: Partial<Layout>) => setLayout(value => ({ ...value, ...patch })),
  };
}
