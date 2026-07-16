// Single fetch wrapper for every API call. Attaches the bearer token, parses the
// error envelope {error:{code,message,details}} and handles 401 globally.

const TOKEN_KEY = 'cam.token';

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;

  constructor(status: number, code: string, message: string, details: unknown = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function handleUnauthorized(): void {
  clearToken();
  if (window.location.pathname !== '/login') {
    window.location.assign('/login');
  }
}

async function toApiError(res: Response): Promise<ApiError> {
  let code = 'unknown_error';
  let message = `Request failed (${res.status})`;
  let details: unknown = null;
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string; details?: unknown } };
    if (body && body.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details ?? null;
    }
  } catch {
    // non-JSON error body; keep defaults
  }
  return new ApiError(res.status, code, message, details);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders() };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body; // browser sets multipart boundary
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  const res = await fetch(path, { method, headers, body: payload });

  if (res.status === 401) {
    const err = await toApiError(res);
    handleUnauthorized();
    throw err;
  }
  if (!res.ok) {
    throw await toApiError(res);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  // Some accepted (202) responses may carry no body.
  const text = await res.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}

function parseFilename(disposition: string | null): string | null {
  if (!disposition) return null;
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (utf8) return decodeURIComponent(utf8[1].trim());
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1].trim() : null;
}

/** Fetches a binary endpoint and triggers a browser download. */
async function download(path: string, fallbackFilename: string): Promise<void> {
  const res = await fetch(path, { headers: authHeaders() });
  if (res.status === 401) {
    const err = await toApiError(res);
    handleUnauthorized();
    throw err;
  }
  if (!res.ok) {
    throw await toApiError(res);
  }
  const blob = await res.blob();
  const filename = parseFilename(res.headers.get('Content-Disposition')) ?? fallbackFilename;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  del: <T = void>(path: string) => request<T>('DELETE', path),
  postForm: <T>(path: string, form: FormData) => request<T>('POST', path, form),
  download,
};

/** Human-readable message for toasts. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return `${err.message} (${err.code})`;
  }
  if (err instanceof Error) return err.message;
  return 'Unexpected error';
}
