import { ApiError } from './api';
import type { Run } from './types';

export const MAX_UPDATE_FAILURES = 3;
export const RUN_UNAVAILABLE = 'This run is no longer available (the server restarted). Please start it again.';

export function updateFailure(error: unknown, failures: number): { retry: boolean; message: string } {
  if (error instanceof ApiError && error.status === 404) return { retry: false, message: RUN_UNAVAILABLE };
  if (failures >= MAX_UPDATE_FAILURES) return {
    retry: false,
    message: 'Live updates stopped after several attempts. The server may still be processing this task. Reconnect to check its status, or open the Airlock console before repeating a payment.',
  };
  return { retry: true, message: `Live update failed. Reconnecting… (${failures}/${MAX_UPDATE_FAILURES})` };
}

export const isRunActive = (status: Run['status']) => status === 'running' || status === 'awaiting_approval' || status === 'cancelling';
export const isRunBlocking = (run: Run) => isRunActive(run.status) || Boolean(run.blocking);

// Provider identifiers are internal configuration; show neutral AI wording in UI messages.
export const displayText = (text: string) => text
  .replace(/(?:OPENAI|ANTHROPIC)_API_KEY/gi, 'an API key')
  .replace(/\b(?:openai|anthropic)\b/gi, 'AI');


export function conflictRun(error: unknown): Run | undefined {
  if (!(error instanceof ApiError) || error.status !== 409 || !error.detail || typeof error.detail !== 'object') return;
  const value = (error.detail as { run?: Run }).run;
  if (value && typeof value.id === 'string' && ['running', 'awaiting_approval', 'cancelling', 'completed', 'failed', 'cancelled'].includes(value.status) && Array.isArray(value.events) && Array.isArray(value.messages)) {
    return isRunActive(value.status) ? value : { ...value, blocking: true };
  }
}

// Responses may arrive ahead of the next SSE frame. Keep their run until the
// stream has caught up, so accepted starts and stops cannot regress in the UI.
export function reconcileRuns(runs: Run[], responseRun: Run | null): Run[] {
  if (!responseRun) return runs;
  const remote = runs.find(run => run.id === responseRun.id);
  if (!responseRun.blocking && remote && (remote.revision ?? -1) >= (responseRun.revision ?? 0)) return runs;
  if (!remote) return [...runs, responseRun];
  return runs.map(run => run.id === responseRun.id ? responseRun : run);
}

export function approvalHref(run: Run): string {
  const approval = run.approvals?.find(item => ['pending', 'awaiting_approval'].includes(item.status));
  return approval ? `/console?approval=${encodeURIComponent(approval.id)}#approvals` : '/console#approvals';
}
