import { useEffect, useRef } from "react"
import { getAccessToken } from "@/lib/api"

const WS_BASE = "ws://localhost:8000/api/v1/ws"
const MAX_BACKOFF_MS = 30_000

// ── Message types ─────────────────────────────────────────────────────────────

export interface AppointmentChangedData {
  appointment_id: string
  patient_id: string
  provider_id: string
  status: string
  scheduled_at: string
}

export interface NotificationSentData {
  notification_id: string
  appointment_id: string
  patient_id: string
}

export interface NotificationReceivedData {
  from_number: string
  intent: "confirmed" | "reschedule_requested" | "unrecognized"
  appointment_id: string
}

export type WsMessage =
  | { event: "appointment.status_changed"; data: AppointmentChangedData }
  | { event: "notification.sent";          data: NotificationSentData }
  | { event: "notification.received";      data: NotificationReceivedData }

// ── Hook ──────────────────────────────────────────────────────────────────────

/**
 * Opens a WebSocket to the ClinicFlow backend and calls `onMessage` for each
 * event pushed by the server.  Reconnects automatically with exponential
 * backoff (1 s → 2 s → … → 30 s max).  Stops reconnecting if the server
 * closes with code 4001 (invalid / expired token).
 *
 * Pass a stable callback reference (e.g. via useCallback or useRef) to avoid
 * restarting the socket on every render.
 */
export function useClinicSocket(onMessage: (msg: WsMessage) => void): void {
  // Keep the callback ref fresh without restarting the connection
  const callbackRef = useRef(onMessage)
  callbackRef.current = onMessage

  useEffect(() => {
    let ws: WebSocket | null = null
    let backoffMs = 1_000
    let timer: ReturnType<typeof setTimeout> | null = null
    let unmounted = false

    function connect(): void {
      const token = getAccessToken()
      if (!token) return // not authenticated yet — nothing to connect to

      ws = new WebSocket(`${WS_BASE}?token=${encodeURIComponent(token)}`)

      ws.onopen = () => {
        backoffMs = 1_000 // reset on successful connect
      }

      ws.onmessage = (e: MessageEvent) => {
        try {
          const msg = JSON.parse(e.data as string) as WsMessage
          callbackRef.current(msg)
        } catch {
          // ignore non-JSON or unexpected shapes
        }
      }

      ws.onerror = () => {
        ws?.close()
      }

      ws.onclose = (e: CloseEvent) => {
        ws = null
        if (unmounted) return
        if (e.code === 4001) return // auth failure — don't reconnect, user must re-login

        timer = setTimeout(() => {
          if (!unmounted) connect()
        }, backoffMs)

        backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS)
      }
    }

    connect()

    return () => {
      unmounted = true
      if (timer !== null) clearTimeout(timer)
      ws?.close()
    }
  }, []) // deliberately empty — reconnect logic inside handles token and lifecycle
}
