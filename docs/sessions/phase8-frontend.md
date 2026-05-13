# Phase 8 — Frontend

## What was built

React 18 + Vite + TypeScript SPA with TailwindCSS and shadcn/ui. Three route-level
pages: Login, Dashboard, and Portal. Fourteen reusable UI components (shadcn/ui
wrappers), a WebSocket hook, an Axios API client with JWT interceptors, and an auth
context. Fifteen Vitest + RTL tests covering all three pages.

---

## File structure

```
frontend/src/
  lib/
    api.ts          Axios instance + callWithToken helper
    auth.tsx        AuthProvider, useAuth, login/logout
  hooks/
    useClinicSocket.ts  WebSocket connection + reconnect logic
  pages/
    Login.tsx
    Dashboard.tsx
    Portal.tsx
  components/ui/
    badge.tsx button.tsx card.tsx input.tsx label.tsx select.tsx
    avatar.tsx dialog.tsx dropdown-menu.tsx separator.tsx
    toast.tsx toaster.tsx use-toast.ts tooltip.tsx
  test/
    setup.ts
  __tests__/
    Login.test.tsx
    Dashboard.test.tsx
    Portal.test.tsx
  App.tsx           Router + ProtectedRoute
  main.tsx
```

---

## Components

### Login.tsx
Controlled email + password form. Calls `useAuth().login()` (POST `/auth/login` then
GET `/users/me`). On success redirects to `/dashboard` via `useNavigate`. Distinguishes
401 ("Invalid email or password.") from all other errors ("Something went wrong.").
Error rendered with `role="alert"` so RTL can query it by role.

### Dashboard.tsx
Main staff view. Four summary cards at the top: Total Today, Confirmed (green), High
Risk (red), No-shows This Week (orange). Below: a filter bar (date picker, provider
select, status select, risk bucket select) and an appointment table.

**Appointment table columns:** time, patient name, provider specialty, appointment type,
risk badge, status badge, quick actions.

**Quick actions:**
- Confirm — visible on `scheduled` and `rescheduled` rows; PATCH status → `confirmed`
- Remind — visible on `scheduled`, `confirmed`, `rescheduled`; POST to send reminder
- View — always visible; navigates to appointment detail

**Data loading:** on mount and on filter change, fetches `/providers`, `/patients`, and
`/appointments` in parallel, builds `patients: Record<id, name>` and
`providers: Record<id, specialty>` lookup maps. A second appointments call with
`status=no_show` fetches only `total` for the summary card.

**Risk badge colors:** `low` → green-100/green-800, `medium` → amber-100/amber-800,
`high` → red-100/red-800.

**Status badge colors:** `scheduled` → blue, `confirmed` → green, `rescheduled` →
orange, `completed` / `cancelled` → muted, `no_show` → red.

### Portal.tsx
Public page — no auth context, no ProtectedRoute. Steps: `loading → ready → success →
error`.

1. Reads `?token=` from the URL.
2. GET `/auth/magic-link/verify?token=...` → receives a short-lived portal JWT.
3. `callWithToken('GET', '/portal/appointment', jwt)` → appointment details.
4. Loads available slots non-blocking; groups them by date for the slot picker UI.
5. Patient selects a slot → POST `/portal/reschedule` → success screen.
6. 400/401/410 → "This link has expired" screen with 30-minute expiry message.
7. Other errors or missing `?token` → "Link not valid" generic screen.

---

## Auth and token strategy

Tokens stored as module-level variables in `api.ts` (`_accessToken`, `_refreshToken`),
never in `localStorage` or `sessionStorage`. A request interceptor injects the Bearer
header. A response interceptor catches 401s, queues concurrent requests, calls
`POST /auth/refresh`, replays queued requests, or dispatches a custom `auth:logout`
window event on refresh failure. `AuthProvider` listens for that event to clear React
state without coupling the Axios layer to React.

