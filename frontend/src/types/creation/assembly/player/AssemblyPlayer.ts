export interface AssemblyPlayerHandle {
  seek: (frame: number) => void;
  pause: () => void;
  toggle: () => void;
}
