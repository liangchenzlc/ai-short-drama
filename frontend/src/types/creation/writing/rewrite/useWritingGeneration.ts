import type { WritingTarget } from '@host/types/creation/writing/rewrite/writing-requests';
import type { TextGenerationRequest } from '@host/types/generations/api';

export interface WritingAttempt {
  format: 1;
  key: string;
  target: WritingTarget;
  body: TextGenerationRequest;
}
