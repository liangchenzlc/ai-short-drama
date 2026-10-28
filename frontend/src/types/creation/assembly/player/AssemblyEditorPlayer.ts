export type Voice = {
  element: HTMLAudioElement;
  gain: GainNode;
  source: MediaElementAudioSourceNode;
  mediaId: string;
};
