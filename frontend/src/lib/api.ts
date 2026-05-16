import axios, { type InternalAxiosRequestConfig, type Method } from "axios"

// Tokens held in module scope — never written to localStorage.
// Page refresh clears them; users must re-authenticate.
let _accessToken: string | null = null
let _refreshToken: string | null = null

export function setAccessToken(token: string | null): void {
  _accessToken = token
}

export function setRefreshToken(token: string | null): void {
  _refreshToken = token
}

export function getAccessToken(): string | null {
  return _accessToken
}

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1"

export const api = axios.create({
  baseURL: BASE_URL,
  headers: { "Content-Type": "application/json" },
})

/**
 * Make a one-off request with an explicit Bearer token, bypassing the shared
 * access-token interceptor.  Used by the patient portal which holds a portal
 * JWT rather than a staff access token.
 */
export async function callWithToken<T>(
  method: Method,
  path: string,
  token: string,
  options: { params?: Record<string, string>; body?: unknown } = {},
): Promise<T> {
  const { data } = await axios({
    method,
    url: `${BASE_URL}${path}`,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    params: options.params,
    data: options.body,
  })
  return data as T
}

// ── Request interceptor: attach Bearer token ──────────────────────────────────
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (_accessToken) {
    config.headers.Authorization = `Bearer ${_accessToken}`
  }
  return config
})

// ── Response interceptor: refresh on 401 ─────────────────────────────────────
// Queue concurrent requests that arrive while a refresh is in flight so they
// all get the new token rather than triggering multiple refresh calls.
let _isRefreshing = false
let _refreshQueue: Array<{
  resolve: (token: string) => void
  reject: (err: unknown) => void
}> = []

function _processQueue(err: unknown, token: string | null): void {
  _refreshQueue.forEach((p) => (err ? p.reject(err) : p.resolve(token!)))
  _refreshQueue = []
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

    if (error.response?.status !== 401 || original._retry) {
      return Promise.reject(error)
    }

    if (_isRefreshing) {
      // Park this request until the ongoing refresh resolves
      return new Promise((resolve, reject) => {
        _refreshQueue.push({
          resolve: (token) => {
            original.headers.Authorization = `Bearer ${token}`
            resolve(api(original))
          },
          reject,
        })
      })
    }

    original._retry = true
    _isRefreshing = true

    try {
      const { data } = await axios.post<{ access_token: string; refresh_token: string }>(
        `${BASE_URL}/auth/refresh`,
        { refresh_token: _refreshToken },
      )
      setAccessToken(data.access_token)
      setRefreshToken(data.refresh_token)
      _processQueue(null, data.access_token)
      original.headers.Authorization = `Bearer ${data.access_token}`
      return api(original)
    } catch (refreshError) {
      _processQueue(refreshError, null)
      setAccessToken(null)
      setRefreshToken(null)
      // Signal React context to clear auth state
      window.dispatchEvent(new Event("auth:logout"))
      return Promise.reject(refreshError)
    } finally {
      _isRefreshing = false
    }
  },
)
