import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Warehouse, WarehouseBuild, WarehouseStatus } from "@/lib/api"
import { LangProvider } from "@/lib/i18n"
import { fmtDateTime, setLocale } from "@/lib/format"

const adminWarehouse = vi.fn<() => Promise<Warehouse>>()
const adminWarehouseBuild = vi.fn<() => Promise<WarehouseStatus & { started: boolean }>>()
const adminWarehouseDownload = vi.fn<(b: WarehouseBuild) => Promise<Blob>>()
const adminSaveSettings = vi.fn<(v: Record<string, unknown>) => Promise<{ changed: string[]; settings: never[] }>>()
vi.mock("@/lib/api", () => ({
  api: {
    adminWarehouse: () => adminWarehouse(),
    adminWarehouseBuild: () => adminWarehouseBuild(),
    adminWarehouseDownload: (b: WarehouseBuild) => adminWarehouseDownload(b),
    adminSaveSettings: (v: Record<string, unknown>) => adminSaveSettings(v),
  },
}))

import { WarehouseCard } from "./WarehouseCard"

const URL_BASE = "https://app.example/api/v1/admin/warehouse/download"
const build = (name: string, over: Partial<WarehouseBuild> = {}): WarehouseBuild => ({
  name, built_at: "2026-09-13T03:31:00Z", size: 184_000_000, rows: 1_234_567, row_counts: { instruments: 1_200, snapshot_holdings: 1_233_367 }, url: `${URL_BASE}/${name}`,
  git_rev: "a66db629d2f1", schema_version: 1, error: null, ...over,
})
const IDLE: WarehouseStatus = { running: false, started_by: null, started_at: null, last: null }
const RUNNING: WarehouseStatus = { running: true, started_by: "admin@instilens.app", started_at: "2026-09-17T10:00:00Z", last: null }
/** Two Sunday builds, the weekly job switched on, nothing running, the last outcome the newer file's. */
const DATA: Warehouse = {
  ...IDLE, enabled: true, keep: 4,
  last: { started_by: "scheduler", started_at: "2026-09-13T03:30:00Z", finished_at: "2026-09-13T03:31:00Z", name: "instilens-20260913.duckdb", rows: 1_234_567, seconds: 58.2, error: null },
  builds: [
    build("instilens-20260913.duckdb"),
    build("instilens-20260906.duckdb", { built_at: "2026-09-06T03:30:00Z", size: 179_500_000 }),
  ],
  latest: build("latest.duckdb"),
}

function setup(lang: "tr" | "en" = "tr", user: Parameters<typeof userEvent.setup>[0] = {}) {
  localStorage.setItem("instilens.lang", lang)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <LangProvider><WarehouseCard /></LangProvider>
    </QueryClientProvider>,
  )
  return userEvent.setup(user)
}

const rowOf = (name: string) => within(screen.getByText(name).closest("tr")!)

