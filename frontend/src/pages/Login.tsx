import { FormEvent, useState } from "react"
import { useNavigate } from "react-router-dom"
import { motion } from "framer-motion"
import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

const fadeUp = {
  hidden: { opacity: 0, y: 24 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { duration: 0.5, delay: i * 0.1, ease: [0.25, 0.46, 0.45, 0.94] },
  }),
}

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()

  const [subdomain, setSubdomain] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setPending(true)
    try {
      await login(email, password, subdomain)
      navigate("/dashboard", { replace: true })
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status
      setError(status === 401 ? "Invalid email or password." : "Something went wrong. Try again.")
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="flex min-h-screen">
      {/* Left panel — branding */}
      <motion.div
        className="hidden lg:flex lg:w-1/2 relative overflow-hidden"
        style={{ background: "linear-gradient(135deg, #1e40af 0%, #3b82f6 50%, #06b6d4 100%)" }}
        initial={{ opacity: 0, x: -40 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.7, ease: [0.25, 0.46, 0.45, 0.94] }}
      >
        {/* Animated blobs */}
        <motion.div
          className="absolute -top-32 -left-32 w-96 h-96 rounded-full opacity-20"
          style={{ background: "rgba(255,255,255,0.3)" }}
          animate={{ scale: [1, 1.15, 1], rotate: [0, 45, 0] }}
          transition={{ duration: 12, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.div
          className="absolute -bottom-24 -right-24 w-80 h-80 rounded-full opacity-20"
          style={{ background: "rgba(255,255,255,0.2)" }}
          animate={{ scale: [1, 1.2, 1], rotate: [0, -60, 0] }}
          transition={{ duration: 15, repeat: Infinity, ease: "easeInOut", delay: 2 }}
        />
        <motion.div
          className="absolute top-1/2 left-1/2 w-64 h-64 rounded-full opacity-10"
          style={{ background: "rgba(255,255,255,0.4)", transform: "translate(-50%,-50%)" }}
          animate={{ scale: [1, 1.3, 1] }}
          transition={{ duration: 8, repeat: Infinity, ease: "easeInOut", delay: 1 }}
        />

        {/* Content */}
        <div className="relative z-10 flex flex-col justify-center px-16 text-white">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.3 }}
          >
            <div className="mb-8 flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/20 backdrop-blur-sm">
                <svg viewBox="0 0 24 24" fill="none" className="h-7 w-7 text-white" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                </svg>
              </div>
              <span className="text-2xl font-bold tracking-tight">ClinicFlow</span>
            </div>

            <h1 className="text-4xl font-bold leading-tight mb-4">
              Reduce no-shows.<br />Run a smarter clinic.
            </h1>
            <p className="text-lg text-blue-100 leading-relaxed mb-10">
              AI-powered risk scoring and personalized SMS reminders — purpose-built for independent specialty clinics.
            </p>

            <div className="space-y-4">
              {[
                { icon: "📊", text: "Real-time appointment risk scoring" },
                { icon: "💬", text: "Automated personalized SMS reminders" },
                { icon: "📅", text: "Patient portal & self-reschedule" },
              ].map((item, i) => (
                <motion.div
                  key={i}
                  className="flex items-center gap-3 text-blue-100"
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.6 + i * 0.15, duration: 0.5 }}
                >
                  <span className="text-xl">{item.icon}</span>
                  <span className="text-sm">{item.text}</span>
                </motion.div>
              ))}
            </div>
          </motion.div>
        </div>
      </motion.div>

      {/* Right panel — form */}
      <div className="flex w-full lg:w-1/2 flex-col items-center justify-center px-6 sm:px-12 bg-background">
        <div className="w-full max-w-md">
          {/* Mobile logo */}
          <motion.div
            className="mb-8 flex lg:hidden items-center gap-2"
            initial={{ opacity: 0, y: -16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
          >
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary">
              <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5 text-white" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
              </svg>
            </div>
            <span className="text-xl font-bold">ClinicFlow</span>
          </motion.div>

          <motion.div custom={0} variants={fadeUp} initial="hidden" animate="show">
            <h2 className="text-3xl font-bold tracking-tight text-foreground">Welcome back</h2>
            <p className="mt-2 text-sm text-muted-foreground">Sign in to your clinic account to continue</p>
          </motion.div>

          <form onSubmit={handleSubmit} className="mt-8 space-y-5">
            <motion.div custom={1} variants={fadeUp} initial="hidden" animate="show" className="space-y-1.5">
              <Label htmlFor="subdomain" className="text-sm font-medium">Clinic subdomain</Label>
              <Input
                id="subdomain"
                type="text"
                autoComplete="organization"
                placeholder="demo"
                required
                value={subdomain}
                onChange={(e) => setSubdomain(e.target.value.toLowerCase())}
                className="h-11 text-sm transition-shadow focus:shadow-md"
              />
            </motion.div>

            <motion.div custom={2} variants={fadeUp} initial="hidden" animate="show" className="space-y-1.5">
              <Label htmlFor="email" className="text-sm font-medium">Email address</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                placeholder="you@clinic.com"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="h-11 text-sm transition-shadow focus:shadow-md"
              />
            </motion.div>

            <motion.div custom={3} variants={fadeUp} initial="hidden" animate="show" className="space-y-1.5">
              <Label htmlFor="password" className="text-sm font-medium">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="h-11 text-sm transition-shadow focus:shadow-md"
              />
            </motion.div>

            {error && (
              <motion.p
                role="alert"
                className="rounded-lg bg-destructive/10 px-4 py-3 text-sm font-medium text-destructive"
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.2 }}
              >
                {error}
              </motion.p>
            )}

            <motion.div custom={4} variants={fadeUp} initial="hidden" animate="show">
              <Button
                type="submit"
                className="w-full h-11 text-sm font-semibold transition-all duration-200 hover:shadow-lg hover:shadow-primary/25 active:scale-[0.98]"
                disabled={pending}
              >
                {pending ? (
                  <span className="flex items-center gap-2">
                    <motion.span
                      className="inline-block h-4 w-4 rounded-full border-2 border-white/30 border-t-white"
                      animate={{ rotate: 360 }}
                      transition={{ duration: 0.8, repeat: Infinity, ease: "linear" }}
                    />
                    Signing in…
                  </span>
                ) : (
                  "Sign in"
                )}
              </Button>
            </motion.div>
          </form>

          <motion.p
            className="mt-8 text-center text-xs text-muted-foreground"
            custom={5} variants={fadeUp} initial="hidden" animate="show"
          >
            Protected by enterprise-grade security
          </motion.p>
        </div>
      </div>
    </div>
  )
}
