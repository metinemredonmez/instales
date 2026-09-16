import { Link } from "react-router-dom"
import { GradientWordmark } from "@/components/layout/Brand"
import { useI18n } from "@/lib/i18n"
import { cn } from "@/lib/utils"

/** Centered card for pages that render outside the auth gate (reset, verify, desktop download). */
export function PublicFrame({ title, sub, wide, children }: { title?: string; sub?: string; wide?: boolean; children: React.ReactNode }) {
  const { lang, setLang, t } = useI18n()
  return (
    <div className="relative flex min-h-dvh items-center justify-center bg-background p-6 text-foreground">
      <div className="absolute right-5 top-5 flex overflow-hidden rounded-md border border-border text-[11px] font-semibold" role="radiogroup" aria-label={t("lang.label")}>
        {(["tr", "en"] as const).map((l) => (
          <button key={l} type="button" role="radio" aria-checked={lang === l} onClick={() => setLang(l)} className={cn("px-2.5 py-1 uppercase", lang === l ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground")}>{l}</button>
        ))}
      </div>
      <div className={cn("w-full space-y-5 rounded-xl border border-border bg-card p-7 shadow-xl shadow-black/10", wide ? "max-w-3xl" : "max-w-sm")}>
        <Link to="/" className="flex flex-col items-center gap-2 pb-1"><GradientWordmark className="w-48" /></Link>
        {title && (
          <div>
            <div className="text-base font-semibold leading-tight">{title}</div>
            {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
          </div>
        )}
        {children}
      </div>
    </div>
  )
}
