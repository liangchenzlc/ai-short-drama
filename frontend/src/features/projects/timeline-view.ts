export const MIN_ZOOM = .02;
export const MAX_ZOOM = 600;
export const boundZoom = (value: number) => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, value));
/** Eight screen pixels, capped at six frames so a long overview stays controllable. */
export function snapFrame(frame: number, boundaries: number[], pixelsPerSecond: number) {
  const rounded = Math.round(frame);
  const threshold = Math.min(6, 8 * 30 / pixelsPerSecond);
  let result = rounded, distance = Infinity;
  for (const boundary of boundaries) {
    const delta = Math.abs(boundary - frame);
    if (delta <= threshold && delta < distance) { result = boundary; distance = delta; }
  }
  return result;
}
export function adjacentStart(starts: number[], frame: number, direction: -1 | 1) {
  return direction === -1 ? [...starts].reverse().find(start => start < frame) : starts.find(start => start > frame);
}
export function tickInterval(pixelsPerSecond: number) {
  return [.1, .2, .5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600].find(seconds => seconds * pixelsPerSecond >= 72) ?? 3600;
}
export function zoomScroll(seconds: number, oldZoom: number, newZoom: number, scroll: number, width: number) {
  const x = 20 + seconds * oldZoom - scroll;
  const anchor = x >= 20 && x <= width - 20 ? x : width / 2;
  return Math.max(0, 20 + seconds * newZoom - anchor);
}
/** Only visible cells exist, including one partially visible cell on each side. */
export function filmstripCells(width: number, clipStart: number, viewportStart: number, viewportWidth: number,
  trimIn: number, duration: number, interval: number, count: number, cellWidth: number) {
  if (width <= 0 || interval <= 0 || count <= 0) return [];
  const first = Math.max(0, Math.floor((viewportStart - clipStart) / cellWidth));
  const last = Math.min(Math.ceil(width / cellWidth), Math.ceil((viewportStart + viewportWidth - clipStart) / cellWidth));
  return Array.from({ length: Math.max(0, Math.min(32, last - first)) }, (_, i) => {
    const x = (first + i) * cellWidth;
    const time = trimIn + Math.min(width, x + cellWidth / 2) / width * duration;
    return { x, index: Math.max(0, Math.min(count - 1, Math.floor(time / interval))) };
  });
}
