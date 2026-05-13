import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { ArrowRight, Bell, CheckCircle2 } from "lucide-react"
import { useAuth } from "@/lib/auth"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useClinicSocket, type WsMessage } from "@/hooks/useClinicSocket"

// ── Types ─────────────────────────────────────────────────────────────────────

interface Appt {
  id: string
  patient_id: string
  provider_id: string
  scheduled_at: string
  appointment_type: string
  status: string
  risk_score: number | null
  risk_bucket: string | null
}

interface Provider {
  id: string
  specialty: string
  is_active: boolean
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

function weekBounds(): { from: string; to: string } {
  const now = new Date()
  const dow = now.getDay() // 0=Sun
  const monday = new Date(now)
  monday.setDate(now.getDate() - (dow === 0 ? 6 : dow - 1))
  monday.setHours(0, 0, 0, 0)
  const sunday = new Date(monday)
  sunday.setDate(monday.getDate() + 6)
  sunday.setHours(23, 59, 59, 999)
  return { from: monday.toISOString(), to: sunday.toISOString() }
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
}

const TYPE_LABELS: Record<string, string> = {
  initial_eval: "Initial Eval",
  follow_up: "Follow-up",
  consultation: "Consultation",
}

type BadgeVariant = "success" | "blue" | "warning" | "orange" | "danger" | "muted"

const RISK_VARIANT: Record<string, BadgeVariant> = {
  low: "success",
  medium: "warning",
  high: "danger",
}

const STATUS_VARIANT: Record<string, BadgeVariant> = {
  scheduled: "blue",
  confirmed: "success",
  rescheduled: "orange",
  completed: "muted",
  no_show: "danger",
  cancelled: "muted",
}

// ── StatCard ──────────────────────────────────────────────────────────────────

function StatCard({
  title,
  value,
  className = "",
}: {
  title: string
  value: number | null
  className?: string
}) {
  return (
    <Card>
      <CardHeader className="pb-1 pt-4">
        <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="pb-4">
        {value === null ? (
          <Skeleton className="h-8 w-14" />
        ) : (
          <p className={`text-3xl font-bold tabular-nums ${className}`}>{value}</p>
        )}
      </CardContent>
    </Card>
  )
}

// ── Filter select ─────────────────────────────────────────────────────────────

function FilterSelect({
  value,
  onChange,
  children,
}: {
  value: string
  onChange: (v: string) => void
  children: React.ReactNode
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
    >
      {children}
    </select>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  // Filters
  const [date, setDate] = useState(todayISO())
  const [providerFilter, setProviderFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")
  const [riskFilter, setRiskFilter] = useState("")

  // Data
  const [appointments, setAppointments] = useState<Appt[]>([])
  const [patients, setPatients] = useState<Record<string, string>>({})
  const [providers, setProviders] = useState<Provider[]>([])
  const [noShowsWeek, setNoShowsWeek] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  function showToast(msg: string) {
    setToast(msg)
    setTimeout(() => setToast(null), 3000)
  }

  // ── WebSocket — keep fetchAppointments ref stable across renders ──────────
  const fetchRef = useRef<(() => Promise<void>) | null>(null)

  const handleSocketMessage = useCallback(
    (msg: WsMessage) => {
      if (msg.event === "appointment.status_changed") {
        void fetchRef.current?.()
        showToast(`Appointment ${msg.data.status.replace("_", " ")}.`)
      } else if (msg.event === "notification.sent") {
        showToast("Reminder sent to patient.")
      } else if (msg.event === "notification.received") {
        const intentLabel =
          msg.data.intent === "confirmed"
            ? "confirmed their appointment"
            : msg.data.intent === "reschedule_requested"
              ? "requested a reschedule"
              : "sent an unrecognized reply"
        showToast(`Patient ${intentLabel}.`)
      }
    },
    // showToast is stable (defined in this scope); intentionally no deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  useClinicSocket(handleSocketMessage)

  // ── Fetch appointments (re-runs on any filter change) ─────────────────────
  const fetchAppointments = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string> = {
        date_from: `${date}T00:00:00Z`,
        date_to: `${date}T23:59:59Z`,
        page_size: "100",
      }
      if (providerFilter) params.provider_id = providerFilter
      if (statusFilter) params.status = statusFilter
      if (riskFilter) params.risk_bucket = riskFilter
      const { data } = await api.get<{ items: Appt[] }>("/appointments", { params })
      setAppointments(data.items)
    } finally {
      setLoading(false)
    }
  }, [date, providerFilter, statusFilter, riskFilter])

  // ── On mount: providers, patient names, no-shows this week ────────────────
  useEffect(() => {
    api
      .get<Provider[]>("/providers")
      .then((r) => setProviders(r.data))
      .catch(() => {})

    api
      .get<{ items: { id: string; full_name: string }[] }>("/patients", {
        params: { page_size: 100 },
      })
      .then((r) => {
        const map: Record<string, string> = {}
        r.data.items.forEach((p) => {
          map[p.id] = p.full_name
        })
        setPatients(map)
      })
      .catch(() => {})

    const { from, to } = weekBounds()
    api
      .get<{ total: number }>("/appointments", {
        params: { date_from: from, date_to: to, status: "no_show", page_size: 1 },
      })
      .then((r) => setNoShowsWeek(r.data.total))
      .catch(() => setNoShowsWeek(0))
  }, [])

  useEffect(() => {
    fetchRef.current = fetchAppointments
    fetchAppointments()
  }, [fetchAppointments])

  // ── Actions ───────────────────────────────────────────────────────────────
  async function markConfirmed(id: string) {
    setPending(id)
    try {
      await api.patch(`/appointments/${id}`, { status: "confirmed" })
      await fetchAppointments()
      showToast("Appointment confirmed.")
    } catch {
      showToast("Could not confirm appointment.")
    } finally {
      setPending(null)
    }
  }

  async function sendReminder(id: string) {
    setPending(id)
    try {
      await api.post(`/appointments/${id}/remind`)
      showToast("Reminder sent.")
    } catch {
      showToast("Could not send reminder.")
    } finally {
      setPending(null)
    }
  }

  // ── Derived stats ─────────────────────────────────────────────────────────
  const totalToday = loading ? null : appointments.length
  const confirmedCount = loading ? null : appointments.filter((a) => a.status === "confirmed").length
  const highRiskCount = loading ? null : appointments.filter((a) => a.risk_bucket === "high").length

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-muted/30">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b bg-background px-6 py-3 flex items-center justify-between">
        <div>
          <span className="text-base font-semibold">ClinicFlow</span>
          <span className="ml-3 text-sm text-muted-foreground">{user?.email}</span>
        </div>
        <Button variant="ghost" size="sm" onClick={logout}>
          Sign out
        </Button>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-6 space-y-6">
        {/* Summary cards */}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard title="Total Today" value={totalToday} />
          <StatCard title="Confirmed" value={confirmedCount} className="text-green-600" />
          <StatCard title="High Risk" value={highRiskCount} className="text-red-600" />
          <StatCard title="No-shows This Week" value={noShowsWeek} className="text-orange-600" />
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <FilterSelect value={providerFilter} onChange={setProviderFilter}>
            <option value="">All providers</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.specialty}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect value={statusFilter} onChange={setStatusFilter}>
            <option value="">All statuses</option>
            {["scheduled", "confirmed", "rescheduled", "completed", "no_show", "cancelled"].map(
              (s) => (
                <option key={s} value={s}>
                  {s.replace("_", " ")}
                </option>
              ),
            )}
          </FilterSelect>
          <FilterSelect value={riskFilter} onChange={setRiskFilter}>
            <option value="">All risk</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </FilterSelect>
        </div>

