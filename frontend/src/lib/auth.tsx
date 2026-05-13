import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react"
import { api, setAccessToken, setRefreshToken } from "@/lib/api"

// ── Types ─────────────────────────────────────────────────────────────────────

export interface AuthUser {
  id: string
  email: string
  role: "owner" | "provider" | "front_desk"
  tenant_id: string
}

interface AuthState {
  user: AuthUser | null
  isLoading: boolean
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

// ── Context ───────────────────────────────────────────────────────────────────

const AuthContext = createContext<AuthContextValue | null>(null)

// ── Provider ──────────────────────────────────────────────────────────────────

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({ user: null, isLoading: false })

  const _clearAuth = useCallback(() => {
    setAccessToken(null)
    setRefreshToken(null)
    setState({ user: null, isLoading: false })
  }, [])

  // Listen for refresh failures signalled by the Axios interceptor
  useEffect(() => {
    window.addEventListener("auth:logout", _clearAuth)
    return () => window.removeEventListener("auth:logout", _clearAuth)
  }, [_clearAuth])

  const login = useCallback(async (email: string, password: string) => {
    setState((s) => ({ ...s, isLoading: true }))
    try {
      const { data } = await api.post<{
        access_token: string
        refresh_token: string
      }>("/auth/login", { email, password })

      setAccessToken(data.access_token)
      setRefreshToken(data.refresh_token)

      const { data: me } = await api.get<AuthUser>("/users/me")
      setState({ user: me, isLoading: false })
    } catch (err) {
      setState({ user: null, isLoading: false })
      throw err
    }
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout")
    } catch {
      // best-effort — clear local state regardless
    } finally {
      _clearAuth()
    }
  }, [_clearAuth])

  return (
    <AuthContext.Provider value={{ ...state, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider")
  return ctx
}
