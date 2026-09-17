import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Download, RefreshCw } from "lucide-react"
import { api, type WarehouseBuild } from "@/lib/api"
import { fmtDateTime, fmtNum, fmtQty } from "@/lib/format"
import { useI18n } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const fmtSize = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(2)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} KB`)
/** Hands a fetched file to the browser's own download flow: an object URL on a throwaway <a download>, revoked once the click has been dispatched. */
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/**
 * Admin → Veri & pipeline: the DuckDB warehouse (docs/08-warehouse.md). The switch is the `warehouse_enabled` runtime
 * setting (the weekly Sunday build; saved through the same PUT /admin/settings the settings form uses, so that page
 * shows the override too), the table lists every build the server holds with a download that fetches the file
 * under the session's bearer token and hands it to the browser, and "Şimdi derle" starts a build in the server's
 * background thread: while one runs the card polls every 5 s and the button spins, until the API reports the build
 * over (`running` false). The outcome of the last build is printed under the switch — a failed one with its error,
 * since the file it would have written is simply absent from the list. A second click while one runs is refused by
 * the server (`started` false) and the card says so rather than pretending it started another; a build started
 * elsewhere (another admin, the weekly job, or a lock that outlived its worker — taken over after two hours) is
 * shown with who started it and when, so a stuck one can be told from a long one. A file the server could not read
 * (`error` on the build) is listed with the error in place of its date and rows. The footer says how many dated files
 * the server keeps (`keep`): older ones go at the next build, so a file name handed to a colleague has a shelf life.
 * The download button prints the file's size while it fetches — the whole file is held in memory before the browser
 * gets it, so minutes may pass on a large build with no other feedback.
 */
export function WarehouseCard() {
  const { t } = useI18n()
  const qc = useQueryClient()
  // `building` (the click's time) bridges the click and the first poll fetched after it: only a response newer than the
  // click may say the build is over — the cached one from before it still reads `running: false`.
  const [building, setBuilding] = useState<number | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const q = useQuery({ queryKey: ["admin", "warehouse"], queryFn: api.adminWarehouse, refetchInterval: (query) => (building !== null || query.state.data?.running ? 5_000 : 60_000) })
  const d = q.data
  useEffect(() => { if (building !== null && d && !d.running && q.dataUpdatedAt >= building) setBuilding(null) }, [building, d, q.dataUpdatedAt])
  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin", "warehouse"] })
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.adminSaveSettings({ warehouse_enabled: enabled }),
    onSuccess: () => { invalidate(); qc.invalidateQueries({ queryKey: ["admin", "settings"] }) },
    onError: (e) => setMsg((e as Error).message),
  })
  const build = useMutation({
    mutationFn: api.adminWarehouseBuild,
    onSuccess: (r) => { if (r.started) setBuilding(Date.now()); else setMsg(t("wh.locked")); invalidate() },
    onError: (e) => setMsg((e as Error).message),
  })
  const download = useMutation({
    mutationFn: async (b: WarehouseBuild) => ({ name: b.name, blob: await api.adminWarehouseDownload(b) }),
    onSuccess: ({ name, blob }) => saveBlob(blob, name),
    onError: () => setMsg(t("wh.downloadError")),
  })
  const running = building !== null || d?.running === true

  return (
    <Section title={t("wh.title")} hint={t("wh.hint")} right={
      <Button size="sm" onClick={() => { setMsg(null); build.mutate() }} disabled={!d || running || build.isPending}>
        <RefreshCw className={cn("size-3.5", running && "animate-spin")} /> {running ? t("wh.building") : t("wh.build")}
      </Button>
    }>
      {!d ? (
        <p className={cn("p-4 text-sm", q.isError ? "text-negative" : "text-muted-foreground")}>{q.isError ? t("wh.loadError") : "…"}</p>
      ) : (
        <div className="divide-y divide-border/60">
          <div className="space-y-1 p-4 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={d.enabled} disabled={toggle.isPending} onChange={(e) => { setMsg(null); toggle.mutate(e.target.checked) }} /> {t("wh.enabled")}
            </label>
            <div className="pl-6 text-[11px] text-muted-foreground">{t("wh.enabled.hint")}</div>
            {running && (
              <div className="pl-6 text-[11px] text-muted-foreground">
                {d.running && d.started_by && d.started_at && <div>{t("wh.running", { by: d.started_by, at: fmtDateTime(d.started_at) })}</div>}
                {t("wh.building.hint")}
              </div>
            )}
            {!running && d.last && (
              <div className={cn("pl-6 text-[11px]", d.last.error ? "text-negative" : "text-muted-foreground")}>
                {d.last.error
                  ? t("wh.last.failed", { at: fmtDateTime(d.last.finished_at), by: d.last.started_by ?? "?", err: d.last.error })
                  : t("wh.last.ok", { at: fmtDateTime(d.last.finished_at), by: d.last.started_by ?? "?", name: d.last.name ?? "?", s: fmtNum(d.last.seconds, 1) })}
              </div>
            )}
            {msg && <div className="pl-6 text-xs text-negative">{msg}</div>}
          </div>
          {d.builds.length === 0 ? (
            <div className="px-4 py-6 text-sm text-muted-foreground">{t("wh.empty")}</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
                  <tr className="border-b border-border/60">
                    <th className="px-4 py-2 text-left font-medium">{t("wh.col.name")}</th>
                    <th className="px-2 py-2 text-left font-medium">{t("wh.col.date")}</th>
                    <th className="px-2 py-2 text-right font-medium">{t("wh.col.size")}</th>
                    <th className="px-2 py-2 text-right font-medium">{t("wh.col.rows")}</th>
                    <th className="px-4 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {d.builds.map((b) => {
                    const busy = download.isPending && download.variables?.name === b.name
                    return (
                      <tr key={b.name} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
                        <td className="whitespace-nowrap px-4 py-2 font-mono text-xs">{b.name}</td>
                        <td className={cn("num whitespace-nowrap px-2 py-2", b.error ? "text-negative" : "text-muted-foreground")} title={b.error ?? undefined}>{b.error ? t("wh.unreadable") : b.built_at ? fmtDateTime(b.built_at) : "—"}</td>
                        <td className="num whitespace-nowrap px-2 py-2 text-right">{fmtSize(b.size)}</td>
                        <td className="num whitespace-nowrap px-2 py-2 text-right">{b.rows === null ? "—" : fmtQty(b.rows)}</td>
                        <td className="px-4 py-1.5 text-right">
                          <Button size="xs" variant="outline" onClick={() => { setMsg(null); download.mutate(b) }} disabled={download.isPending} aria-label={`${t("wh.download")} ${b.name}`}>
                            <Download className="size-3" /> {busy ? t("wh.downloading", { size: fmtSize(b.size) }) : t("wh.download")}
                          </Button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="px-4 py-2 text-[11px] text-muted-foreground">{t("wh.howto")} {t("wh.keep", { keep: d.keep })}</p>
        </div>
      )}
    </Section>
  )
}
