import { useEffect, useState, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import { motion, AnimatePresence } from "framer-motion"
import { Search, UserPlus, Phone, Mail, ArrowLeft, X } from "lucide-react"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"

interface Patient {
  id: string
  full_name: string
  phone: string
  email: string | null
  dob: string | null
  notes: string | null
  created_at: string
}

function fmtDob(dob: string | null) {
  if (!dob) return "—"
  return new Date(dob).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" })
}

function getInitials(name: string) {
  return name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase()
}

const COLORS = ["bg-blue-500", "bg-emerald-500", "bg-violet-500", "bg-orange-500", "bg-pink-500", "bg-teal-500"]
function avatarColor(name: string) {
  const i = name.charCodeAt(0) % COLORS.length
  return COLORS[i]
}

export default function Patients() {
  const navigate = useNavigate()
  const [patients, setPatients] = useState<Patient[]>([])
  const [total, setTotal] = useState(0)
  const [search, setSearch] = useState("")
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)

  // Add form state
  const [form, setForm] = useState({ full_name: "", phone: "", email: "", date_of_birth: "" })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchPatients = useCallback(async (q: string) => {
    setLoading(true)
    try {
      const params: Record<string, string> = { page_size: "50" }
      if (q) params.search = q
      const { data } = await api.get<{ items: Patient[]; total: number }>("/patients", { params })
      setPatients(data.items)
      setTotal(data.total)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => fetchPatients(search), 300)
    return () => clearTimeout(timer)
  }, [search, fetchPatients])

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      await api.post("/patients", {
        full_name: form.full_name,
        phone: form.phone,
        email: form.email || undefined,
        date_of_birth: form.date_of_birth || undefined,
      })
      setShowAdd(false)
      setForm({ full_name: "", phone: "", email: "", date_of_birth: "" })
      fetchPatients(search)
    } catch {
      setError("Failed to add patient. Check the fields and try again.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-50/50">
      {/* Header */}
      <motion.header
        className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur-md px-4 sm:px-6 py-3"
        initial={{ opacity: 0, y: -16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
      >
        <div className="mx-auto max-w-7xl flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => navigate("/dashboard")}>
              <ArrowLeft className="h-4 w-4" /> Dashboard
            </Button>
            <span className="text-muted-foreground">/</span>
            <span className="font-semibold text-sm">Patients</span>
          </div>
          <Button size="sm" className="gap-1.5" onClick={() => setShowAdd(true)}>
            <UserPlus className="h-4 w-4" /> Add patient
          </Button>
        </div>
      </motion.header>

      <main className="mx-auto max-w-7xl px-4 sm:px-6 py-6 space-y-5">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
          <h1 className="text-xl font-semibold">Patients</h1>
          <p className="text-sm text-muted-foreground">{total} total</p>
        </motion.div>

        {/* Search */}
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }} className="relative max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search patients…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9 h-10 rounded-xl"
          />
        </motion.div>

        {/* Table */}
        <motion.div
          className="rounded-2xl border bg-background shadow-sm overflow-hidden"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15, duration: 0.4 }}
        >
          {loading ? (
            <div className="divide-y">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="flex items-center gap-4 px-5 py-4">
                  <Skeleton className="h-10 w-10 rounded-full flex-shrink-0" />
                  <div className="space-y-1.5 flex-1">
                    <Skeleton className="h-4 w-36" />
                    <Skeleton className="h-3 w-24" />
                  </div>
                  <Skeleton className="h-4 w-28 hidden sm:block" />
                  <Skeleton className="h-4 w-36 hidden md:block" />
                </div>
              ))}
            </div>
          ) : patients.length === 0 ? (
            <div className="py-20 flex flex-col items-center gap-3 text-muted-foreground">
              <Search className="h-10 w-10 opacity-30" />
              <p className="text-sm">No patients found.</p>
            </div>
          ) : (
            <>
              <div className="hidden sm:grid grid-cols-12 gap-4 px-5 py-3 border-b bg-slate-50/80 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <div className="col-span-4">Patient</div>
                <div className="col-span-3">Phone</div>
                <div className="col-span-3">Email</div>
                <div className="col-span-2">Date of Birth</div>
              </div>
              <div className="divide-y">
                {patients.map((p, i) => (
                  <motion.div
                    key={p.id}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.03 }}
                    className="grid grid-cols-1 sm:grid-cols-12 gap-2 sm:gap-4 px-5 py-4 hover:bg-slate-50/80 transition-colors cursor-pointer items-center"
                    onClick={() => navigate(`/patients/${p.id}`)}
                  >
                    <div className="col-span-4 flex items-center gap-3">
                      <div className={`flex h-10 w-10 items-center justify-center rounded-full text-white text-sm font-semibold flex-shrink-0 ${avatarColor(p.full_name)}`}>
                        {getInitials(p.full_name)}
                      </div>
                      <div>
                        <p className="font-medium text-sm">{p.full_name}</p>
                        <p className="text-xs text-muted-foreground sm:hidden">{p.phone}</p>
                      </div>
                    </div>
                    <div className="col-span-3 hidden sm:flex items-center gap-1.5 text-sm text-muted-foreground">
                      <Phone className="h-3.5 w-3.5 flex-shrink-0" />{p.phone}
                    </div>
                    <div className="col-span-3 hidden md:flex items-center gap-1.5 text-sm text-muted-foreground truncate">
                      <Mail className="h-3.5 w-3.5 flex-shrink-0" />{p.email ?? "—"}
                    </div>
                    <div className="col-span-2 hidden sm:block text-sm text-muted-foreground">{fmtDob(p.dob)}</div>
                  </motion.div>
                ))}
              </div>
            </>
          )}
        </motion.div>
      </main>

      {/* Add Patient Modal */}
      <AnimatePresence>
        {showAdd && (
          <>
            <motion.div
              className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              onClick={() => setShowAdd(false)}
            />
            <motion.div
              className="fixed inset-0 z-50 flex items-center justify-center p-4"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            >
              <motion.div
                className="bg-background rounded-2xl shadow-2xl w-full max-w-md p-6"
                initial={{ scale: 0.95, y: 16 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, y: 16 }}
                transition={{ duration: 0.2 }}
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex items-center justify-between mb-5">
                  <h2 className="text-lg font-semibold">Add new patient</h2>
                  <button onClick={() => setShowAdd(false)} className="text-muted-foreground hover:text-foreground">
                    <X className="h-5 w-5" />
                  </button>
                </div>
                <form onSubmit={handleAdd} className="space-y-4">
                  <div className="space-y-1.5">
                    <Label>Full name *</Label>
                    <Input placeholder="Jane Smith" required value={form.full_name} onChange={(e) => setForm(f => ({ ...f, full_name: e.target.value }))} className="h-10" />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Phone *</Label>
                    <Input placeholder="+15551234567" required value={form.phone} onChange={(e) => setForm(f => ({ ...f, phone: e.target.value }))} className="h-10" />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Email</Label>
                    <Input type="email" placeholder="jane@email.com" value={form.email} onChange={(e) => setForm(f => ({ ...f, email: e.target.value }))} className="h-10" />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Date of birth</Label>
                    <Input type="date" value={form.date_of_birth} onChange={(e) => setForm(f => ({ ...f, date_of_birth: e.target.value }))} className="h-10" />
                  </div>
                  {error && <p className="text-sm text-destructive bg-destructive/10 rounded-lg px-3 py-2">{error}</p>}
                  <div className="flex gap-2 pt-1">
                    <Button type="button" variant="outline" className="flex-1" onClick={() => setShowAdd(false)}>Cancel</Button>
                    <Button type="submit" className="flex-1" disabled={saving}>{saving ? "Saving…" : "Add patient"}</Button>
                  </div>
                </form>
              </motion.div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  )
}
