import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import Portal from "@/pages/Portal"

// ── Mocks ─────────────────────────────────────────────────────────────────────

const mockGet = vi.hoisted(() => vi.fn())
const mockCallWithToken = vi.hoisted(() => vi.fn())

vi.mock("@/lib/api", () => ({
  api: { get: mockGet },
  callWithToken: mockCallWithToken,
}))

// ── Helpers ───────────────────────────────────────────────────────────────────

function renderPortal(search = "") {
  const path = search ? `/portal?token=${search}` : "/portal"
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/portal" element={<Portal />} />
      </Routes>
    </MemoryRouter>,
  )
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("Portal", () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockCallWithToken.mockReset()
  })

  it("shows expired-link screen when verify returns 401", async () => {
    mockGet.mockRejectedValue({ response: { status: 401 } })

    renderPortal("expired-magic-token")

    await waitFor(() => {
      expect(screen.getByText("This link has expired")).toBeInTheDocument()
    })
    expect(screen.getByText(/appointment links expire after 30 minutes/i)).toBeInTheDocument()
  })

  it("shows expired-link screen when verify returns 400", async () => {
    mockGet.mockRejectedValue({ response: { status: 400 } })

    renderPortal("used-token")

    await waitFor(() => {
      expect(screen.getByText("This link has expired")).toBeInTheDocument()
    })
  })

  it("shows generic error screen when verify returns 500", async () => {
    mockGet.mockRejectedValue({ response: { status: 500 } })

    renderPortal("bad-token")

    await waitFor(() => {
      expect(screen.getByText("Link not valid")).toBeInTheDocument()
    })
  })

  it("shows loading spinner while verifying", () => {
    mockGet.mockReturnValue(new Promise(() => {})) // never resolves

    renderPortal("pending-token")

    expect(screen.getByText(/verifying your link/i)).toBeInTheDocument()
  })

  it("shows appointment details on successful verification", async () => {
    mockGet.mockResolvedValue({
      data: {
        access_token: "portal-jwt",
        patient_id: "pat-1",
        appointment_id: "appt-1",
      },
    })

    mockCallWithToken.mockImplementation((_method: string, path: string) => {
      if (path === "/portal/appointment") {
        return Promise.resolve({
          id: "appt-1",
          scheduled_at: "2026-06-01T09:00:00Z",
          appointment_type: "follow_up",
          status: "scheduled",
          duration_minutes: 45,
          patient_name: "Alice Johnson",
          provider_specialty: "Neurology",
          provider_id: "pv-1",
        })
      }
      if (path.startsWith("/portal/slots")) {
        return Promise.resolve({ provider_id: "pv-1", slots: [] })
      }
    })

    renderPortal("valid-token")

    await waitFor(() => {
      expect(screen.getByText(/Alice Johnson/i)).toBeInTheDocument()
      expect(screen.getByText("Neurology")).toBeInTheDocument()
      expect(screen.getByText("Follow-up")).toBeInTheDocument()
    })
  })

  it("shows error screen when ?token param is absent", async () => {
    renderPortal() // no token in URL

    await waitFor(() => {
      expect(screen.getByText("Link not valid")).toBeInTheDocument()
    })
  })
})
