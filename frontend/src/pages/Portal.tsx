import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { CheckCircle2, AlertTriangle } from "lucide-react"
import { api, callWithToken } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"

// ── API types ─────────────────────────────────────────────────────────────────

interface PortalTokenResponse {
  access_token: string
  patient_id: string
  appointment_id: string
}

interface AppointmentDetail {
  id: string
  scheduled_at: string
  appointment_type: string
  status: string
  duration_minutes: number
  patient_name: string
  provider_specialty: string
  provider_id: string
}

interface SlotsResponse {
  provider_id: string
  slots: string[]
}

interface RescheduleOut {
  id: string
  scheduled_at: string
  appointment_type: string
  status: string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const TYPE_LABELS: Record<string, string> = {
  initial_eval: "Initial Evaluation",
  follow_up: "Follow-up",
  consultation: "Consultation",
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  })
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  })
}

function fmtDayLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  })
}

interface SlotGroup {
  dateLabel: string
  slots: string[]
}

function groupSlots(slots: string[]): SlotGroup[] {
  const map = new Map<string, string[]>()
  for (const s of slots) {
    const key = fmtDayLabel(s)
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(s)
  }
  return Array.from(map.entries()).map(([dateLabel, slots]) => ({ dateLabel, slots }))
}

// ── Sub-components ────────────────────────────────────────────────────────────

function PageShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-muted/30 px-4 py-10">
      <div className="w-full max-w-lg space-y-6">
        <div className="text-center">
          <p className="text-lg font-semibold tracking-tight">ClinicFlow</p>
          <p className="text-sm text-muted-foreground">Patient Portal</p>
        </div>
        {children}
      </div>
    </div>
  )
}

function LoadingCard() {
  return (
    <PageShell>
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-12">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Verifying your link…</p>
        </CardContent>
      </Card>
    </PageShell>
  )
}

function ErrorCard({ message }: { message: string }) {
  const expired = message === "expired"
  return (
    <PageShell>
      <Card>
        <CardContent className="flex flex-col items-center gap-4 py-10 text-center">
          <AlertTriangle className="h-10 w-10 text-amber-500" />
          <div>
            <p className="font-semibold text-lg">
              {expired ? "This link has expired" : "Link not valid"}
            </p>
            <p className="mt-1 text-sm text-muted-foreground max-w-sm">
              {expired
                ? "Appointment links expire after 30 minutes for your security. Please contact your clinic to receive a new link."
                : "We couldn't verify this link. It may have already been used or may be invalid. Please contact your clinic directly."}
            </p>
          </div>
        </CardContent>
      </Card>
    </PageShell>
  )
}

