import { useRef } from 'react';

/** Pointer capture keeps resizing local; arrows provide the same control without dragging. */
export function AssemblyResizeHandle({ label, controls, value, min, max, onChange }: {
  label: string; controls: string; value: number; min: number; max: number;
  onChange: (value: number) => void;
}) {
  const drag = useRef<{ pointer: number; y: number; value: number } | null>(null);
  const change = (next: number) => onChange(Math.round(Math.max(min, Math.min(max, next))));
  return <div className="assembly-resize-handle" role="separator" tabIndex={0}
    aria-label={label} aria-orientation="horizontal" aria-controls={controls}
    aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} aria-valuetext={`${value} 像素`}
    title={`${label}：拖动或使用上下方向键，Home / End 调至最小 / 最大`}
    onPointerDown={event => {
      if (event.button !== 0) return;
      event.preventDefault(); event.currentTarget.focus();
      drag.current = { pointer: event.pointerId, y: event.clientY, value };
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={event => {
      const start = drag.current;
      if (start?.pointer === event.pointerId) change(start.value + event.clientY - start.y);
    }}
    onPointerUp={event => {
      if (drag.current?.pointer !== event.pointerId) return;
      drag.current = null;
      event.currentTarget.releasePointerCapture(event.pointerId);
    }}
    onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}
    onKeyDown={event => {
      const next = { ArrowUp: value - 20, ArrowDown: value + 20, Home: min, End: max }[event.key];
      if (next !== undefined) { event.preventDefault(); event.stopPropagation(); change(next); }
    }}><span aria-hidden="true"/></div>;
}
