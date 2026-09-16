import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Star } from "lucide-react"
import { api, type Market } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useI18n } from "@/lib/i18n"

export function WatchButton({ symbol, fundCode, market }: { symbol?: string; fundCode?: string; market: Market }) {
  const { t } = useI18n()
  const qc = useQueryClient()
  const wl = useQuery({ queryKey: ["watchlist"], queryFn: api.watchlist })
  const item = wl.data?.find((i) => (symbol ? i.kind === "stock" && i.ref === symbol : i.kind === "fund" && i.ref === fundCode))
  const toggle = useMutation({
    mutationFn: async () => (item ? api.removeWatch(item.id) : api.addWatch({ symbol, fund_code: fundCode, market })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  })
  return (
    <Button variant="outline" size="sm" onClick={() => toggle.mutate()} disabled={toggle.isPending} className={cn(item && "border-warning/50 text-warning")}>
      <Star className={cn("size-4", item && "fill-current")} /> {item ? t("watch.watching") : t("watch.watch")}
    </Button>
  )
}
