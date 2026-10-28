"""
llm.py - provider adapters. The loop talks to one small interface, so the LLM vendor can be swapped.

    client = make_client()                       # provider from WHYBOT_PROVIDER (default gemini)
    conv = client.start(system_prompt, tools)    # tools: [{"name", "description", "input_schema"}] (JSON Schema)
    turn = conv.send(user_text="...")            # -> Turn(text, tool_calls=[ToolCall(id, name, args)])
    turn = conv.send(tool_results=[(call, result_dict), ...])

A conversation keeps the provider's own message history (Gemini thought signatures must be sent back unchanged).
Add a provider: write a class with start() returning an object with send(), and register it in PROVIDERS.
Env: WHYBOT_PROVIDER, WHYBOT_MODEL, GEMINI_API_KEY (read from .env at the repo root).
"""
import copy
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash"}   # 2026-10-28: 2.5-flash 404 for new keys, 3.8-flash 503 under load


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str = ""
    tool_calls: list = field(default_factory=list)


class LLMError(Exception):
    pass


def load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def _plain(x):
    """SDK arg objects -> plain JSON types."""
    return json.loads(json.dumps(x, default=lambda o: dict(o) if hasattr(o, "items") else list(o)))


# ---------------------------------------------------------------- Gemini
def gemini_schema(schema):
    """Contract JSON Schema -> the subset Gemini accepts (oneOf -> anyOf, no additionalProperties)."""
    s = copy.deepcopy(schema)

    def fix(node):
        if isinstance(node, dict):
            node.pop("additionalProperties", None)
            if "oneOf" in node:
                node["anyOf"] = node.pop("oneOf")
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)
    fix(s)
    return s


class GeminiClient:
    """Tries `model`, then each fallback model (WHYBOT_FALLBACK_MODELS, comma-separated) when one stays
    unavailable (429/5xx/404/timeouts). The model that answered becomes the first choice for later turns."""
    RETRIES = 2                 # per model (backoff 2 s), then move on: a busy model costs ~2 s, not ~6 s
    TIMEOUT_MS = 120_000        # per request, so one stuck call cannot hang the demo
    FALLBACKS = "gemini-3.1-flash-lite,gemini-3.8-flash"
    THINKING = "low"            # WHYBOT_THINKING: minimal | low | medium | high. Tool choice needs little reasoning

    def __init__(self, model=None, api_key=None):
        from google import genai
        from google.genai import types
        load_env()
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY is not set (put it in .env at the repo root)")
        self.model = model or os.environ.get("WHYBOT_MODEL") or DEFAULT_MODELS["gemini"]
        fallbacks = os.environ.get("WHYBOT_FALLBACK_MODELS", self.FALLBACKS)
        self.models = [self.model] + [m.strip() for m in fallbacks.split(",") if m.strip() and m.strip() != self.model]
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=self.TIMEOUT_MS))

    def start(self, system, tools):
        return _GeminiConversation(self, system, tools)


class _GeminiConversation:
    def __init__(self, owner, system, tools):
        from google.genai import types
        self.t, self.owner, self.history = types, owner, []
        decls = [types.FunctionDeclaration(name=x["name"], description=x["description"],
                                           parameters_json_schema=gemini_schema(x["input_schema"])) for x in tools]
        level = os.environ.get("WHYBOT_THINKING", GeminiClient.THINKING).upper()
        self.config = types.GenerateContentConfig(
            system_instruction=system, temperature=0, tools=[types.Tool(function_declarations=decls)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=getattr(types.ThinkingLevel, level)))

    def send(self, user_text=None, tool_results=None):
        t = self.t
        parts = [t.Part(function_response=t.FunctionResponse(id=c.id, name=c.name, response=r))
                 for c, r in (tool_results or [])]
        if user_text:
            parts.append(t.Part.from_text(text=user_text))
        if not parts:
            raise LLMError("nothing to send")
        self.history.append(t.Content(role="user", parts=parts))
        try:
            resp = self._generate()
        except LLMError:
            self.history.pop()          # leave the conversation as it was, so a caller can retry
            raise
        cand = (resp.candidates or [None])[0]
        content = getattr(cand, "content", None)
        if content is None or not content.parts:
            reason = getattr(cand, "finish_reason", None) if cand else "no candidates"
            raise LLMError(f"Gemini returned no content (finish_reason={reason})")
        self.history.append(content)
        calls = [ToolCall(id=p.function_call.id or f"call_{i}", name=p.function_call.name,
                          args=_plain(p.function_call.args or {}))
                 for i, p in enumerate(content.parts) if p.function_call]
        text = "".join(p.text for p in content.parts if p.text and not p.thought)
        return Turn(text=text, tool_calls=calls)

    def _generate(self):
        from google.genai import errors
        owner, failures = self.owner, []
        for model in list(owner.models):
            for attempt in range(GeminiClient.RETRIES):
                try:
                    resp = owner.client.models.generate_content(model=model, contents=self.history,
                                                                config=self.config)
                    if model != owner.model:            # sticky: keep using the model that works
                        owner.models.remove(model)
                        owner.models.insert(0, model)
                        owner.model = model
                    return resp
                except errors.APIError as e:
                    code = getattr(e, "code", None)
                    if code not in (404, 429, 500, 503, 504):
                        raise LLMError(f"Gemini API error ({model}): {e}") from e
                    err = f"{model}: {code}"
                    if code == 404:
                        break                          # model not offered to this key: next model
                except Exception as e:  # noqa: BLE001  (timeouts / connection drops from the HTTP layer)
                    err = f"{model}: {type(e).__name__}"
                if attempt < GeminiClient.RETRIES - 1:
                    time.sleep(2 ** attempt * 2)
            failures.append(err)
        raise LLMError("Gemini unavailable (tried " + ", ".join(failures) + "); try again shortly or set "
                       "WHYBOT_MODEL / WHYBOT_FALLBACK_MODELS")


PROVIDERS = {"gemini": GeminiClient}


def make_client(provider=None, model=None):
    load_env()
    name = (provider or os.environ.get("WHYBOT_PROVIDER") or "gemini").lower()
    if name not in PROVIDERS:
        raise LLMError(f"unknown provider '{name}'; available: {', '.join(PROVIDERS)}")
    return PROVIDERS[name](model=model)
