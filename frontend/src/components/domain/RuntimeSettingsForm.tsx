import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { RotateCcw } from "lucide-react"
import { api, type RuntimeSetting } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import type { Key } from "@/i18n/tr"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const GROUPS: RuntimeSetting["group"][] = ["access", "ai", "data"]

/** Non-secret settings the admin can change live; a value equal to the default clears the override. */
export function RuntimeSettingsForm() {
  const { t } = useI18n()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ["admin", "settings"], queryFn: api.adminSettings })
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => { if (q.data) setDraft(Object.fromEntries(q.data.map((s) => [s.key, toText(s.value)]))) }, [q.data])
  const save = useMutation({
    mutationFn: () => {
      const values: Record<string, unknown> = {}
      for (const s of q.data ?? []) if (draft[s.key] !== toText(s.value)) values[s.key] = fromText(s, draft[s.key])
      return api.adminSaveSettings(values)
    },
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["admin"] }); setMsg({ ok: true, text: r.changed.length ? `${t("settings.saved")}: ${r.changed.join(", ")}` : t("settings.nochange") }) },
    onError: (e) => setMsg({ ok: false, text: (e as Error).message }),
  })
  if (!q.data) return null
  const dirty = q.data.some((s) => draft[s.key] !== undefined && draft[s.key] !== toText(s.value))
  return (
    <Section title={t("settings.title")} hint={t("settings.hint")} right={
      <div className="flex items-center gap-2">
        {msg && <span className={cn("text-xs", msg.ok ? "text-positive" : "text-negative")}>{msg.text}</span>}
        <Button size="sm" onClick={() => { setMsg(null); save.mutate() }} disabled={!dirty || save.isPending}>{t("common.save")}</Button>
      </div>
    }>
      <div className="grid gap-5 p-4 lg:grid-cols-3">
        {GROUPS.map((g) => (
          <div key={g} className="space-y-3">
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{t(`settings.group.${g}` as Key)}</div>
            {q.data!.filter((s) => s.group === g).map((s) => (
              <label key={s.key} className="block text-xs">
                <div className="mb-1 flex items-center gap-2">
                  <span className="font-medium text-foreground">{t(`settings.k.${s.key}` as Key)}</span>
                  {s.overridden && <span className="rounded-sm border border-warning/40 bg-warning/10 px-1 py-px text-[10px] text-warning" title={`${s.updated_by ?? ""} ${s.updated_at ?? ""}`}>{t("settings.overridden")}</span>}
                  {s.overridden && <button type="button" onClick={() => setDraft({ ...draft, [s.key]: toText(s.default) })} className="text-muted-foreground hover:text-foreground" title={t("settings.reset")}><RotateCcw className="size-3" /></button>}
                </div>
                <Field s={s} value={draft[s.key] ?? ""} onChange={(v) => setDraft({ ...draft, [s.key]: v })} />
                <div className="mt-0.5 text-[10px] text-muted-foreground">{t(`settings.d.${s.key}` as Key)}{s.min !== null && s.max !== null ? ` · ${s.min}–${s.max}` : ""}</div>
              </label>
            ))}
          </div>
        ))}
      </div>
    </Section>
  )
}

function Field({ s, value, onChange }: { s: RuntimeSetting; value: string; onChange: (v: string) => void }) {
  const cls = "h-8 w-full rounded-md border border-input bg-background px-2 text-sm outline-none focus:ring-2 focus:ring-ring/40"
  if (s.type === "bool") return <select value={value} onChange={(e) => onChange(e.target.value)} className={cls}><option value="true">on</option><option value="false">off</option></select>
  if (s.type.startsWith("choice:")) return <select value={value} onChange={(e) => onChange(e.target.value)} className={cls}>{s.type.slice(7).split("|").map((o) => <option key={o}>{o}</option>)}</select>
  if (s.type === "int") return <input type="number" min={s.min ?? undefined} max={s.max ?? undefined} value={value} onChange={(e) => onChange(e.target.value)} className={cn(cls, "num")} />
  return <input value={value} onChange={(e) => onChange(e.target.value)} className={cls} placeholder={s.type === "list" ? "A, B, C" : ""} />
}

function toText(v: unknown): string { return Array.isArray(v) ? v.join(", ") : typeof v === "boolean" ? String(v) : String(v ?? "") }
function fromText(s: RuntimeSetting, v: string): unknown {
  if (s.type === "bool") return v === "true"
  if (s.type === "int") return Number(v)
  if (s.type === "list") return v.split(",").map((x) => x.trim()).filter(Boolean)
  return v
}
