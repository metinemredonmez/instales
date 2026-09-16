// Waitlist form → POST /api/v1/public/waitlist (same origin; nginx proxies /api to the backend).
document.querySelectorAll("form.wait").forEach((form) => {
  const msg = form.querySelector(".msg")
  const lang = document.documentElement.lang || "tr"
  const T = lang === "en"
    ? { busy: "Sending…", ok: "You're on the list — we'll write when your seat opens.", dup: "This e-mail is already on the list.", err: "Couldn't send. Try again in a minute." }
    : { busy: "Gönderiliyor…", ok: "Listeye alındın — yerin açılınca yazacağız.", dup: "Bu e-posta zaten listede.", err: "Gönderilemedi. Bir dakika sonra tekrar dene." }
  form.addEventListener("submit", async (e) => {
    e.preventDefault()
    const email = form.email.value.trim()
    if (!email) return
    msg.className = "msg"; msg.textContent = T.busy
    try {
      const r = await fetch("/api/v1/public/waitlist", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, lang, source: new URLSearchParams(location.search).get("utm_source") || document.referrer.slice(0, 64) || null }) })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || r.status)
      msg.className = "msg ok"; msg.textContent = d.already ? T.dup : T.ok
      form.email.value = ""
    } catch { msg.className = "msg err"; msg.textContent = T.err }
  })
})
