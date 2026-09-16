import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Check, Download, Trash2, Undo2 } from "lucide-react"
import { api, type DesktopRelease } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { fmtDateTime } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const fmtSize = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} KB`)

/** Version management: one row per release, one column per platform; publish/withdraw/delete. */
export function AdminReleasesPage() {
  const { t } = useI18n()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ["admin", "releases"], queryFn: api.adminReleases, refetchInterval: 30_000 })
  const patch = useMutation({ mutationFn: ({ id, body }: { id: number; body: Parameters<typeof api.adminPatchRelease>[1] }) => api.adminPatchRelease(id, body), onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "releases"] }) })
  const del = useMutation({ mutationFn: api.adminDeleteRelease, onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "releases"] }) })
  const [notes, setNotes] = useState<Record<number, string>>({})
  const d = q.data
  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("rel.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("rel.sub")}</p>
      </div>
      {d && (!d.upload_key_set || !d.updater_pubkey_set) && (
        <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs">
          {!d.upload_key_set && <div>⚠ {t("rel.noUploadKey")}</div>}
          {!d.updater_pubkey_set && <div>⚠ {t("rel.noPubkey")}</div>}
        </div>
      )}
      {patch.isError && <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-xs">{(patch.error as Error).message}</div>}
      {d?.releases.length === 0 && <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">{t("rel.empty")}</div>}
      {d?.releases.map((r) => <ReleaseCard key={r.id} r={r} platforms={d.platforms} notes={notes[r.id] ?? r.notes} setNotes={(v) => setNotes({ ...notes, [r.id]: v })} patch={(body) => patch.mutate({ id: r.id, body })} del={() => { if (confirm(`${r.version} — ${t("common.delete")}?`)) del.mutate(r.id) }} />)}
      <Section title={t("rel.howto")}>
        <div className="space-y-2 p-4 text-xs text-muted-foreground">
          <div>{t("rel.howto1")}</div>
          <pre className="overflow-x-auto rounded bg-muted/60 p-2 font-mono text-[11px] text-foreground">bash scripts/desktop-release.sh            # Mac: macOS (+ Windows via cargo-xwin) → yükler</pre>
          <pre className="overflow-x-auto rounded bg-muted/60 p-2 font-mono text-[11px] text-foreground">bash infra/pm2/desktop-build.sh            # Sunucu: Linux (+ Windows) → aynı sürüme ekler</pre>
          <div>{t("rel.howto2")}</div>
        </div>
      </Section>
    </>
  )
}

function ReleaseCard({ r, platforms, notes, setNotes, patch, del }: { r: DesktopRelease; platforms: { key: string; label: string }[]; notes: string; setNotes: (v: string) => void; patch: (b: { status?: "DRAFT" | "PUBLISHED" | "WITHDRAWN"; notes?: string }) => void; del: () => void }) {
  const { t } = useI18n()
  const tone = r.status === "PUBLISHED" ? "border-positive/40 text-positive" : r.status === "WITHDRAWN" ? "border-negative/40 text-negative" : "border-warning/40 text-warning"
  const label = r.status === "PUBLISHED" ? t("rel.published") : r.status === "WITHDRAWN" ? t("rel.withdrawn") : t("rel.draft")
  return (
    <Section title={`v${r.version}`} hint={`${fmtDateTime(r.created_at)}${r.created_by ? ` · ${r.created_by}` : ""}`} right={
      <div className="flex items-center gap-2">
        <span className={cn("rounded-sm border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider", tone)}>{label}</span>
        {r.status !== "PUBLISHED" && <Button size="sm" onClick={() => patch({ status: "PUBLISHED", notes })} disabled={!r.files.some((f) => f.kind === "INSTALLER")}><Check className="size-4" /> {t("rel.publish")}</Button>}
        {r.status === "PUBLISHED" && <Button size="sm" variant="outline" onClick={() => patch({ status: "WITHDRAWN" })}><Undo2 className="size-4" /> {t("rel.withdraw")}</Button>}
        {r.status === "DRAFT" && <Button size="sm" variant="ghost" onClick={del} aria-label={t("common.delete")}><Trash2 className="size-4" /></Button>}
      </div>
    }>
      <div className="grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-4">
        {platforms.map((p) => {
          const files = r.files.filter((f) => f.platform === p.key)
          const installer = files.find((f) => f.kind === "INSTALLER")
          const update = files.find((f) => f.kind === "UPDATE")
          return (
            <div key={p.key} className={cn("rounded-md border p-3 text-xs", files.length ? "border-border bg-card" : "border-dashed border-border/60 text-muted-foreground")}>
              <div className="font-medium text-foreground">{p.label}</div>
              {!files.length && <div className="mt-1">{t("rel.missing")}</div>}
              {installer && <div className="mt-1.5 flex items-center gap-1.5"><Download className="size-3 text-primary" /><a href={installer.url} className="truncate hover:underline" title={installer.filename}>{installer.filename}</a><span className="num ml-auto shrink-0 text-muted-foreground">{fmtSize(installer.size)}</span></div>}
              {update && <div className="mt-1 flex items-center gap-1.5 text-muted-foreground"><span className={cn("size-1.5 rounded-full", update.signed ? "bg-positive" : "bg-negative")} /><span className="truncate" title={update.filename}>{t("rel.updateBundle")} {update.signed ? t("rel.signed") : t("rel.unsigned")}</span></div>}
              {installer && <div className="mt-1 num text-[10px] text-muted-foreground">{t("rel.downloads")}: {installer.downloads}</div>}
            </div>
          )
        })}
      </div>
      <div className="border-t border-border/60 p-4">
        <div className="mb-1 text-xs text-muted-foreground">{t("rel.notes")}</div>
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} onBlur={() => notes !== r.notes && patch({ notes })} rows={2} className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm outline-none focus:ring-2 focus:ring-ring/40" placeholder="YENİ — … / DÜZELTME — …" />
      </div>
    </Section>
  )
}
