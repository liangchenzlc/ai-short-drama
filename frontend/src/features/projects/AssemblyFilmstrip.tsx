import { useState } from 'react';
import type { AssemblyClip } from '../../api/modules/assembly';
import { filmstripCells } from './timeline-view';

export function AssemblyFilmstrip({ clip, start, width, height, viewport }: {
  clip: AssemblyClip; start: number; width: number; height: number;
  viewport: { left: number; width: number };
}) {
  const [failed, setFailed] = useState('');
  const sheet = clip.filmstrip;
  if (!sheet?.url || sheet.url === failed) return clip.poster ? <img className="assembly-poster" src={clip.poster} alt="" loading="lazy" draggable={false}/> : null;
  const cellWidth = Math.max(96, Math.round(height * 16 / 9));
  const cells = filmstripCells(width, start, viewport.left, viewport.width, clip.trim_in_ms,
    (clip.trim_out_ms ?? clip.duration_ms ?? 0) - clip.trim_in_ms, sheet.interval_ms, sheet.count, cellWidth);
  return <span className="assembly-filmstrip" aria-hidden="true">{cells.map(cell => <span key={cell.x} className="assembly-filmstrip-cell" data-frame-index={cell.index} style={{ left: cell.x, width: cellWidth }}>
    <img className="assembly-sprite" src={sheet.url!} alt="" loading="lazy" draggable={false} onError={() => setFailed(sheet.url!)}
      style={{ width: cellWidth * sheet.count, left: -cell.index * cellWidth }}/>
  </span>)}</span>;
}