`callWithToken` is a separate Axios call (bypasses the interceptor chain) used by
Portal. Portal JWTs have `type: "portal"` and are rejected by staff endpoints; staff
JWTs have `type: "access"` and are rejected by portal endpoints.

---

## WebSocket strategy

### Hook: `useClinicSocket(onMessage)`
Connects to `ws://localhost:8000/api/v1/ws?token=<jwt>`. The JWT is passed as a query
param because browser WebSocket APIs do not support custom headers.

**Reconnect:** exponential backoff starting at 1 s, doubling each attempt, capped at
30 s. Close code 4001 (auth failure from the server) stops reconnection entirely.

**Callback stability:** `onMessage` is stored in a `callbackRef` so the socket handler
always calls the current version of the callback without restarting the connection when
Dashboard re-renders.

**Cleanup:** on unmount sets `unmounted = true`, clears the backoff timer, and closes
the socket.

### Message types handled in Dashboard
- `appointment.status_changed` — refetches appointments and shows a toast
- `notification.sent` — shows a toast ("Reminder sent to {patient}")
- `notification.received` — shows a toast ("Reply from {patient}: {body}")

### Backend (built to support the hook)
`ws.py` endpoint subscribes to a dedicated Redis pub/sub connection on the channel
`clinicflow:ws:{tenant_id}`. Two concurrent asyncio tasks run: `_forward` (Redis →
WebSocket) and `_receive` (drains incoming WS frames). `asyncio.wait` with
`FIRST_COMPLETED` tears both down cleanly when either side disconnects.

Any process (API handler, Arq worker, webhook handler) publishes to the channel via
`ws_events.broadcast(redis, tenant_id, event, data)`. This works across process
boundaries — workers and API servers share nothing but Redis.

---

## UX decisions

| Decision | Rationale |
|---|---|
| Tokens in module memory, not localStorage | XSS cannot read JS module scope; localStorage is accessible to any script |
| Portal is a separate auth domain (portal JWT) | Patients must not be able to hit staff endpoints even with a valid token |
| Slot picker groups slots by day | Easier to scan than a flat list of ISO timestamps |
| `fetchRef` pattern in Dashboard | Keeps the WebSocket connection alive across filter changes without restarting it |
| Error screen distinguishes 400/401 from 5xx | Patients see a clear "link expired" message instead of a generic error for the common case |
| No-shows card uses a separate API call | Avoids loading all no-show appointments just to count them; uses `page_size=1` and reads `total` |
| `role="alert"` on Login error div | Accessible and queryable by RTL without relying on text content matching |

---

## Testing

15 tests across 3 files, all passing.

**Login.test.tsx (3 tests)**
- Success → navigate to /dashboard
- 401 → "Invalid email or password."
- 500 → "Something went wrong"

**Dashboard.test.tsx (6 tests)**
- Appointment rows render patient names
- Provider specialty appears in each row
- High-risk badge has red background classes
- Low-risk badge has green background classes
- Medium-risk badge has amber background classes
- No-shows summary card shows correct count

**Portal.test.tsx (6 tests)**
- 401 verify → "This link has expired"
- 400 verify → "This link has expired"
- 500 verify → "Link not valid"
- Pending verify → loading spinner
- Valid verify → appointment details (patient name, specialty, type)
- Missing `?token` → "Link not valid"

**Key testing patterns:**
- `vi.hoisted(() => vi.fn())` for mock functions referenced inside `vi.mock` factories
  (factories are hoisted before `const` declarations)
- `getAllByText` when the same text appears in multiple rows
- Regex matchers (`/Alice Johnson/i`) for text embedded in larger strings
- Unique fixture values to avoid `getByText` ambiguity across summary cards

---

## Open items / not built in this phase

- Appointment detail page (linked from View action but not yet implemented)
- Provider and patient management pages
- Billing / subscription UI (Stripe customer portal link)
- Dark mode
- Mobile-responsive layout