describe("WarehouseCard", () => {
  beforeEach(() => {
    adminWarehouse.mockReset(); adminWarehouseBuild.mockReset(); adminWarehouseDownload.mockReset(); adminSaveSettings.mockReset()
    adminWarehouse.mockResolvedValue(DATA)
    adminSaveSettings.mockResolvedValue({ changed: ["warehouse_enabled"], settings: [] })
  })
  afterEach(() => { localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("lists every build with its date, size and row count, newest first, and shows the weekly switch as the API reports it", async () => {
    setup()
    const table = await screen.findByRole("table")
    expect(screen.getByText("Veri deposu (DuckDB)")).toBeInTheDocument()
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Dosya", "Derleme", "Boyut", "Satır", ""])
    expect(within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent)).toEqual(["instilens-20260913.duckdb", "instilens-20260906.duckdb"])
    const newest = rowOf("instilens-20260913.duckdb").getAllByRole("cell").map((c) => c.textContent)
    expect(newest.slice(1, 4)).toEqual([fmtDateTime("2026-09-13T03:31:00Z"), "184.0 MB", "1.234.567"])   // the _meta total; the time in the viewer's zone
    expect(rowOf("instilens-20260906.duckdb").getAllByRole("cell").map((c) => c.textContent).slice(2, 4)).toEqual(["179.5 MB", "1.234.567"])
    expect(screen.getByRole("checkbox", { name: "Haftalık derleme (Pazar 03:30)" })).toBeChecked()
    expect(screen.getByRole("button", { name: "Şimdi derle" })).toBeEnabled()
    expect(screen.getByText(`Son derleme: instilens-20260913.duckdb · ${fmtDateTime("2026-09-13T03:31:00Z")} · scheduler · 58,2 sn`)).toBeInTheDocument()
    const howto = screen.getByText(/docs\/08-warehouse\.md/)
    expect(howto.textContent).toContain("duckdb --readonly instilens-YYYYMMDD.duckdb")   // a build is a snapshot; a writer would lock every other reader out
    expect(howto.textContent).toContain("En yeni 4 tarihli dosya tutulur; eskileri bir sonraki derlemede silinir.")   // the server's `keep`, so a handed-over file name is known to expire
  })

  it("names who started a build running elsewhere and when, so a stuck build can be told from a long one", async () => {
    adminWarehouse.mockResolvedValue({ ...DATA, ...RUNNING })
    setup()
    expect(await screen.findByRole("button", { name: "Derleniyor…" })).toBeDisabled()
    expect(screen.getByText(`Derleniyor… (admin@instilens.app, ${fmtDateTime("2026-09-17T10:00:00Z")} başlattı)`)).toBeInTheDocument()
    expect(screen.getByText("Arka planda çalışır; bitince liste kendini yeniler.")).toBeInTheDocument()
    expect(screen.queryByText(/Son derleme/)).toBeNull()
  })

  it("says so when no build exists yet and keeps the build button", async () => {
    adminWarehouse.mockResolvedValue({ ...IDLE, enabled: false, builds: [], latest: null, keep: 4 })
    setup()
    expect(await screen.findByText("Henüz derlenmiş dosya yok.")).toBeInTheDocument()
    expect(screen.queryByRole("table")).toBeNull()
    expect(screen.getByRole("checkbox")).not.toBeChecked()
    expect(screen.getByRole("button", { name: "Şimdi derle" })).toBeEnabled()
    expect(screen.queryByText(/Son derleme/)).toBeNull()
  })

  it("lists a file whose _meta could not be read with its error in place of date and rows, and prints a failed last build in red", async () => {
    adminWarehouse.mockResolvedValue({
      ...DATA,
      builds: [build("instilens-20260914.duckdb", { built_at: null, rows: null, row_counts: null, git_rev: null, schema_version: null, error: "IOException: not a DuckDB file" }), ...DATA.builds],
      last: { started_by: "admin@instilens.app", started_at: "2026-09-14T09:00:00Z", finished_at: "2026-09-14T09:00:04Z", name: null, rows: null, seconds: null, error: "WarehouseError: verification failed" },
    })
    setup()
    await screen.findByRole("table")
    const damaged = rowOf("instilens-20260914.duckdb").getAllByRole("cell")
    expect(damaged.slice(1, 4).map((c) => c.textContent)).toEqual(["okunamadı", "184.0 MB", "—"])
    expect(damaged[1]).toHaveAttribute("title", "IOException: not a DuckDB file")
    expect(damaged[1]).toHaveClass("text-negative")
    const failed = screen.getByText(`Son derleme başarısız (${fmtDateTime("2026-09-14T09:00:04Z")}, admin@instilens.app): WarehouseError: verification failed`)
    expect(failed).toHaveClass("text-negative")
    expect(screen.getByRole("button", { name: "Şimdi derle" })).toBeEnabled()   // a failed build holds no lock
  })

  it("saves the weekly switch as the warehouse_enabled runtime setting", async () => {
    const user = setup()
    await user.click(await screen.findByRole("checkbox"))
    await waitFor(() => expect(adminSaveSettings).toHaveBeenCalledWith({ warehouse_enabled: false }))
  })

  it("starts a build, shows it running while the API reports so, and settles once it is over", async () => {
    // Fake timers so the 5 s poll can be stepped; they still advance with real time so findBy/waitFor keep working.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      adminWarehouseBuild.mockResolvedValue({ ...RUNNING, started: true })
      adminWarehouse.mockResolvedValueOnce(DATA).mockResolvedValueOnce({ ...DATA, ...RUNNING }).mockResolvedValue({ ...DATA, builds: [build("instilens-20260917.duckdb", { built_at: "2026-09-17T10:00:00Z" }), ...DATA.builds] })
      const user = setup("tr", { advanceTimers: vi.advanceTimersByTime })
      await user.click(await screen.findByRole("button", { name: "Şimdi derle" }))
      expect(adminWarehouseBuild).toHaveBeenCalledTimes(1)
      expect(await screen.findByRole("button", { name: "Derleniyor…" })).toBeDisabled()
      expect(screen.getByText("Arka planda çalışır; bitince liste kendini yeniler.")).toBeInTheDocument()
      expect(screen.queryByText(/Son derleme/)).toBeNull()   // the last outcome yields to the running line
      // The click's own refetch still says running; the next 5 s poll answers running:false with the new file listed.
      await waitFor(() => expect(adminWarehouse).toHaveBeenCalledTimes(2))
      await vi.advanceTimersByTimeAsync(5_000)
      expect(await screen.findByText("instilens-20260917.duckdb")).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Şimdi derle" })).toBeEnabled()
      expect(screen.queryByText("Arka planda çalışır; bitince liste kendini yeniler.")).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it("keeps the running state between the click and the first poll fetched after it, even though the cached status still says idle", async () => {
    adminWarehouseBuild.mockResolvedValue({ ...RUNNING, started: true })
    adminWarehouse.mockResolvedValueOnce(DATA).mockReturnValue(new Promise(() => {}))   // the refetch the click triggers never lands
    const user = setup()
    await user.click(await screen.findByRole("button", { name: "Şimdi derle" }))
    expect(await screen.findByRole("button", { name: "Derleniyor…" })).toBeDisabled()
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.getByRole("button", { name: "Derleniyor…" })).toBeDisabled()   // the pre-click data (running:false) did not end it
  })

  it("relays the server's refusal when a build is already running instead of pretending one started", async () => {
    adminWarehouseBuild.mockResolvedValue({ ...RUNNING, started: false })
    const user = setup()
    await user.click(await screen.findByRole("button", { name: "Şimdi derle" }))
    expect(await screen.findByText("Zaten bir derleme çalışıyor.")).toBeInTheDocument()
  })

  it("downloads a build through the authenticated client and hands the blob to the browser under the file's name", async () => {
    const blob = new Blob(["duck"], { type: "application/octet-stream" })
    let serve: (b: Blob) => void = () => {}
    adminWarehouseDownload.mockReturnValue(new Promise<Blob>((r) => { serve = r }))   // held open: the button's pending label is checked first
    const createObjectURL = vi.fn(() => "blob:instilens/1")
    const revokeObjectURL = vi.fn()
    Object.assign(URL, { createObjectURL, revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {})
    const user = setup()
    await screen.findByRole("table")
    await user.click(rowOf("instilens-20260906.duckdb").getByRole("button", { name: "İndir instilens-20260906.duckdb" }))
    expect((await rowOf("instilens-20260906.duckdb").findByText(/İndiriliyor…/)).textContent).toContain("İndiriliyor… (179.5 MB)")   // the size is the only progress the fetch can show
    serve(blob)
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
    expect(adminWarehouseDownload).toHaveBeenCalledWith(expect.objectContaining({ name: "instilens-20260906.duckdb", url: `${URL_BASE}/instilens-20260906.duckdb` }))
    expect(createObjectURL).toHaveBeenCalledWith(blob)
    const anchor = click.mock.instances[0] as HTMLAnchorElement
    expect(anchor.download).toBe("instilens-20260906.duckdb")
    expect(anchor.href).toBe("blob:instilens/1")
    click.mockRestore()
  })

  it("says when the download fails", async () => {
    adminWarehouseDownload.mockRejectedValue(new Error("403 admin only"))
    const user = setup()
    await screen.findByRole("table")
    await user.click(rowOf("instilens-20260906.duckdb").getByRole("button", { name: /İndir/ }))
    expect(await screen.findByText("İndirme başarısız.")).toBeInTheDocument()
  })

  it("renders English labels", async () => {
    setup("en")
    await screen.findByRole("table")
    expect(screen.getByText("Warehouse (DuckDB)")).toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "Weekly build (Sunday 03:30)" })).toBeChecked()
    expect(screen.getByRole("button", { name: "Build now" })).toBeInTheDocument()
    setLocale("en")
    expect(rowOf("instilens-20260906.duckdb").getAllByRole("cell").map((c) => c.textContent).slice(1, 4)).toEqual([fmtDateTime("2026-09-06T03:30:00Z"), "179.5 MB", "1,234,567"])
    expect(screen.getByText(`Last build: instilens-20260913.duckdb · ${fmtDateTime("2026-09-13T03:31:00Z")} · scheduler · 58.2 s`)).toBeInTheDocument()
  })
})