function SuccessCard({ scheduledAt }: { scheduledAt: string }) {
  return (
    <PageShell>
      <Card>
        <CardContent className="flex flex-col items-center gap-4 py-10 text-center">
          <CheckCircle2 className="h-12 w-12 text-green-500" />
          <div>
            <p className="font-semibold text-lg">Appointment Rescheduled</p>
            <p className="mt-1 text-sm text-muted-foreground">Your appointment has been moved to</p>
            <p className="mt-2 font-medium text-base">{fmtDate(scheduledAt)}</p>
            <p className="text-sm text-muted-foreground">{fmtTime(scheduledAt)}</p>
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            You'll receive a new reminder before your appointment.
          </p>
        </CardContent>
      </Card>
    </PageShell>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

type Step = "loading" | "ready" | "success" | "error"

export default function Portal() {
  const [searchParams] = useSearchParams()

  const [step, setStep] = useState<Step>("loading")
  const [portalToken, setPortalToken] = useState<string | null>(null)
  const [appointment, setAppointment] = useState<AppointmentDetail | null>(null)
  const [slotGroups, setSlotGroups] = useState<SlotGroup[]>([])
  const [slotsLoading, setSlotsLoading] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [successAt, setSuccessAt] = useState<string | null>(null)
  const [errorKey, setErrorKey] = useState<string>("error")
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    const magicToken = searchParams.get("token")
    if (!magicToken) {
      setErrorKey("invalid")
      setStep("error")
      return
    }
    void verifyAndLoad(magicToken)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function verifyAndLoad(magicToken: string) {
    try {
      // 1. Exchange magic-link token for a portal JWT
      const { data: verifyData } = await api.get<PortalTokenResponse>(
        `/auth/magic-link/verify?token=${encodeURIComponent(magicToken)}`,
      )
      const jwt = verifyData.access_token
      setPortalToken(jwt)

      // 2. Fetch appointment details + slots in parallel
      const [appt] = await Promise.all([
        callWithToken<AppointmentDetail>("GET", "/portal/appointment", jwt),
      ])
      setAppointment(appt)

      // 3. Load slots separately (non-blocking — show page first)
      setStep("ready")
      setSlotsLoading(true)
      try {
        const slotsData = await callWithToken<SlotsResponse>(
          "GET",
          `/portal/slots?provider_id=${appt.provider_id}&days_ahead=14`,
          jwt,
        )
        setSlotGroups(groupSlots(slotsData.slots))
      } finally {
        setSlotsLoading(false)
      }
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status
      setErrorKey(status === 400 || status === 401 || status === 410 ? "expired" : "error")
      setStep("error")
    }
  }

  async function handleConfirm() {
    if (!selected || !appointment || !portalToken) return
    setSubmitError(null)
    setSubmitting(true)
    try {
      const result = await callWithToken<RescheduleOut>("POST", "/portal/reschedule", portalToken, {
        body: { appointment_id: appointment.id, new_slot: selected },
      })
      setSuccessAt(result.scheduled_at)
      setStep("success")
    } catch {
      setSubmitError("Something went wrong. Please try again.")
    } finally {
      setSubmitting(false)
    }
  }

  // ── Render branches ───────────────────────────────────────────────────────

  if (step === "loading") return <LoadingCard />
  if (step === "error") return <ErrorCard message={errorKey} />
  if (step === "success" && successAt) return <SuccessCard scheduledAt={successAt} />

  if (!appointment) return <LoadingCard />

  const groups = slotGroups

  return (
    <PageShell>
      {/* Current appointment */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Your Appointment</CardTitle>
          <CardDescription>Hi {appointment.patient_name}</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-y-3 text-sm">
          <span className="text-muted-foreground">Date</span>
          <span className="font-medium">{fmtDate(appointment.scheduled_at)}</span>

          <span className="text-muted-foreground">Time</span>
          <span className="font-medium">{fmtTime(appointment.scheduled_at)}</span>

          <span className="text-muted-foreground">Type</span>
          <span>{TYPE_LABELS[appointment.appointment_type] ?? appointment.appointment_type}</span>

          <span className="text-muted-foreground">Provider</span>
          <span>{appointment.provider_specialty}</span>

          <span className="text-muted-foreground">Duration</span>
          <span>{appointment.duration_minutes} min</span>

          <span className="text-muted-foreground">Status</span>
          <span className="capitalize">{appointment.status.replace("_", " ")}</span>
        </CardContent>
      </Card>

      {/* Slot picker */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Reschedule</CardTitle>
          <CardDescription>
            Select a new time below — your appointment won't change until you confirm.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {slotsLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-muted border-t-primary" />
              Loading available times…
            </div>
          ) : groups.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4">
              No available slots in the next 14 days. Please contact your clinic.
            </p>
          ) : (
            groups.map((g) => (
              <div key={g.dateLabel}>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {g.dateLabel}
                </p>
                <div className="flex flex-wrap gap-2">
                  {g.slots.map((slot) => (
                    <Button
                      key={slot}
                      size="sm"
                      variant={selected === slot ? "default" : "outline"}
                      className="h-8 px-3 text-xs"
                      onClick={() => setSelected(slot === selected ? null : slot)}
                    >
                      {fmtTime(slot)}
                    </Button>
                  ))}
                </div>
              </div>
            ))
          )}

          {submitError && (
            <p role="alert" className="text-sm font-medium text-destructive">
              {submitError}
            </p>
          )}

          <Button
            className="w-full mt-2"
            disabled={!selected || submitting || slotsLoading}
            onClick={handleConfirm}
          >
            {submitting
              ? "Rescheduling…"
              : selected
                ? `Confirm — ${fmtDate(selected)} at ${fmtTime(selected)}`
                : "Select a time to continue"}
          </Button>
        </CardContent>
      </Card>
    </PageShell>
  )
}