        {/* Appointments */}
        <div className="rounded-lg border bg-background overflow-hidden">
          {loading ? (
            <div className="space-y-px">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="flex items-center gap-4 px-4 py-3 border-b last:border-b-0">
                  <Skeleton className="h-4 w-12" />
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-4 w-20" />
                  <Skeleton className="h-5 w-14 rounded-full" />
                  <Skeleton className="h-5 w-20 rounded-full" />
                </div>
              ))}
            </div>
          ) : appointments.length === 0 ? (
            <div className="py-16 text-center text-sm text-muted-foreground">
              No appointments found.
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/40">
                  {["Time", "Patient", "Provider", "Type", "Risk", "Status", "Actions"].map(
                    (h) => (
                      <th
                        key={h}
                        className="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground"
                      >
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {appointments.map((a) => (
                  <tr key={a.id} className="border-b last:border-b-0 hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3 tabular-nums font-medium">{fmtTime(a.scheduled_at)}</td>
                    <td className="px-4 py-3">{patients[a.patient_id] ?? <span className="text-muted-foreground text-xs">—</span>}</td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {providers.find((p) => p.id === a.provider_id)?.specialty ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {TYPE_LABELS[a.appointment_type] ?? a.appointment_type}
                    </td>
                    <td className="px-4 py-3">
                      {a.risk_bucket ? (
                        <Badge variant={RISK_VARIANT[a.risk_bucket]}>
                          {a.risk_bucket}
                        </Badge>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={STATUS_VARIANT[a.status]}>
                        {a.status.replace("_", " ")}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        {(a.status === "scheduled" || a.status === "rescheduled") && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 gap-1 px-2 text-xs"
                            disabled={pending === a.id}
                            onClick={() => markConfirmed(a.id)}
                          >
                            <CheckCircle2 className="h-3 w-3" />
                            Confirm
                          </Button>
                        )}
                        {["scheduled", "confirmed", "rescheduled"].includes(a.status) && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 gap-1 px-2 text-xs"
                            disabled={pending === a.id}
                            onClick={() => sendReminder(a.id)}
                          >
                            <Bell className="h-3 w-3" />
                            Remind
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 w-7 p-0"
                          title="View detail"
                          onClick={() => navigate(`/appointments/${a.id}`)}
                        >
                          <ArrowRight className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-4 right-4 z-50 rounded-lg border bg-background px-4 py-3 shadow-lg text-sm animate-in fade-in slide-in-from-bottom-2">
          {toast}
        </div>
      )}
    </div>
  )
}
