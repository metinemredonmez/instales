import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Org } from "@/lib/api"
import type { User } from "@/lib/auth"
import { LangProvider } from "@/lib/i18n"

const orgAccept = vi.fn<(token: string) => Promise<Org>>()
vi.mock("@/lib/api", async (orig) => ({ ...(await orig<typeof import("@/lib/api")>()), api: { orgAccept: (t: string) => orgAccept(t) } }))
let user: User | null = null
const refreshUser = vi.fn<() => Promise<void>>()
vi.mock("@/lib/auth", async (orig) => ({ ...(await orig<typeof import("@/lib/auth")>()), useAuth: () => ({ user, refreshUser }) }))

import { ApiError } from "@/lib/api"
import { OrgAcceptPage } from "./OrgAcceptPage"

const ACME: Org = { id: 1, name: "Acme", owner_user_id: 3, is_owner: false, plan: "PRO_PLUS", seats: 10, seats_used: 2, created_at: "2026-09-01", members: [] }
let lastSearch = ""
function Probe() { lastSearch = useLocation().search; return null }

function setup(path: string) {
  localStorage.setItem("instilens.lang", "tr")
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <LangProvider>
        <MemoryRouter initialEntries={[path]}>
          <Probe />
          <Routes><Route path="/org/accept" element={<OrgAcceptPage />} /></Routes>
        </MemoryRouter>
      </LangProvider>
    </QueryClientProvider>,
  )
}

describe("OrgAcceptPage", () => {
  beforeEach(() => { orgAccept.mockReset(); refreshUser.mockReset(); refreshUser.mockResolvedValue(undefined); user = { id: 9, email: "m@x.com", name: "M", plan: "FREE" }; lastSearch = "" })
  afterEach(() => localStorage.removeItem("instilens.lang"))

  it("posts the mailed token once, takes it off the address bar, and names the organisation joined", async () => {
    orgAccept.mockResolvedValue(ACME)
    setup("/org/accept?token=abcdefghijklmnopqrstuvwxyz012345")
    expect(await screen.findByText("Acme kurumuna katıldın.")).toBeInTheDocument()
    expect(orgAccept).toHaveBeenCalledTimes(1)
    expect(orgAccept).toHaveBeenCalledWith("abcdefghijklmnopqrstuvwxyz012345")
    await waitFor(() => expect(lastSearch).toBe(""))
    await waitFor(() => expect(refreshUser).toHaveBeenCalled())  // the inherited plan reaches the session
    expect(screen.getByRole("link", { name: "Plan" })).toHaveAttribute("href", "/plan")
  })

  it("shows the API's reason when the link is stale, for the wrong or an unverified address, or an account already in a team", async () => {
    orgAccept.mockRejectedValue(new ApiError(409, "verify your e-mail address first", "409 verify your e-mail address first"))
    setup("/org/accept?token=abcdefghijklmnopqrstuvwxyz012345")
    expect(await screen.findByText("Davet kabul edilemedi.")).toBeInTheDocument()
    expect(screen.getByText("verify your e-mail address first")).toBeInTheDocument()
  })

  it("without a token nothing is posted", async () => {
    setup("/org/accept")
    expect(await screen.findByText(/Bu bağlantıda davet kodu yok/)).toBeInTheDocument()
    expect(orgAccept).not.toHaveBeenCalled()
  })
})
