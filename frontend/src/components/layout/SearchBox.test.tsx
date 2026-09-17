import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { SearchHit } from "@/lib/api"

const search = vi.fn<(market: string, q: string) => Promise<SearchHit[]>>()
vi.mock("@/lib/api", () => ({ api: { search: (m: string, q: string) => search(m, q) } }))

import { SearchBox } from "./SearchBox"

const HITS: SearchHit[] = [
  { kind: "stock", key: "TR:ASELS", label: "ASELS", name: "Aselsan", href: "/stocks/ASELS" },
  { kind: "fund", key: "TMV", label: "TMV", name: "Örnek fon", href: "/funds/TMV" },
  { kind: "institution", key: "TR:ISP", label: "ISP", name: "İş Portföy", href: "/institutions/ISP" },
]

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.search}</div> }

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/"]}>
        <SearchBox />
        <Routes><Route path="*" element={<Where />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return userEvent.setup()
}

describe("SearchBox keyboard navigation", () => {
  beforeEach(() => { search.mockReset(); search.mockResolvedValue(HITS) })

  it("opens results after typing and Enter opens the first hit", async () => {
    const user = setup()
    const box = screen.getByRole("combobox")
    await user.type(box, "as")
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(3))
    expect(search).toHaveBeenLastCalledWith("TR", "as")
    expect(screen.getAllByRole("option")[0]).toHaveAttribute("aria-selected", "true")
    await user.keyboard("{Enter}")
    expect(screen.getByTestId("where").textContent).toBe("/stocks/ASELS")
    expect(box).toHaveValue("")                 // cleared after navigating
    expect(screen.queryByRole("listbox")).toBeNull()
  })

  it("ArrowDown / ArrowUp move the highlight and clamp at the ends", async () => {
    const user = setup()
    const box = screen.getByRole("combobox")
    await user.type(box, "tm")
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(3))
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}")
    let opts = screen.getAllByRole("option")
    expect(opts[2]).toHaveAttribute("aria-selected", "true")
    await user.keyboard("{ArrowUp}")
    opts = screen.getAllByRole("option")
    expect(opts[1]).toHaveAttribute("aria-selected", "true")
    await user.keyboard("{Enter}")
    expect(screen.getByTestId("where").textContent).toBe("/funds/TMV")
  })

  it("Escape closes the list; with no hits the empty state's button opens the route guessed from the shape", async () => {
    search.mockResolvedValue([])
    const user = setup()
    const box = screen.getByRole("combobox")
    await user.type(box, "xy")
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument())
    await user.keyboard("{Escape}")
    expect(screen.queryByRole("listbox")).toBeNull()
    expect(box).not.toHaveFocus()               // Escape also blurs
    await user.click(box)
    await user.keyboard("{Enter}")
    expect(screen.getByTestId("where").textContent).toBe("/stocks/XY")   // under three characters there is no text row: the shape decides
    await user.type(box, "xyz")
    await user.click(await screen.findByRole("button", { name: "“XYZ” sayfasını dene" }))   // the empty state, once the fetch settles
    expect(screen.getByTestId("where").textContent).toBe("/funds/XYZ")   // TR + 3 letters → fund code
  })

  it("from three characters on the last row searches the texts; with no hits it is what Enter opens", async () => {
    const user = setup()
    const box = screen.getByRole("combobox")
    await user.type(box, "asel")
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(4))
    const last = screen.getAllByRole("option")[3]
    expect(last).toHaveTextContent("Metinlerde ara: “asel”")
    expect(screen.getAllByRole("option")[0]).toHaveAttribute("aria-selected", "true")   // the hits come first
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}")
    expect(screen.getAllByRole("option")[3]).toHaveAttribute("aria-selected", "true")
    await user.keyboard("{Enter}")
    expect(screen.getByTestId("where").textContent).toBe("/search?q=asel")
    expect(box).toHaveValue("")
    search.mockResolvedValue([])
    await user.type(box, "temettü")
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(1))
    await user.keyboard("{Enter}")
    expect(screen.getByTestId("where").textContent).toBe("/search?q=temett%C3%BC")
  })
})
