import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { authPost, useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { PublicFrame } from "@/components/layout/PublicFrame"

/** /verify?token=… — from the verification e-mail. Works signed-out; when signed in, the header hint refreshes. */
export function VerifyEmailPage() {
  const { t } = useI18n()
  const { user, refreshUser } = useAuth()
  const [params] = useSearchParams()
  const token = params.get("token") ?? ""
  const [state, setState] = useState<"working" | "ok" | "fail" | "none">(token ? "working" : "none")
  useEffect(() => {
    if (!token) return
    let alive = true
    authPost<{ ok: boolean }>("verify", { token })
      .then((r) => { if (!alive) return; setState(r.ok ? "ok" : "fail"); if (r.ok && user) refreshUser().catch(() => {}) })
      .catch(() => alive && setState("fail"))
    return () => { alive = false }
  }, [token])  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <PublicFrame title={t("verify.title")}>
      <div className="space-y-4">
        {state === "working" && <div className="text-sm text-muted-foreground">{t("verify.working")}</div>}
        {state === "ok" && <div className="rounded-md border border-positive/40 bg-positive/10 px-3 py-2 text-sm">{t("verify.ok")}</div>}
        {state === "fail" && <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{t("verify.fail")}</div>}
        {state === "none" && <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{t("verify.noToken")}</div>}
        <Link to="/" className="block text-center text-xs text-primary hover:underline">{user ? t("verify.goApp") : t("login.back")}</Link>
      </div>
    </PublicFrame>
  )
}
