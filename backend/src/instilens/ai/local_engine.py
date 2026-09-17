"""Self-hosted research engine over an OpenAI-compatible chat completions API (Ollama ≥ 0.4, vLLM, LM Studio…).

The contract is the Claude engine's: the same tools (`build_tools`, exposed as OpenAI function schemas by
`ai/openai_tools`), the same SYSTEM_PROMPT, the same ResearchAnswer with the tool-call audit trail. Only the
transport differs — `POST {base_url}/v1/chat/completions` with `tools`, the model's `tool_calls` run here and go
back as `tool` messages, at most MAX_ROUNDS rounds; then one closing call without tools so the answer is text.

Ground rule, enforced rather than trusted: every figure of three or more digits in the final answer must appear,
at the start of a number, in some tool result of this conversation (or in the question itself). A figure that does
not is never dropped silently — the answer comes back with `unverified_numbers` filled and a sentence in the user's
language naming those figures in front of the text. What the check does not cover (see `unverified_numbers`):
figures under 100 and one- or two-digit percentages are never checked; a bare four-digit year (1900–2099) is exempt;
"843" is verified by a result holding 843000000 (a prefix at a number boundary), not by one holding 1608432.

Prompt size: a tool result longer than TOOL_OUTPUT_MAX characters goes to the model clipped (the number check still
sees all of it) and once the messages exceed `prompt_chars` the next call is the closing one — a local server
truncates the prompt from the head, silently (Ollama) or with a 400 (vLLM), and the head is the system prompt.

httpx, 120 s per call and an overall `deadline` for the whole ask; only connect-phase failures and 429/5xx are
retried (3 tries, backoff) — a read timeout means the server is still generating and a retry would queue behind it.
No streaming. The client is injectable so tests fake the HTTP layer (`httpx.MockTransport`); nothing here touches
the network in tests.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from instilens.ai.assess import AiUnavailable
from instilens.ai.engine import ResearchAnswer, ToolCall
from instilens.ai.openai_tools import function_schema
from instilens.ai.prompts import SYSTEM_PROMPT
from instilens.ai.tools import build_tools

log = logging.getLogger("instilens.ai.local")

MAX_ROUNDS = 8
TIMEOUT_S = 120.0
DEADLINE_S = 300.0  # the whole ask, every round included (settings.ai_local_deadline_s)
PROMPT_CHARS = 40_000  # messages budget before the closing call (settings.ai_local_prompt_chars)
TOOL_OUTPUT_MAX = 12_000  # characters of one tool result the model sees
TRIES = 3
BACKOFF_S = 0.5  # doubled per retry
RETRIED = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError)  # nothing was generated yet
PREVIEW = 300
# A figure as the model writes it: digits with optional thousands groups ("46.526.835", "1,438"); a decimal part is not
# a figure ("317.23" is 317), nor is a run that continues a number.
FIGURE = re.compile(r"(?<![\d.,])\d+(?:[.,\u00a0]\d{3})*")
SEPARATORS = re.compile(r"[.,\u00a0]")
YEAR = re.compile(r"(19|20)\d{2}")
# Thousands separators between digit groups: "46.526.835" / "46,526,835" / "46 526 835" read as 46526835 too.
THOUSANDS = re.compile("(?<=\\d)[.,\\u00a0 ](?=\\d{3}(?!\\d))")
# Language of the question, for the unverified-figures sentence: a Turkish letter, one word only Turkish uses, or two
# short function words that also exist in English decide it; everything else is answered in English.
TURKISH_CHARS = set("çğıöşüÇĞİÖŞÜ")
TURKISH_STRONG = {"hangi", "fon", "fonlar", "fonun", "hisse", "hisseleri", "nedir", "neler", "durum", "hakkinda", "neden", "kim", "alim", "satim",
                  "bildirim", "rapor", "kurum", "kurumsal", "yatirim", "sirket", "sirketi"}
TURKISH_WEAK = {"ve", "bir", "ne", "bu", "son", "mi", "mu", "var", "yok", "kadar", "ile", "gibi", "en", "daha"}


class LocalResearchEngine:
    name = "local"

    def __init__(self, session: Session, base_url: str, model: str, api_key: str | None = None, *, client: httpx.Client | None = None,
                 max_rounds: int = MAX_ROUNDS, timeout: float = TIMEOUT_S, deadline: float = DEADLINE_S, prompt_chars: int = PROMPT_CHARS,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.session = session
        self.url = completions_url(base_url)
        self.model = model
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=timeout)
        self.timeout = timeout
        self.max_rounds = max_rounds
        self.deadline = deadline
        self.prompt_chars = prompt_chars
        self.sleep = sleep
        self.clock = clock

    def ask(self, question: str, market: str = "TR") -> ResearchAnswer:
        if not self.model:
            raise AiUnavailable("local model not configured (INSTILENS_AI_LOCAL_MODEL)")
        tools = {fn.__name__: fn for fn in build_tools(self.session, market)}
        schemas = [function_schema(fn) for fn in tools.values()]
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]
        calls: list[ToolCall] = []
        results: list[str] = []
        usage = {"input_tokens": 0, "output_tokens": 0}
        served_model = self.model
        message: dict = {}
        ends_at = self.clock() + self.deadline
        for round_no in range(self.max_rounds + 1):
            # The tool budget is spent, or the prompt is as large as the local context allows: the model answers in text.
            closing = round_no == self.max_rounds or _size(messages) >= self.prompt_chars
            data = self._complete(messages, None if closing else schemas, ends_at)
            served_model = data.get("model") or served_model
            spent = data.get("usage") or {}
            usage["input_tokens"] += int(spent.get("prompt_tokens") or 0)
            usage["output_tokens"] += int(spent.get("completion_tokens") or 0)
            message = _message(data)
            tool_calls = message.get("tool_calls") or []
            if closing or not tool_calls:
                break
            for i, tc in enumerate(tool_calls):  # a server that omits ids (older llama.cpp, some gateways) gets unique ones
                tc["id"] = tc.get("id") or f"call_{round_no}_{i}"
            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
            for tc in tool_calls:
                name, args, output = self._run(tools, tc)
                results.append(output)
                calls.append(ToolCall(name=name, input=args, output_preview=_preview(output)))
                messages.append({"role": "tool", "tool_call_id": tc["id"], "name": name, "content": _clip(output, TOOL_OUTPUT_MAX)})
        text = _text(message.get("content"))
        if not text:
            text = "Model yanıt üretemedi." if is_turkish(question) else "The model produced no answer."
        unverified = unverified_numbers(text, results, question)
        if unverified:
            log.warning("local answer carries unverified figures %s", unverified)
            text = f"{unverified_prefix(unverified, question)}\n\n{text}"
        return ResearchAnswer(question=question, answer=text, tool_calls=calls, model=served_model, usage=usage, unverified_numbers=unverified)

    # ------------------------------------------------------------------ transport

    def _complete(self, messages: list[dict], schemas: list[dict] | None, ends_at: float) -> dict:
        """One chat completion within the ask's deadline. What the server said on failure goes to the log; the
        AiUnavailable text (the 503 body any signed-in user sees) names only the kind of failure."""
        body: dict = {"model": self.model, "messages": messages, "temperature": 0, "stream": False}
        if schemas:
            body["tools"] = schemas
            body["tool_choice"] = "auto"
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        last = ""
        for attempt in range(TRIES):
            if attempt:
                self.sleep(BACKOFF_S * 2 ** (attempt - 1))
            remaining = ends_at - self.clock()
            if remaining <= 0:
                raise AiUnavailable(f"local model deadline of {self.deadline:.0f} s exceeded")
            try:
                resp = self.client.post(self.url, json=body, headers=headers, timeout=min(self.timeout, remaining))
            except RETRIED as exc:  # connection refused, connect timeout, connection dropped before any response
                last = type(exc).__name__
                log.warning("local model %s (try %s/%s): %s", last, attempt + 1, TRIES, exc)
                continue
            except httpx.TransportError as exc:  # a read timeout: the server is still generating — do not queue another
                log.warning("local model %s: %s", type(exc).__name__, exc)
                raise AiUnavailable(f"local model {type(exc).__name__}") from exc
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
                log.warning("local model %s (try %s/%s): %s", last, attempt + 1, TRIES, resp.text[:200])
                continue
            if resp.status_code >= 400:
                log.warning("local model HTTP %s: %s", resp.status_code, resp.text[:200])
                raise AiUnavailable(f"local model HTTP {resp.status_code}")
            try:
                return resp.json()
            except ValueError as exc:
                log.warning("local model returned no JSON: %s", resp.text[:200])
                raise AiUnavailable("local model returned no JSON") from exc
        raise AiUnavailable(f"local model unreachable after {TRIES} tries ({last})")

    # ------------------------------------------------------------------ tools

    @staticmethod
    def _run(tools: dict[str, Callable[..., str]], tc: dict) -> tuple[str, dict, str]:
        """Run one tool call; a bad name, bad JSON or a tool that raises comes back as an error payload the model can
        read — the loop never breaks on the model's mistake."""
        fn_call = tc.get("function") or {}
        name = str(fn_call.get("name") or "")
        raw = fn_call.get("arguments")
        if isinstance(raw, dict):  # Ollama may send the object itself
            args = raw
        else:
            try:
                args = json.loads(raw) if raw else {}
            except ValueError:
                return name, {"_raw": str(raw)[:PREVIEW]}, json.dumps({"error": "arguments are not valid JSON"})
            if not isinstance(args, dict):
                return name, {"_raw": str(raw)[:PREVIEW]}, json.dumps({"error": "arguments must be a JSON object"})
        fn = tools.get(name)
        if fn is None:
            return name, args, json.dumps({"error": f"unknown tool {name}"})
        try:
            return name, args, fn(**args)
        except TypeError as exc:  # wrong or missing parameters
            return name, args, json.dumps({"error": f"bad arguments for {name}: {exc}"})
        except Exception as exc:  # noqa: BLE001 — a tool failure is data for the model, not a crash of the answer
            log.warning("tool %s failed: %s: %s", name, type(exc).__name__, exc)  # a database error carries the statement: log only
            return name, args, json.dumps({"error": f"{name} failed"})


