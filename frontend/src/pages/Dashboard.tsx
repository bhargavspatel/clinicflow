import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { motion, AnimatePresence } from "framer-motion"
import { ArrowRight, Bell, CheckCircle2, LogOut, Activity, Users, AlertTriangle, TrendingDown, Menu, X } from "lucide-react"
import { useAuth } from "@/lib/auth"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
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
  const dow = now.getDay()
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

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })
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

// ── Animated counter ──────────────────────────────────────────────────────────

function AnimatedNumber({ value }: { value: number }) {
  const [display, setDisplay] = useState(0)
  useEffect(() => {
    if (value === 0) { setDisplay(0); return }
    let start = 0
    const step = Math.ceil(value / 20)
    const timer = setInterval(() => {
      start += step
      if (start >= value) { setDisplay(value); clearInterval(timer) }
      else setDisplay(start)
    }, 30)
    return () => clearInterval(timer)
  }, [value])
  return <>{display}</>
}

// ── StatCard ──────────────────────────────────────────────────────────────────

function StatCard({
  title, value, icon: Icon, color, delay = 0,
}: {
  title: string
  value: number | null
  icon: React.ElementType
  color: string
  delay?: number
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay, ease: [0.25, 0.46, 0.45, 0.94] }}
      whileHover={{ y: -2, transition: { duration: 0.2 } }}
      className="rounded-2xl border bg-background p-5 shadow-sm hover:shadow-md transition-shadow"
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground mb-2">{title}</p>
          <p className={`text-3xl font-bold tabular-nums ${color}`}>
            {value === null ? <Skeleton className="h-8 w-14" /> : <AnimatedNumber value={value} />}
          </p>
        </div>
        <div className={`rounded-xl p-2.5 ${color.replace("text-", "bg-").replace("-600", "-50").replace("-500", "-50")}`}>
          <Icon className={`h-5 w-5 ${color}`} />
        </div>
      </div>
    </motion.div>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const [date, setDate] = useState(todayISO())
  const [providerFilter, setProviderFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")
  const [riskFilter, setRiskFilter] = useState("")
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  const [appointments, setAppointments] = useState<Appt[]>([])
  const [patients, setPatients] = useState<Record<string, string>>({})
  const [providers, setProviders] = useState<Provider[]>([])
  const [noShowsWeek, setNoShowsWeek] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState<string | null>(null)
  const [toast, setToast] = useState<{ msg: string; id: number } | null>(null)

  function showToast(msg: string) {
    setToast({ msg, id: Date.now() })
    setTimeout(() => setToast(null), 3500)
  }

  const fetchRef = useRef<(() => Promise<void>) | null>(null)

  const handleSocketMessage = useCallback((msg: WsMessage) => {
    if (msg.event === "appointment.status_changed") {
      void fetchRef.current?.()
      showToast(`Appointment ${msg.data.status.replace("_", " ")}.`)
    } else if (msg.event === "notification.sent") {
      showToast("Reminder sent to patient.")
    } else if (msg.event === "notification.received") {
      const label =
        msg.data.intent === "confirmed" ? "confirmed their appointment"
        : msg.data.intent === "reschedule_requested" ? "requested a reschedule"
        : "sent an unrecognized reply"
      showToast(`Patient ${label}.`)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useClinicSocket(handleSocketMessage)

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

  useEffect(() => {
    api.get<Provider[]>("/providers").then((r) => setProviders(r.data)).catch(() => {})
    api.get<{ items: { id: string; full_name: string }[] }>("/patients", { params: { page_size: 100 } })
      .then((r) => {
        const map: Record<string, string> = {}
        r.data.items.forEach((p) => { map[p.id] = p.full_name })
        setPatients(map)
      }).catch(() => {})
    const { from, to } = weekBounds()
    api.get<{ total: number }>("/appointments", { params: { date_from: from, date_to: to, status: "no_show", page_size: 1 } })
      .then((r) => setNoShowsWeek(r.data.total)).catch(() => setNoShowsWeek(0))
  }, [])

  useEffect(() => {
    fetchRef.current = fetchAppointments
    fetchAppointments()
  }, [fetchAppointments])

  async function markConfirmed(id: string) {
    setPending(id)
    try {
      await api.patch(`/appointments/${id}`, { status: "confirmed" })
      await fetchAppointments()
      showToast("Appointment confirmed.")
    } catch { showToast("Could not confirm appointment.") }
    finally { setPending(null) }
  }

  async function sendReminder(id: string) {
    setPending(id)
    try {
      await api.post(`/appointments/${id}/remind`)
      showToast("Reminder sent.")
    } catch { showToast("Could not send reminder.") }
    finally { setPending(null) }
  }

  const totalToday = loading ? null : appointments.length
  const confirmedCount = loading ? null : appointments.filter((a) => a.status === "confirmed").length
  const highRiskCount = loading ? null : appointments.filter((a) => a.risk_bucket === "high").length

  const selectClass = "h-9 rounded-xl border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring transition-shadow"

  return (
    <div className="min-h-screen bg-slate-50/50">
      {/* Header */}
      <motion.header
        className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur-md px-4 sm:px-6 py-3"
        initial={{ opacity: 0, y: -16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
      >
        <div className="mx-auto max-w-7xl flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary">
                <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5 text-white" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                </svg>
              </div>
              <span className="font-bold text-base">ClinicFlow</span>
            </div>
            <nav className="hidden md:flex items-center gap-1">
              <button onClick={() => navigate("/dashboard")} className="px-3 py-1.5 text-sm font-medium text-primary bg-primary/10 rounded-lg">Dashboard</button>
              <button onClick={() => navigate("/patients")} className="px-3 py-1.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted rounded-lg transition-colors">Patients</button>
              <button onClick={() => navigate("/providers")} className="px-3 py-1.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted rounded-lg transition-colors">Providers</button>
            </nav>
          </div>

          <div className="hidden sm:flex items-center gap-3">
            <span className="text-sm text-muted-foreground">{user?.email}</span>
            <Button variant="ghost" size="sm" onClick={logout} className="gap-1.5 text-muted-foreground hover:text-foreground">
              <LogOut className="h-3.5 w-3.5" />
              Sign out
            </Button>
          </div>

          <button className="sm:hidden p-1" onClick={() => setMobileMenuOpen(!mobileMenuOpen)}>
            {mobileMenuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>

        {/* Mobile menu */}
        <AnimatePresence>
          {mobileMenuOpen && (
            <motion.div
              className="sm:hidden pt-3 pb-2 border-t mt-3 flex flex-col gap-2"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.2 }}
            >
              <span className="text-sm text-muted-foreground px-1">{user?.email}</span>
              <Button variant="ghost" size="sm" onClick={logout} className="justify-start gap-1.5 text-muted-foreground">
                <LogOut className="h-3.5 w-3.5" /> Sign out
              </Button>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.header>

      <main className="mx-auto max-w-7xl px-4 sm:px-6 py-6 space-y-6">
        {/* Page title */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.1 }}
        >
          <h1 className="text-xl font-semibold text-foreground">Dashboard</h1>
          <p className="text-sm text-muted-foreground">{fmtDate(new Date().toISOString())}</p>
        </motion.div>

        {/* Stat cards */}
        <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
          <StatCard title="Today's Appointments" value={totalToday} icon={Activity} color="text-blue-600" delay={0.15} />
          <StatCard title="Confirmed" value={confirmedCount} icon={CheckCircle2} color="text-emerald-600" delay={0.2} />
          <StatCard title="High Risk" value={highRiskCount} icon={AlertTriangle} color="text-red-500" delay={0.25} />
          <StatCard title="No-shows This Week" value={noShowsWeek} icon={TrendingDown} color="text-orange-500" delay={0.3} />
        </div>

        {/* Filters */}
        <motion.div
          className="flex flex-wrap items-center gap-2"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.35 }}
        >
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className={selectClass}
          />
          <select value={providerFilter} onChange={(e) => setProviderFilter(e.target.value)} className={selectClass}>
            <option value="">All providers</option>
            {providers.map((p) => <option key={p.id} value={p.id}>{p.specialty}</option>)}
          </select>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className={selectClass}>
            <option value="">All statuses</option>
            {["scheduled", "confirmed", "rescheduled", "completed", "no_show", "cancelled"].map((s) => (
              <option key={s} value={s}>{s.replace("_", " ")}</option>
            ))}
          </select>
          <select value={riskFilter} onChange={(e) => setRiskFilter(e.target.value)} className={selectClass}>
            <option value="">All risk</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </motion.div>

        {/* Appointments table */}
        <motion.div
          className="rounded-2xl border bg-background shadow-sm overflow-hidden"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4, duration: 0.5 }}
        >
          {loading ? (
            <div className="divide-y">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="flex items-center gap-4 px-5 py-4">
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
            <motion.div
              className="py-20 flex flex-col items-center gap-3 text-muted-foreground"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
            >
              <Users className="h-10 w-10 opacity-30" />
              <p className="text-sm">No appointments found for this date.</p>
            </motion.div>
          ) : (
            <>
              {/* Desktop table */}
              <div className="hidden md:block overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-slate-50/80">
                      {["Time", "Patient", "Provider", "Type", "Risk", "Status", "Actions"].map((h) => (
                        <th key={h} className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {appointments.map((a, i) => (
                      <motion.tr
                        key={a.id}
                        initial={{ opacity: 0, x: -8 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ delay: i * 0.04, duration: 0.3 }}
                        className="hover:bg-slate-50/80 transition-colors group"
                      >
                        <td className="px-5 py-3.5 tabular-nums font-semibold text-foreground">{fmtTime(a.scheduled_at)}</td>
                        <td className="px-5 py-3.5 font-medium">{patients[a.patient_id] ?? <span className="text-muted-foreground">—</span>}</td>
                        <td className="px-5 py-3.5 text-muted-foreground">{providers.find((p) => p.id === a.provider_id)?.specialty ?? "—"}</td>
                        <td className="px-5 py-3.5 text-muted-foreground">{TYPE_LABELS[a.appointment_type] ?? a.appointment_type}</td>
                        <td className="px-5 py-3.5">
                          {a.risk_bucket ? <Badge variant={RISK_VARIANT[a.risk_bucket]}>{a.risk_bucket}</Badge> : <span className="text-xs text-muted-foreground">—</span>}
                        </td>
                        <td className="px-5 py-3.5">
                          <Badge variant={STATUS_VARIANT[a.status]}>{a.status.replace("_", " ")}</Badge>
                        </td>
                        <td className="px-5 py-3.5">
                          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                            {(a.status === "scheduled" || a.status === "rescheduled") && (
                              <Button size="sm" variant="outline" className="h-7 gap-1 px-2 text-xs rounded-lg" disabled={pending === a.id} onClick={() => markConfirmed(a.id)}>
                                <CheckCircle2 className="h-3 w-3" /> Confirm
                              </Button>
                            )}
                            {["scheduled", "confirmed", "rescheduled"].includes(a.status) && (
                              <Button size="sm" variant="outline" className="h-7 gap-1 px-2 text-xs rounded-lg" disabled={pending === a.id} onClick={() => sendReminder(a.id)}>
                                <Bell className="h-3 w-3" /> Remind
                              </Button>
                            )}
                            <Button size="sm" variant="ghost" className="h-7 w-7 p-0 rounded-lg" onClick={() => navigate(`/appointments/${a.id}`)}>
                              <ArrowRight className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </td>
                      </motion.tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Mobile cards */}
              <div className="md:hidden divide-y">
                {appointments.map((a, i) => (
                  <motion.div
                    key={a.id}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.05 }}
                    className="px-4 py-4 space-y-2"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="font-semibold text-sm">{patients[a.patient_id] ?? "—"}</p>
                        <p className="text-xs text-muted-foreground">{fmtTime(a.scheduled_at)} · {providers.find((p) => p.id === a.provider_id)?.specialty ?? "—"}</p>
                      </div>
                      <div className="flex gap-1.5 flex-shrink-0">
                        {a.risk_bucket && <Badge variant={RISK_VARIANT[a.risk_bucket]}>{a.risk_bucket}</Badge>}
                        <Badge variant={STATUS_VARIANT[a.status]}>{a.status.replace("_", " ")}</Badge>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5">
                      {(a.status === "scheduled" || a.status === "rescheduled") && (
                        <Button size="sm" variant="outline" className="h-7 gap-1 px-2 text-xs flex-1" disabled={pending === a.id} onClick={() => markConfirmed(a.id)}>
                          <CheckCircle2 className="h-3 w-3" /> Confirm
                        </Button>
                      )}
                      {["scheduled", "confirmed", "rescheduled"].includes(a.status) && (
                        <Button size="sm" variant="outline" className="h-7 gap-1 px-2 text-xs flex-1" disabled={pending === a.id} onClick={() => sendReminder(a.id)}>
                          <Bell className="h-3 w-3" /> Remind
                        </Button>
                      )}
                      <Button size="sm" variant="ghost" className="h-7 w-7 p-0 ml-auto" onClick={() => navigate(`/appointments/${a.id}`)}>
                        <ArrowRight className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </motion.div>
                ))}
              </div>
            </>
          )}
        </motion.div>
      </main>

      {/* Toast */}
      <AnimatePresence>
        {toast && (
          <motion.div
            key={toast.id}
            className="fixed bottom-5 right-5 z-50 flex items-center gap-2.5 rounded-2xl border bg-background px-4 py-3 shadow-xl text-sm font-medium"
            initial={{ opacity: 0, y: 16, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.95 }}
            transition={{ duration: 0.25, ease: [0.25, 0.46, 0.45, 0.94] }}
          >
            <div className="h-2 w-2 rounded-full bg-emerald-500 flex-shrink-0" />
            {toast.msg}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
