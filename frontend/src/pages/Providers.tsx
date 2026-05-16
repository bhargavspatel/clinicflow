import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { motion } from "framer-motion"
import { ArrowLeft, Stethoscope, CheckCircle2, XCircle } from "lucide-react"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"

interface Provider {
  id: string
  specialty: string
  is_active: boolean
  created_at: string
}

const SPECIALTY_COLORS: Record<string, string> = {
  Orthopedics: "bg-blue-50 text-blue-700 border-blue-200",
  Cardiology: "bg-red-50 text-red-700 border-red-200",
  Neurology: "bg-violet-50 text-violet-700 border-violet-200",
  Dermatology: "bg-orange-50 text-orange-700 border-orange-200",
  Pediatrics: "bg-emerald-50 text-emerald-700 border-emerald-200",
}

function specialtyColor(s: string) {
  return SPECIALTY_COLORS[s] ?? "bg-slate-50 text-slate-700 border-slate-200"
}

export default function Providers() {
  const navigate = useNavigate()
  const [providers, setProviders] = useState<Provider[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get<Provider[]>("/providers")
      .then((r) => setProviders(r.data))
      .finally(() => setLoading(false))
  }, [])

  const active = providers.filter((p) => p.is_active)
  const inactive = providers.filter((p) => !p.is_active)

  return (
    <div className="min-h-screen bg-slate-50/50">
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
            <span className="font-semibold text-sm">Providers</span>
          </div>
        </div>
      </motion.header>

      <main className="mx-auto max-w-7xl px-4 sm:px-6 py-6 space-y-5">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
          <h1 className="text-xl font-semibold">Providers</h1>
          <p className="text-sm text-muted-foreground">{providers.length} total · {active.length} active</p>
        </motion.div>

        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="rounded-2xl border bg-background p-5 space-y-3">
                <Skeleton className="h-12 w-12 rounded-xl" />
                <Skeleton className="h-5 w-32" />
                <Skeleton className="h-4 w-20" />
              </div>
            ))}
          </div>
        ) : providers.length === 0 ? (
          <div className="rounded-2xl border bg-background py-20 flex flex-col items-center gap-3 text-muted-foreground">
            <Stethoscope className="h-10 w-10 opacity-30" />
            <p className="text-sm">No providers yet.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {providers.map((p, i) => (
              <motion.div
                key={p.id}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.07, duration: 0.4 }}
                whileHover={{ y: -2, transition: { duration: 0.2 } }}
                className="rounded-2xl border bg-background p-5 shadow-sm hover:shadow-md transition-shadow"
              >
                <div className="flex items-start justify-between mb-4">
                  <div className={`flex h-12 w-12 items-center justify-center rounded-xl border text-sm font-bold ${specialtyColor(p.specialty)}`}>
                    {p.specialty.slice(0, 2).toUpperCase()}
                  </div>
                  {p.is_active ? (
                    <Badge variant="success" className="gap-1">
                      <CheckCircle2 className="h-3 w-3" /> Active
                    </Badge>
                  ) : (
                    <Badge variant="muted" className="gap-1">
                      <XCircle className="h-3 w-3" /> Inactive
                    </Badge>
                  )}
                </div>
                <h3 className="font-semibold text-base">{p.specialty}</h3>
                <p className="text-xs text-muted-foreground mt-1">
                  Added {new Date(p.created_at).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}
                </p>
              </motion.div>
            ))}
          </div>
        )}

        {inactive.length > 0 && (
          <p className="text-xs text-muted-foreground text-center pt-2">
            {inactive.length} inactive provider{inactive.length > 1 ? "s" : ""} not shown in scheduling
          </p>
        )}
      </main>
    </div>
  )
}
