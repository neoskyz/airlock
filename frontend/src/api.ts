export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
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
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        if (typeof body.detail === 'string') detail = body.detail;
      } catch { /* Non-JSON server errors retain their status text. */ }
      throw new ApiError(detail, response.status);
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
