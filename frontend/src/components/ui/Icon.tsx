import type { CSSProperties } from 'react';

export type IconName = 'film' | 'folder' | 'library' | 'tasks' | 'settings' | 'arrow' | 'plus' | 'person' | 'scene' | 'prop';
const paths: Record<IconName, string> = {
  film: 'M3 8h18v12H3z M3 8V4h18v4 M7 4l3 4 M13 4l3 4 M19 4l2 3 M10 12l5 3-5 3z',
  folder: 'M3 7V5h6l2 2h10v13H3z',
  library: 'M3 4h5v16H3z M11 4h4v16h-4z M18 4l3 15',
  tasks: 'M9 5h12 M9 12h12 M9 19h12 M3 5l1 1 2-2 M3 12l1 1 2-2 M3 19l1 1 2-2',
  settings: 'M4 7h16 M4 17h16 M8 4v6 M16 14v6',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  plus: 'M12 5v14 M5 12h14',
  person: 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M4 21v-2a8 8 0 0 1 16 0v2',
  scene: 'M3 4h18v16H3z M3 16l6-6 5 5 3-3 4 4 M16 8h.01',
  prop: 'M4 7l8-4 8 4v10l-8 4-8-4z M4 7l8 4 8-4 M12 11v10',
};
export function Icon({ name, size = 20, style }: { name: IconName; size?: number; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}><path d={paths[name]} /></svg>;
}
