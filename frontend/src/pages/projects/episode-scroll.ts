export function activeStepIndex(
  tops: readonly number[],
  probeY: number,
  atBottom: boolean,
): number {
  if (!tops.length) return 0;
  if (atBottom) return tops.length - 1;
  let index = 0;
  tops.forEach((top, candidate) => {
    // scrollIntoView rounds scroll positions, while section bounds can be fractional.
    if (top <= probeY + 1) index = candidate;
  });
  return index;
}
