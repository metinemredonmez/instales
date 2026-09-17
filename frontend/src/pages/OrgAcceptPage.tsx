import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { Users } from "lucide-react"
import { ApiError, api, type Org } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"

/**
 * /org/accept?token=… — where the invitation mail lands (services/org.invite). Signed out, App shows the login page on
 * this same address, so the token survives the sign-in and the accept runs right after. The token is read once and
 * taken off the address bar (history, not the server, is what it would otherwise sit in), posted to /org/accept, and
 * the answer is the organisation joined or the API's reason (a stale link, the wrong or an unverified address, an
 * account already in a team).
 */
export function OrgAcceptPage() {
  const { t } = useI18n()
  const { user, refreshUser } = useAuth()
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const token = useRef<string | null>(params.get("token"))
  const [state, setState] = useState<{ kind: "working" | "done" | "failed" | "notoken"; org?: Org; text?: string }>(token.current ? { kind: "working" } : { kind: "notoken" })
  useEffect(() => {
    if (params.has("token")) setParams({}, { replace: true })
    const tok = token.current
    if (!tok) return
    token.current = null // once: a re-render or a strict-mode double effect must not post the token twice
    api.orgAccept(tok)
      .then((org) => { setState({ kind: "done", org }); qc.invalidateQueries({ queryKey: ["org"] }); qc.invalidateQueries({ queryKey: ["billing"] }); void refreshUser?.().catch(() => {}) })
      .catch((e: unknown) => setState({ kind: "failed", text: e instanceof ApiError && typeof e.detail === "string" ? e.detail : (e as Error).message }))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="mx-auto max-w-md space-y-4">
      <div><h1 className="text-2xl font-semibold tracking-tight">{t("org.accept.title")}</h1><p className="text-sm text-muted-foreground">{t("org.accept.hint")}{user ? ` (${user.email})` : ""}</p></div>
      <div role="status" className="rise rounded-lg border border-border bg-card px-6 py-8 text-center text-sm">
        <div className="mx-auto grid size-10 place-items-center rounded-full bg-muted text-muted-foreground"><Users className="size-5" /></div>
        {state.kind === "working" && <div className="mt-3 text-muted-foreground">{t("org.accept.working")}</div>}
        {state.kind === "done" && state.org && <div className="mt-3 font-medium">{t("org.accept.done", { name: state.org.name })}</div>}
        {state.kind === "notoken" && <div className="mt-3 text-muted-foreground">{t("org.accept.noToken")}</div>}
        {state.kind === "failed" && (
          <div className="mt-3">
            <div className="font-medium">{t("org.accept.failed")}</div>
            {state.text && <div className="mt-1 text-xs text-muted-foreground">{state.text}</div>}
          </div>
        )}
        {state.kind !== "working" && <Button asChild size="sm" className="mt-4"><Link to="/plan">{t("plan.title")}</Link></Button>}
      </div>
    </div>
  )
}
