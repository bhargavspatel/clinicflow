import { useEffect, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { motion } from "framer-motion"
import { ArrowLeft, Bell, CheckCircle2, Clock, User, Stethoscope, Calendar, AlertTriangle, FileText } from "lucide-react"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"

interface Appointment {
  id: string
  patient_id: string
  provider_id: string
  scheduled_at: string
  duration_minutes: number
  appointment_type: string
  status: string
  risk_score: number | null
  risk_bucket: string | null
  last_scored_at: string | null
  notes: string | null
  created_at: string
}

interface Patient { id: string; full_name: string; phone: string; email: string | null; dob: string | null }
interface Provider { id: string; specialty: string; is_active: boolean }

type BadgeVariant = "success" | "blue" | "warning" | "orange" | "danger" | "muted"

const RISK_VARIANT: Record<string, BadgeVariant> = { low: "success", medium: "warning", high: "danger" }
const STATUS_VARIANT: Record<string, BadgeVariant> = {
  scheduled: "blue", confirmed: "success", rescheduled: "orange",
  completed: "muted", no_show: "danger", cancelled: "muted",
}

const TYPE_LABELS: Record<string, string> = {
  initial_eval: "Initial Evaluation",
  follow_up: "Follow-up",
  consultation: "Consultation",
}

function InfoRow({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 py-3 border-b last:border-0">
      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 flex-shrink-0 mt-0.5">
        <Icon className="h-4 w-4 text-slate-500" />
      </div>
      <div>
        <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{label}</p>
        <p className="text-sm font-medium mt-0.5">{value}</p>
      </div>
    </div>
  )
}

export default function AppointmentDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [appt, setAppt] = useState<Appointment | null>(null)
  const [patient, setPatient] = useState<Patient | null>(null)
  const [provider, setProvider] = useState<Provider | null>(null)
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  function showToast(msg: string) {
    setToast(msg)
    setTimeout(() => setToast(null), 3000)
  }

  useEffect(() => {
    if (!id) return
    setLoading(true)
    api.get<Appointment>(`/appointments/${id}`)
      .then(async (r) => {
        setAppt(r.data)
        const [patRes, provRes] = await Promise.all([
          api.get<Patient>(`/patients/${r.data.patient_id}`),
          api.get<Provider>(`/providers/${r.data.provider_id}`),
        ])
        setPatient(patRes.data)
        setProvider(provRes.data)
      })
      .catch(() => navigate("/dashboard"))
      .finally(() => setLoading(false))
  }, [id, navigate])

  async function markConfirmed() {
    if (!appt) return
    setPending(true)
    try {
      const { data } = await api.patch<Appointment>(`/appointments/${appt.id}`, { status: "confirmed" })
      setAppt(data)
      showToast("Appointment confirmed.")
    } catch { showToast("Could not confirm.") }
    finally { setPending(false) }
  }

  async function sendReminder() {
    if (!appt) return
    setPending(true)
    try {
      await api.post(`/appointments/${appt.id}/remind`)
      showToast("Reminder sent.")
    } catch { showToast("Could not send reminder.") }
    finally { setPending(false) }
  }

  async function markCancelled() {
    if (!appt) return
    setPending(true)
    try {
      const { data } = await api.patch<Appointment>(`/appointments/${appt.id}`, { status: "cancelled" })
      setAppt(data)
      showToast("Appointment cancelled.")
    } catch { showToast("Could not cancel.") }
    finally { setPending(false) }
  }

  const canConfirm = appt && ["scheduled", "rescheduled"].includes(appt.status)
  const canRemind = appt && ["scheduled", "confirmed", "rescheduled"].includes(appt.status)
  const canCancel = appt && !["cancelled", "completed", "no_show"].includes(appt.status)

  return (
    <div className="min-h-screen bg-slate-50/50">
      <motion.header
        className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur-md px-4 sm:px-6 py-3"
        initial={{ opacity: 0, y: -16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}
      >
        <div className="mx-auto max-w-4xl flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => navigate("/dashboard")}>
              <ArrowLeft className="h-4 w-4" /> Dashboard
            </Button>
            <span className="text-muted-foreground">/</span>
            <span className="font-semibold text-sm">Appointment</span>
          </div>
          {!loading && appt && (
            <Badge variant={STATUS_VARIANT[appt.status]} className="text-xs">
              {appt.status.replace("_", " ")}
            </Badge>
          )}
        </div>
      </motion.header>

      <main className="mx-auto max-w-4xl px-4 sm:px-6 py-6 space-y-5">
        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {Array.from({ length: 2 }).map((_, i) => (
              <div key={i} className="rounded-2xl border bg-background p-5 space-y-4">
                <Skeleton className="h-5 w-32" />
                {Array.from({ length: 4 }).map((_, j) => (
                  <div key={j} className="flex gap-3 py-3 border-b last:border-0">
                    <Skeleton className="h-8 w-8 rounded-lg flex-shrink-0" />
                    <div className="space-y-1.5 flex-1">
                      <Skeleton className="h-3 w-16" />
                      <Skeleton className="h-4 w-32" />
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        ) : appt ? (
          <>
            <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
              <h1 className="text-xl font-semibold">
                {patient?.full_name ?? "Appointment"} — {TYPE_LABELS[appt.appointment_type] ?? appt.appointment_type}
              </h1>
              <p className="text-sm text-muted-foreground">
                {new Date(appt.scheduled_at).toLocaleString([], { weekday: "long", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" })}
              </p>
            </motion.div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              {/* Appointment details */}
              <motion.div
                className="rounded-2xl border bg-background p-5 shadow-sm"
                initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}
              >
                <h2 className="font-semibold text-sm mb-1">Appointment Details</h2>
                <InfoRow icon={Calendar} label="Scheduled" value={new Date(appt.scheduled_at).toLocaleString([], { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" })} />
                <InfoRow icon={Clock} label="Duration" value={`${appt.duration_minutes} minutes`} />
                <InfoRow icon={FileText} label="Type" value={TYPE_LABELS[appt.appointment_type] ?? appt.appointment_type} />
                <InfoRow icon={AlertTriangle} label="Risk" value={
                  appt.risk_bucket
                    ? <Badge variant={RISK_VARIANT[appt.risk_bucket]}>{appt.risk_bucket} {appt.risk_score !== null ? `(${appt.risk_score}/100)` : ""}</Badge>
                    : <span className="text-muted-foreground text-sm">Not scored yet</span>
                } />
                {appt.notes && <InfoRow icon={FileText} label="Notes" value={appt.notes} />}
              </motion.div>

              {/* Patient & Provider */}
              <motion.div
                className="rounded-2xl border bg-background p-5 shadow-sm"
                initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }}
              >
                <h2 className="font-semibold text-sm mb-1">Patient & Provider</h2>
                <InfoRow icon={User} label="Patient" value={
                  <button className="text-primary hover:underline font-medium" onClick={() => navigate(`/patients/${appt.patient_id}`)}>
                    {patient?.full_name ?? "—"}
                  </button>
                } />
                {patient && <InfoRow icon={User} label="Phone" value={patient.phone} />}
                {patient?.email && <InfoRow icon={User} label="Email" value={patient.email} />}
                {patient?.dob && <InfoRow icon={Calendar} label="Date of Birth" value={new Date(patient.dob).toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" })} />}
                <InfoRow icon={Stethoscope} label="Provider" value={provider?.specialty ?? "—"} />
              </motion.div>
            </div>

            {/* Actions */}
            <motion.div
              className="rounded-2xl border bg-background p-5 shadow-sm flex flex-wrap gap-2"
              initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}
            >
              <h2 className="w-full font-semibold text-sm mb-1">Actions</h2>
              {canConfirm && (
                <Button size="sm" className="gap-1.5" disabled={pending} onClick={markConfirmed}>
                  <CheckCircle2 className="h-4 w-4" /> Confirm appointment
                </Button>
              )}
              {canRemind && (
                <Button size="sm" variant="outline" className="gap-1.5" disabled={pending} onClick={sendReminder}>
                  <Bell className="h-4 w-4" /> Send reminder
                </Button>
              )}
              {canCancel && (
                <Button size="sm" variant="outline" className="gap-1.5 text-destructive hover:text-destructive border-destructive/30 hover:bg-destructive/5" disabled={pending} onClick={markCancelled}>
                  Cancel appointment
                </Button>
              )}
              {!canConfirm && !canRemind && !canCancel && (
                <p className="text-sm text-muted-foreground">No actions available for this appointment status.</p>
              )}
            </motion.div>
          </>
        ) : null}
      </main>

      {/* Toast */}
      {toast && (
        <motion.div
          className="fixed bottom-5 right-5 z-50 flex items-center gap-2.5 rounded-2xl border bg-background px-4 py-3 shadow-xl text-sm font-medium"
          initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
        >
          <div className="h-2 w-2 rounded-full bg-emerald-500" />
          {toast}
        </motion.div>
      )}
    </div>
  )
}
