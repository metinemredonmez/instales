"""Headless-Chrome screenshot of the running dev app (real data) for the landing page.
Usage: uv run --with websockets python scripts/shoot.py <session.json> <url> <out.png> [width] [height]"""
import asyncio, base64, json, subprocess, sys, time, urllib.request
import websockets

sess, url, out = sys.argv[1:4]
w, h = int(sys.argv[4]) if len(sys.argv) > 4 else 1400, int(sys.argv[5]) if len(sys.argv) > 5 else 880
chrome = subprocess.Popen(["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--headless=new", "--remote-debugging-port=9333",
                           f"--window-size={w},{h}", "--hide-scrollbars", "--user-data-dir=/tmp/il-shoot", "--no-first-run", "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            targets = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json")); break
        except Exception: time.sleep(0.2)
    ws_url = next(t for t in targets if t["type"] == "page")["webSocketDebuggerUrl"]

    async def main():
        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            n = 0
            async def call(method, **params):
                nonlocal n; n += 1
                await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == n: return m.get("result", {})
            origin = url.split("/", 3)[:3]; origin = "/".join(origin)
            await call("Page.enable")
            await call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=2, mobile=False)
            await call("Page.navigate", url=origin + "/login"); await asyncio.sleep(1.5)
            js = f"localStorage.setItem('instilens.session', {json.dumps(open(sess).read())}); localStorage.setItem('instilens.theme','dark'); localStorage.setItem('instilens.market','TR'); localStorage.setItem('instilens.lang','tr'); 1"
            await call("Runtime.evaluate", expression=js)
            await call("Page.navigate", url=url); await asyncio.sleep(6)
            r = await call("Page.captureScreenshot", format="png")
            open(out, "wb").write(base64.b64decode(r["data"]))
    asyncio.run(main())
finally:
    chrome.terminate()
print("saved", out)
