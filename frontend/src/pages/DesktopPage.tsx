import { useQuery } from "@tanstack/react-query"
import { Download, Monitor } from "lucide-react"
import { api } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { fmtDate } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { Mark } from "@/components/layout/Brand"

/** Download page for the desktop app: latest published installers per platform. */
export function DesktopPage() {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["desktop-latest"], queryFn: api.desktopLatest })
  const r = q.data
  const installers = r?.files.filter((f) => f.downloadable) ?? []
  const ua = navigator.userAgent
  const mine = /Mac/.test(ua) ? "darwin" : /Win/.test(ua) ? "windows" : /Linux/.test(ua) ? "linux" : ""
  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="flex items-center gap-3"><Mark className="size-10" /><div><h1 className="text-2xl font-semibold tracking-tight">{t("desk.title")}</h1><p className="text-sm text-muted-foreground">{t("desk.sub")}</p></div></div>
      {!r && !q.isLoading && <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">{t("desk.none")}</div>}
      {r && (
        <Section title={`InstiLens v${r.version}`} hint={r.published_at ? fmtDate(r.published_at) : undefined}>
          <div className="grid gap-3 p-4 sm:grid-cols-2">
            {installers.map((f) => (
              <a key={f.id} href={f.url} className={`flex items-center gap-3 rounded-md border p-3 text-sm hover:bg-accent/50 ${f.platform.startsWith(mine) ? "border-primary/50 bg-primary/5" : "border-border"}`}>
                <Monitor className="size-5 text-primary" />
                <div className="min-w-0"><div className="font-medium">{f.label}</div><div className="truncate text-xs text-muted-foreground">{f.filename} · {(f.size / 1e6).toFixed(1)} MB</div></div>
                <Download className="ml-auto size-4 text-muted-foreground" />
              </a>
            ))}
          </div>
          {r.notes && <div className="whitespace-pre-wrap border-t border-border/60 px-4 py-3 text-sm text-muted-foreground">{r.notes}</div>}
          <div className="border-t border-border/60 px-4 py-3 text-xs text-muted-foreground">{t("desk.unsigned")}</div>
        </Section>
      )}
    </div>
  )
}
