import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { fmtDateTime } from "@/lib/format"
import { Section } from "@/components/layout/Section"

export function AdminUsersPage() {
  const { t } = useI18n()
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: api.adminUsers })
  const patch = useMutation({ mutationFn: ({ id, body }: { id: number; body: Parameters<typeof api.adminPatchUser>[1] }) => api.adminPatchUser(id, body), onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }) })
  const th = "px-2 py-2 text-left font-medium"
  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("admin.users.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("admin.users.sub")}</p>
      </div>
      <Section title={t("admin.users.title")} hint={`${users.data?.length ?? 0}`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
              <tr className="border-b border-border/60"><th className="px-4 py-2 text-left font-medium">{t("field.email")}</th><th className={th}>{t("field.name")}</th><th className={th}>{t("field.plan")}</th><th className={th}>{t("field.role")}</th><th className={th}>{t("admin.users.lastLogin")}</th><th className="px-4 py-2 text-right font-medium">{t("field.active")}</th></tr>
            </thead>
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
        </div>
      </Section>
    </>
  )
}
