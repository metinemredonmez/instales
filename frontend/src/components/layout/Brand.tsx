import { cn } from "@/lib/utils"

/**
 * Brand assets generated from brand/source by scripts/brand_assets.py.
 * `*-light.png` are white glyphs (dark theme), `*-dark.png` black glyphs (light theme).
 * Both are in the DOM and the theme picks one via the `dark:` variant, so toggling never flashes.
 * Responsive/size classes go on the wrapper; the images just fill it.
 */
function Swap({ name, alt, className }: { name: "wordmark" | "lockup" | "tagline"; alt: string; className?: string }) {
  return (
    <span className={cn("shrink-0", className)}>
      <img src={`/brand/${name}-dark.png`} alt={alt} draggable={false} className="block h-full w-auto max-w-full select-none dark:hidden" />
      <img src={`/brand/${name}-light.png`} alt="" aria-hidden draggable={false} className="hidden h-full w-auto max-w-full select-none dark:block" />
    </span>
  )
}

/** INSTILENS wordmark — header, compact places. Set the height on the wrapper (default 14px). */
export function Wordmark({ className }: { className?: string }) {
  return <Swap name="wordmark" alt="InstiLens" className={cn("block h-3.5", className)} />
}

/** Wordmark + "SEE WHERE SMART MONEY MOVES." + "ISTANBUL - ESTD 2027" — login, splash, about. */
export function Lockup({ className }: { className?: string }) {
  return <Swap name="lockup" alt="InstiLens — See where smart money moves." className={cn("block w-full max-w-md", className)} />
}

/** App icon: stencil “I” on the blue gradient. */
export function Mark({ className }: { className?: string }) {
  return <img src="/icon-192.png" alt="" aria-hidden draggable={false} className={cn("size-7 shrink-0 rounded-md", className)} />
}
