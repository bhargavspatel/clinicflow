import React from "react"
import { Navigate, Route, Routes } from "react-router-dom"
import * as Sentry from "@sentry/react"
import { useAuth } from "@/lib/auth"
import Login from "@/pages/Login"
import Dashboard from "@/pages/Dashboard"
import Portal from "@/pages/Portal"

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth()
  if (isLoading) return null
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

function AppRoutes() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/login" element={<Login />} />
      <Route path="/portal" element={<Portal />} />

      {/* Protected */}
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <Dashboard />
          </ProtectedRoute>
        }
      />

      {/* Default redirect */}
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <Sentry.ErrorBoundary fallback={<p>An unexpected error occurred. Please refresh the page.</p>}>
      <AppRoutes />
    </Sentry.ErrorBoundary>
  )
}
