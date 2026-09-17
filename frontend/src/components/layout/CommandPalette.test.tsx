import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { SearchHit } from "@/lib/api"
import { onOpenChart } from "@/lib/chart"

const search = vi.fn<(market: string, q: string) => Promise<SearchHit[]>>()
vi.mock("@/lib/api", () => ({ api: { search: (m: string, q: string) => search(m, q), ttsStatus: async () => ({ provider: null }) } }))

import { CommandPalette } from "./CommandPalette"

const HITS: SearchHit[] = [{ kind: "stock", key: "TR:ASELS", label: "ASELS", name: "Aselsan", href: "/stocks/ASELS" }]

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.search}</div> }

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/"]}>
        <CommandPalette />
        <Routes><Route path="*" element={<Where />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return userEvent.setup()
}

describe("CommandPalette", () => {
  beforeEach(() => { search.mockReset(); search.mockResolvedValue(HITS) })

  it("⌘K opens it with the commands; a hit comes first and Enter opens it", async () => {
    const user = setup()
    expect(screen.queryByRole("combobox")).toBeNull()
    await user.keyboard("{Meta>}k{/Meta}")
    const box = await screen.findByRole("combobox")
    expect(screen.getByRole("group", { name: "Sayfalar" })).toBeInTheDocument()
    await user.type(box, "asel")
    await waitFor(() => expect(screen.getAllByRole("option")[0]).toHaveTextContent("ASELS"))
    expect(screen.getAllByRole("option").at(-1)).toHaveTextContent("Metinlerde ara: “asel”")
    await user.keyboard("{Enter}")
    expect(screen.getByTestId("where").textContent).toBe("/stocks/ASELS")
    expect(screen.queryByRole("combobox")).toBeNull()
  })

  it("a stock hit adds an “open chart” action that asks the chart window for that symbol in the active market", async () => {
    const opened = vi.fn()
    const off = onOpenChart(opened)
    const user = setup()
    await user.keyboard("{Meta>}k{/Meta}")
    await user.type(await screen.findByRole("combobox"), "asel")
    const action = await screen.findByRole("option", { name: /Grafik aç: ASELS/ })
    expect(screen.getAllByRole("option").indexOf(action)).toBe(screen.getAllByRole("option").length - 2)   // just above the text search
    await user.click(action)
    expect(opened).toHaveBeenCalledWith({ symbol: "ASELS", market: "TR" })
    expect(screen.queryByRole("combobox")).toBeNull()
    off()
  })

  it("with three or more characters and no hit or command, the empty state keeps its “try the page” button above the text-search row", async () => {
    search.mockResolvedValue([])
    const user = setup()
    await user.keyboard("{Meta>}k{/Meta}")
    await user.type(await screen.findByRole("combobox"), "xyz")
    await waitFor(() => expect(search).toHaveBeenLastCalledWith("TR", "xyz"))
    const tryPage = await screen.findByRole("button", { name: "“XYZ” sayfasını dene" })
    expect(screen.getByText(/Eşleşme yok/)).toBeInTheDocument()
    expect(screen.getAllByRole("option")).toHaveLength(1)
    expect(screen.getByRole("option")).toHaveTextContent("Metinlerde ara: “xyz”")
    await user.click(tryPage)
    expect(screen.getByTestId("where").textContent).toBe("/funds/XYZ")   // TR + 3 letters → fund code

    await user.keyboard("{Meta>}k{/Meta}")
    await user.type(await screen.findByRole("combobox"), "temettü")
    await waitFor(() => expect(screen.getByRole("option")).toHaveTextContent("Metinlerde ara: “temettü”"))
    expect(await screen.findByRole("button", { name: "“TEMETTÜ” sayfasını dene" })).toBeInTheDocument()   // once the fetch settles
    await user.keyboard("{Enter}")   // the highlighted row is the text search, not the guess
    expect(screen.getByTestId("where").textContent).toBe("/search?q=temett%C3%BC")
  })
})