# ---------------------------------------------------------------------- helpers


def completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


def _message(data: dict) -> dict:
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise AiUnavailable("local model answered without choices")
    return choices[0].get("message") or {}


def _text(content) -> str:
    """Assistant content as text — a string, or the list of parts some servers return."""
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return (content or "").strip() if isinstance(content, str) else ""


def _preview(content: str, limit: int = PREVIEW) -> str:
    return content[:limit] + ("…" if len(content) > limit else "")


def _clip(content: str, limit: int) -> str:
    """A tool result as the model gets it: at most `limit` characters, the cut named so the model knows the list
    goes on. The audit trail and the number check keep the whole output."""
    return content if len(content) <= limit else f"{content[:limit]}… [truncated, {len(content)} chars]"


def _size(messages: list[dict]) -> int:
    return sum(len(m.get("content") or "") for m in messages)


def unverified_numbers(answer: str, results: list[str], question: str = "") -> list[str]:
    """Figures of `answer` (three or more digits, thousands groups joined: "843.000.000" is one figure, 843000000)
    that no tool result — nor the question — carries at the start of a number, in order of first appearance and
    spelled as the answer spells them. The haystack is read as written and with thousands separators removed, so
    "46.526.835" is verified by 46526835 and the other way round; a match must begin at a number boundary, so 843
    is verified by 843000000 (a prefix: "843 million") but not by 1608432. Not checked: anything under 100 and one-
    or two-digit percentages (too short), and a bare four-digit year — "2025", not "2025%" or "2025,5" — which the
    model may name as a comparison period without a tool saying it."""
    haystack = "\n".join(results) + "\n" + question
    stripped = THOUSANDS.sub("", haystack)
    out: dict[str, str] = {}
    for m in FIGURE.finditer(answer):
        token, run = m.group(0), SEPARATORS.sub("", m.group(0))
        if len(run) < 3 or run in out:
            continue
        after = answer[m.end():m.end() + 2]
        if YEAR.fullmatch(run) and after[:1] != "%" and not (after[:1] in ".," and after[1:2].isdigit()):
            continue
        at_start = re.compile(r"(?<![\d.,])" + re.escape(run))
        if not at_start.search(stripped) and not at_start.search(haystack):
            out[run] = token
    return list(out.values())


def is_turkish(text: str) -> bool:
    if any(ch in TURKISH_CHARS for ch in text):
        return True
    words = re.findall(r"[a-z]+", text.lower())
    return any(w in TURKISH_STRONG for w in words) or sum(w in TURKISH_WEAK for w in words) >= 2


def unverified_prefix(numbers: list[str], question: str) -> str:
    listed = ", ".join(numbers)
    if is_turkish(question):
        return f"Doğrulanamayan rakamlar: {listed} — bu değerler hiçbir araç sonucunda bulunamadı."
    return f"Unverified figures: {listed} — these values were not found in any tool result."
