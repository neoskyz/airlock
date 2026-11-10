import { ApiError } from './api';
import type { Run } from './types';

export const MAX_UPDATE_FAILURES = 3;
export const RUN_UNAVAILABLE = 'This run is no longer available (the server restarted). Please start it again.';

export function updateFailure(error: unknown, failures: number): { retry: boolean; message: string } {
  if (error instanceof ApiError && error.status === 404) return { retry: false, message: RUN_UNAVAILABLE };
  if (failures >= MAX_UPDATE_FAILURES) return {
    retry: false,
    message: 'Live updates stopped after several attempts. You can start another run. The server may still be processing the previous run; check the audit log before repeating a payment.',
  };
  return { retry: true, message: `Live update failed. Reconnecting… (${failures}/${MAX_UPDATE_FAILURES})` };
}

export const isRunActive = (status: Run['status']) => status === 'running' || status === 'awaiting_approval';

// Provider identifiers are internal configuration; show neutral AI wording in UI messages.
export const displayText = (text: string) => text
  .replace(/(?:OPENAI|ANTHROPIC)_API_KEY/gi, 'an API key')
  .replace(/\b(?:openai|anthropic)\b/gi, 'AI');
