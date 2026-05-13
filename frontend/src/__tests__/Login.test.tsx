import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import Login from "@/pages/Login"

// ── Mocks ─────────────────────────────────────────────────────────────────────

const mockNavigate = vi.fn()

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return { ...actual, useNavigate: () => mockNavigate }
})

const mockLogin = vi.fn()

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ login: mockLogin, user: null, isLoading: false }),
}))

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("Login", () => {
  beforeEach(() => {
    mockLogin.mockReset()
    mockNavigate.mockReset()
  })

  it("redirects to /dashboard on successful login", async () => {
    mockLogin.mockResolvedValue(undefined)
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    )

    await user.type(screen.getByLabelText(/email/i), "doc@clinic.com")
    await user.type(screen.getByLabelText(/password/i), "correctpassword")
    await user.click(screen.getByRole("button", { name: /sign in/i }))

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/dashboard", { replace: true })
    })
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("shows 'Invalid email or password' on 401", async () => {
    mockLogin.mockRejectedValue({ response: { status: 401 } })
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    )

    await user.type(screen.getByLabelText(/email/i), "doc@clinic.com")
    await user.type(screen.getByLabelText(/password/i), "wrongpassword")
    await user.click(screen.getByRole("button", { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Invalid email or password.")
    })
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it("shows generic error on non-401 failure", async () => {
    mockLogin.mockRejectedValue({ response: { status: 500 } })
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    )

    await user.type(screen.getByLabelText(/email/i), "doc@clinic.com")
    await user.type(screen.getByLabelText(/password/i), "somepassword")
    await user.click(screen.getByRole("button", { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong")
    })
  })
})
