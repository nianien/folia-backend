"""按功能选 provider + 模型的统一模型客户端。

create_model_client(settings, function) 读:
- models.<function> = {"provider": ..., "model": ...}(embedding 除外, 它是本地 Ollama 字符串);
- providers.<provider> = {"endpoint": ..., "api_key": ...}(凭证/端点在配置页维护)。

provider 为空 或 model 为空 → enabled=False, 消费方(facts/synthesizer/categorize)退回规则。
provider 分四类协议:
- ollama(本地): POST {endpoint}/api/chat, 无 key;
- openai / deepseek / qwen / xinapi: OpenAI 兼容 /chat/completions, Bearer 鉴权;
- claude: Anthropic /v1/messages, x-api-key 鉴权;
- gemini: {endpoint}/models/{model}:generateContent?key=...。
远程 provider 缺 key / 调用失败 → 抛 ModelError, 由消费方 catch 后退回规则。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

# 与 OpenAI /chat/completions 完全兼容的一类, 共用同一实现
OPENAI_COMPATIBLE = {"openai", "deepseek", "qwen", "xinapi"}


class ModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    provider: str  # "" = 规则(不用模型)
    model: str
    endpoint: str
    api_key: str
    timeout_seconds: int
    temperature: float
    max_output_tokens: int
    num_ctx: int  # 仅本地 Ollama 生效


class ModelClient:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return bool(self.config.provider and self.config.model)

    @property
    def model_name(self) -> str:
        if not self.enabled:
            return "heuristic-v1"
        return f"{self.config.provider}:{self.config.model}"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if not self.enabled:
            raise ModelError("model disabled (heuristic)")
        provider = self.config.provider
        if provider == "ollama":
            return self._ollama(system_prompt, user_prompt)
        if provider in OPENAI_COMPATIBLE:
            return self._openai_compatible(system_prompt, user_prompt)
        if provider == "claude":
            return self._claude(system_prompt, user_prompt)
        if provider == "gemini":
            return self._gemini(system_prompt, user_prompt)
        raise ModelError(f"unsupported provider: {provider}")

    def _require_key(self) -> str:
        if not self.config.api_key:
            raise ModelError(f"{self.config.provider} 未配置 API key")
        return self.config.api_key

    def _ollama(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "think": False,  # 关掉 thinking(混合推理模型如 qwen3); 非 thinking 模型忽略此项
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_output_tokens,
                "num_ctx": self.config.num_ctx,  # 显式限定上下文, 否则 Ollama 默认 32K 会撑爆小内存机
            },
        }
        data = self._post(f"{self.config.endpoint}/api/chat", payload)
        content = data.get("message", {}).get("content")
        if isinstance(content, str) and content.strip():
            return content
        raise ModelError("Ollama chat 未返回 message.content")

    def _openai_compatible(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._require_key()}",
            "Content-Type": "application/json",
        }
        data = self._post(self.config.endpoint, payload, headers)
        for choice in data.get("choices", []):
            content = choice.get("message", {}).get("content")
            if isinstance(content, str) and content.strip():
                return content
        raise ModelError(f"{self.config.provider} 未返回 choices[0].message.content")

    def _claude(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "temperature": self.config.temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        headers = {
            "x-api-key": self._require_key(),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        data = self._post(self.config.endpoint, payload, headers)
        for block in data.get("content", []):
            if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip():
                return block["text"]
        raise ModelError("Claude 未返回 content[].text")

    def _gemini(self, system_prompt: str, user_prompt: str) -> str:
        key = urllib.parse.quote(self._require_key())
        endpoint = f"{self.config.endpoint}/models/{self.config.model}:generateContent?key={key}"
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self.config.temperature,
                "maxOutputTokens": self.config.max_output_tokens,
            },
        }
        data = self._post(endpoint, payload, {"Content-Type": "application/json"})
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text
        raise ModelError("Gemini 未返回 candidates[].content.parts[].text")

    def _post(
        self, endpoint: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers=headers or {"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise ModelError(f"{self.config.provider} HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ModelError(f"{self.config.provider} 请求失败: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelError(f"{self.config.provider} 返回非 JSON") from exc


def create_model_client(settings: dict[str, Any], function: str = "synthesis") -> ModelClient:
    """按功能取 provider+模型; provider 或 model 为空 → 规则。"""
    fn = settings.get("models", {}).get(function)
    if isinstance(fn, dict):
        provider = str(fn.get("provider", "") or "").strip()
        model = str(fn.get("model", "") or "").strip()
    else:  # 兼容旧的纯模型名(默认走本地 Ollama)
        model = str(fn or "").strip()
        provider = "ollama" if model else ""

    providers = settings.get("providers", {})
    pc = providers.get(provider, {})
    endpoint = str(pc.get("endpoint", "") if isinstance(pc, dict) else "").rstrip("/")
    api_key = str(pc.get("api_key", "") if isinstance(pc, dict) else "")

    model_cfg = settings.get("model", {})
    return ModelClient(
        ModelConfig(
            provider=provider if model else "",
            model=model,
            endpoint=endpoint,
            api_key=api_key,
            timeout_seconds=int(model_cfg.get("timeout_seconds", 120)),
            temperature=float(model_cfg.get("temperature", 0.2)),
            max_output_tokens=int(model_cfg.get("max_output_tokens", 3000)),
            num_ctx=int(model_cfg.get("num_ctx", 8192)),
        )
    )
