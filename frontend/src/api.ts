export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly detail?: unknown) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const timeout = AbortSignal.timeout(15000);
  const controller = new AbortController();
  const callerSignal = options?.signal;
  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  const abortForTimeout = () => controller.abort(timeout.reason);
  if (callerSignal?.aborted) abortFromCaller();
  else callerSignal?.addEventListener('abort', abortFromCaller, { once: true });
  timeout.addEventListener('abort', abortForTimeout, { once: true });
  const headers = new Headers(options?.headers);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  try {
    const response = await fetch(path, {
      ...options,
      headers,
      signal: controller.signal,
    });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      let detail: unknown;
      try {
        const body = await response.json();
        detail = body.detail;
        if (typeof detail === 'string') message = detail;
        else if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') message = detail.message;
      } catch { /* Non-JSON server errors retain their status text. */ }
      throw new ApiError(message, response.status, detail);
    }
    return await response.json() as T;
  } catch (error) {
    if (timeout.aborted || (error instanceof DOMException && error.name === 'TimeoutError')) {
      throw new Error('The request timed out. Check the current status before retrying.');
    }
    throw error;
  } finally {
    callerSignal?.removeEventListener('abort', abortFromCaller);
    timeout.removeEventListener('abort', abortForTimeout);
  }
}
