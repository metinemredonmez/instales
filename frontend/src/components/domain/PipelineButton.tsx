import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"
import { api } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useI18n } from "@/lib/i18n"

/** Admin-only: run the full data pull (KAP + SEC + prices + compute) from the UI and watch it. */
export function PipelineButton({ size = "sm", className }: { size?: "sm" | "default"; className?: string }) {
  const { user } = useAuth()
  const { t } = useI18n()
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ["pipeline-status"], queryFn: api.adminPipelineStatus, enabled: user?.role === "ADMIN", refetchInterval: (q) => (q.state.data?.running ? 5000 : false) })
  const run = useMutation({ mutationFn: api.adminPipelineRun, onSuccess: () => qc.invalidateQueries({ queryKey: ["pipeline-status"] }) })
  if (user?.role !== "ADMIN") return null
  const st = status.data
  const done = st && !st.running && st.finished_at
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <Button size={size} onClick={() => run.mutate()} disabled={run.isPending || st?.running}>
        <RefreshCw className={cn("size-4", st?.running && "animate-spin")} /> {st?.running ? t("pipeline.running") : t("pipeline.run")}
      </Button>
      {st?.running && <span className="text-xs text-muted-foreground">{t("pipeline.hint")}</span>}
      {done && st.result && <span className="text-xs text-muted-foreground">{t("pipeline.last")}: {Object.entries(st.result).map(([k, v]) => `${k} ${v}`).join(" · ")}</span>}
      {done && st.error && <span className="text-xs text-negative">{t("common.error")}: {st.error.split("\n").slice(-1)[0]}</span>}
    </div>
  )
}
