import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { fmtDateTime } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"
import { PipelineButton } from "@/components/domain/PipelineButton"
import { NewsRulesAdmin } from "@/components/domain/NewsRulesAdmin"

export function AdminPage() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: api.adminUsers, enabled: user?.role === "ADMIN" })
  const review = useQuery({ queryKey: ["admin", "review"], queryFn: api.adminReview, enabled: user?.role === "ADMIN" })
  const patch = useMutation({ mutationFn: ({ id, body }: { id: number; body: Parameters<typeof api.adminPatchUser>[1] }) => api.adminPatchUser(id, body), onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }) })
  const verify = useMutation({ mutationFn: api.adminVerify, onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "review"] }) })
  const outcomes = useMutation({ mutationFn: api.adminComputeOutcomes })

  if (user?.role !== "ADMIN") return <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">Bu alan yalnızca ADMIN rolü için.</div>
  const r = review.data

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight">Admin</h1><p className="text-sm text-muted-foreground">Kullanıcılar ve entity review (otomatik oluşturulmuş, doğrulanmamış kayıtlar).</p></div>
        <div className="flex flex-wrap items-center gap-2"><PipelineButton /><Button variant="outline" size="sm" onClick={() => outcomes.mutate()} disabled={outcomes.isPending}>Sinyal outcome'larını hesapla{outcomes.data ? ` (${outcomes.data.updated})` : ""}</Button></div>
      </div>
      <NewsRulesAdmin />
      <Section title="Kullanıcılar" hint={`${users.data?.length ?? 0}`}>
        <table className="w-full text-sm">
          <thead className="text-[11px] uppercase tracking-wider text-muted-foreground"><tr className="border-b border-border/60"><th className="px-4 py-2 text-left font-medium">E-posta</th><th className="px-2 py-2 text-left font-medium">Ad</th><th className="px-2 py-2 text-left font-medium">Plan</th><th className="px-2 py-2 text-left font-medium">Rol</th><th className="px-2 py-2 text-left font-medium">Son giriş</th><th className="px-4 py-2 text-right font-medium">Aktif</th></tr></thead>
          <tbody>
            {users.data?.map((u) => (
              <tr key={u.id} className="border-b border-border/40 last:border-0">
                <td className="px-4 py-2">{u.email}</td><td className="px-2 py-2">{u.name}</td>
                <td className="px-2 py-2"><select value={u.plan} onChange={(e) => patch.mutate({ id: u.id, body: { plan: e.target.value as "FREE" } })} className="rounded border border-input bg-background px-1 py-0.5 text-xs">{["FREE", "PRO", "PRO_PLUS"].map((p) => <option key={p}>{p}</option>)}</select></td>
                <td className="px-2 py-2"><select value={u.role} onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value as "USER" } })} className="rounded border border-input bg-background px-1 py-0.5 text-xs">{["USER", "ADMIN"].map((p) => <option key={p}>{p}</option>)}</select></td>
                <td className="num px-2 py-2 text-xs text-muted-foreground">{u.last_login_at ? fmtDateTime(u.last_login_at) : "—"}</td>
                <td className="px-4 py-2 text-right"><input type="checkbox" checked={u.is_active} onChange={(e) => patch.mutate({ id: u.id, body: { is_active: e.target.checked } })} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      <div className="grid gap-5 lg:grid-cols-2">
        <ReviewList title="Doğrulanmamış hisseler" rows={r?.instruments.map((i) => ({ id: i.id, label: `${i.market} · ${i.symbol}`, name: i.name })) ?? []} onVerify={(id, name) => verify.mutate({ kind: "instrument", id, name })} />
        <ReviewList title="Doğrulanmamış fonlar" rows={r?.funds.map((f) => ({ id: f.id, label: `${f.code} · ${f.institution}`, name: f.name })) ?? []} onVerify={(id, name) => verify.mutate({ kind: "fund", id, name })} />
        <ReviewList title="Doğrulanmamış kurumlar" rows={r?.institutions.map((i) => ({ id: i.id, label: `${i.market} · ${i.code}`, name: i.name })) ?? []} onVerify={(id, name) => verify.mutate({ kind: "institution", id, name })} />
        <Section title="Parse hataları" hint={`${r?.failed_disclosures.length ?? 0}`}>
          <ul className="divide-y divide-border/60 text-xs">{r?.failed_disclosures.map((d) => <li key={d.id} className="px-4 py-2"><span className="font-mono">{d.source} #{d.source_id}</span> <span className="text-muted-foreground">{d.kind}</span><div className="text-negative">{d.error}</div></li>)}{r?.failed_disclosures.length === 0 && <li className="px-4 py-6 text-muted-foreground">Yok.</li>}</ul>
        </Section>
      </div>
    </div>
  )
}

function ReviewList({ title, rows, onVerify }: { title: string; rows: { id: number; label: string; name: string }[]; onVerify: (id: number, name: string) => void }) {
  return (
    <Section title={title} hint={`${rows.length}`}>
      <ul className="divide-y divide-border/60 text-sm">
        {rows.slice(0, 50).map((r) => (
          <li key={r.id} className="flex items-center gap-2 px-4 py-2">
            <span className="w-40 shrink-0 truncate font-mono text-xs">{r.label}</span>
            <input defaultValue={r.name} id={`n-${title}-${r.id}`} className="h-7 flex-1 rounded border border-input bg-background px-2 text-xs" />
            <Button size="sm" variant="outline" onClick={() => onVerify(r.id, (document.getElementById(`n-${title}-${r.id}`) as HTMLInputElement).value)}>Doğrula</Button>
          </li>
        ))}
        {rows.length === 0 && <li className="px-4 py-6 text-muted-foreground">Yok.</li>}
        {rows.length > 50 && <li className="px-4 py-2 text-xs text-muted-foreground">+{rows.length - 50} daha</li>}
      </ul>
    </Section>
  )
}
