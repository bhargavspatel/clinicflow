import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import Dashboard from "@/pages/Dashboard"

// ── Mocks ─────────────────────────────────────────────────────────────────────
// vi.mock factories are hoisted before const declarations, so mocks that the
// factory references must be created with vi.hoisted().

const mockGet = vi.hoisted(() => vi.fn())

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return { ...actual, useNavigate: () => vi.fn() }
})

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", email: "doc@clinic.com", role: "owner", tenant_id: "t1" },
    isLoading: false,
    logout: vi.fn(),
  }),
}))

vi.mock("@/hooks/useClinicSocket", () => ({
  useClinicSocket: vi.fn(),
}))

vi.mock("@/lib/api", () => ({
  api: { get: mockGet, patch: vi.fn(), post: vi.fn() },
  getAccessToken: () => "mock-token",
}))

// ── Fixtures ──────────────────────────────────────────────────────────────────

const PROVIDERS = [{ id: "pv1", specialty: "Cardiology", is_active: true }]

const PATIENTS = {
  items: [
    { id: "p1", full_name: "John Smith" },
    { id: "p2", full_name: "Jane Doe" },
  ],
}

const APPOINTMENTS = {
  items: [
    {
      id: "a1",
      patient_id: "p1",
      provider_id: "pv1",
      scheduled_at: "2026-05-13T09:00:00Z",
      appointment_type: "follow_up",
      status: "confirmed",
      risk_score: 82,
      risk_bucket: "high",
    },
    {
      id: "a2",
      patient_id: "p2",
      provider_id: "pv1",
      scheduled_at: "2026-05-13T10:00:00Z",
      appointment_type: "initial_eval",
      status: "scheduled",
      risk_score: 18,
      risk_bucket: "low",
    },
    {
      id: "a3",
      patient_id: "p1",
      provider_id: "pv1",
      scheduled_at: "2026-05-13T11:00:00Z",
      appointment_type: "consultation",
      status: "rescheduled",
      risk_score: 55,
      risk_bucket: "medium",
    },
  ],
}

function setupMockGet() {
  mockGet.mockImplementation(
    (url: string, config?: { params?: Record<string, string | number> }) => {
      if (url === "/providers") return Promise.resolve({ data: PROVIDERS })
      if (url.startsWith("/patients")) return Promise.resolve({ data: PATIENTS })
      if (url.startsWith("/appointments")) {
        if (config?.params?.status === "no_show") {
          return Promise.resolve({ data: { total: 5 } })
        }
        return Promise.resolve({ data: APPOINTMENTS })
      }
      return Promise.resolve({ data: {} })
    },
  )
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("Dashboard", () => {
  beforeEach(() => {
    mockGet.mockReset()
    setupMockGet()
  })

  it("renders appointment rows with patient names", async () => {
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    )

    // p1 (John Smith) appears in rows a1 and a3; use getAllByText
    await waitFor(() => {
      expect(screen.getAllByText("John Smith").length).toBeGreaterThan(0)
      expect(screen.getAllByText("Jane Doe").length).toBeGreaterThan(0)
    })
  })

  it("renders provider specialty in each row", async () => {
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getAllByText("Cardiology").length).toBeGreaterThan(0)
    })
  })

  it("renders high-risk badge with red background", async () => {
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    )

    await waitFor(() => {
      const badge = screen.getByText("high")
      expect(badge).toHaveClass("bg-red-100")
      expect(badge).toHaveClass("text-red-800")
    })
  })

  it("renders low-risk badge with green background", async () => {
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    )

    await waitFor(() => {
      const badge = screen.getByText("low")
      expect(badge).toHaveClass("bg-green-100")
      expect(badge).toHaveClass("text-green-800")
    })
  })

  it("renders medium-risk badge with amber background", async () => {
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    )

    await waitFor(() => {
      const badge = screen.getByText("medium")
      expect(badge).toHaveClass("bg-amber-100")
      expect(badge).toHaveClass("text-amber-800")
    })
  })

  it("shows no-shows-this-week count in summary card", async () => {
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText("5")).toBeInTheDocument()
    })
  })
})
