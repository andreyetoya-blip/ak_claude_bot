"""Абстракция над LLM-провайдером: Claude (Anthropic), GigaChat (Сбер), YandexGPT (Яндекс).

Смысл: вся остальная логика бота (Telegram, инструменты, база знаний, память) не
знает, на какой модели она работает. Провайдер выбирается переменной окружения
``LLM_PROVIDER`` (``anthropic`` | ``gigachat`` | ``yandex``; по умолчанию ``anthropic``).

Единый интерфейс — ``provider.run(messages, system, tool_schemas, dispatch) -> str``:
    messages     — история в нейтральном виде: [{"role": "user"|"assistant", "content": str}]
    system       — системный промпт (строка)
    tool_schemas — инструменты в «нейтральном» (Anthropic-style) формате:
                   [{"name": ..., "description": ..., "input_schema": {...}}]
    dispatch     — исполнитель инструмента: dispatch(name: str, args: dict) -> Any

Инструменты у всех *_tools.py уже описаны в Anthropic-формате (ключ ``input_schema``),
поэтому его и берём за нейтральный. Провайдеры на базе OpenAI-совместимого API
(GigaChat, YandexGPT) конвертируют схемы внутри себя.

ВАЖНО: провайдеры gigachat/yandex реализованы по документированному OpenAI-совместимому
контракту, но НЕ протестированы против живого API (нужны ключи, а у GigaChat ещё и
российские TLS-сертификаты Минцифры). Точные base_url / схему авторизации сверяйте с
актуальной докой провайдера. Провайдер anthropic — основной проверенный путь.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)

Dispatch = Callable[[str, dict], Any]

MAX_TOOL_ITERATIONS = 8
MAX_TOKENS = 4096

TOO_MANY_STEPS = (
    "Не получилось завершить запрос — слишком много шагов с инструментами. "
    "Попробуй сформулировать иначе."
)


class ProviderError(RuntimeError):
    """Транзиентный сбой на стороне провайдера — стоит предложить пользователю повторить."""


class LLMProvider(Protocol):
    name: str
    supports_web: bool

    def run(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tool_schemas: list[dict[str, Any]],
        dispatch: Dispatch,
    ) -> str:
        ...


def to_openai_tools(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic-формат инструментов -> OpenAI-формат (function calling)."""
    tools = []
    for schema in schemas:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema.get("description", ""),
                    "parameters": schema.get(
                        "input_schema", {"type": "object", "properties": {}}
                    ),
                },
            }
        )
    return tools


class AnthropicProvider:
    """Claude через Anthropic Messages API. Основной, проверенный путь.

    Умеет серверные инструменты web_search / web_fetch (крутятся на стороне Anthropic)
    и корректно обрабатывает stop_reason == "pause_turn".
    """

    name = "anthropic"
    supports_web = True

    WEB_TOOLS: list[dict[str, Any]] = [
        {"type": "web_search_20260209", "name": "web_search"},
        {"type": "web_fetch_20260209", "name": "web_fetch"},
    ]

    def __init__(self) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_KEY"], max_retries=3)
        self.model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

    def run(self, messages, system, tool_schemas, dispatch):
        anthropic = self._anthropic
        tools = list(tool_schemas) + self.WEB_TOOLS
        convo: list[dict[str, Any]] = [dict(m) for m in messages]

        try:
            for _ in range(MAX_TOOL_ITERATIONS):
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=system,
                    messages=convo,
                    tools=tools,
                )

                if response.stop_reason == "pause_turn":
                    convo.append({"role": "assistant", "content": response.content})
                    continue

                if response.stop_reason != "tool_use":
                    return "".join(
                        block.text
                        for block in response.content
                        if getattr(block, "type", None) == "text"
                    ).strip()

                convo.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    try:
                        result = dispatch(block.name, dict(block.input))
                        content = json.dumps(result, ensure_ascii=False, default=str)
                        tool_results.append(
                            {"type": "tool_result", "tool_use_id": block.id, "content": content}
                        )
                    except Exception as exc:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": f"Ошибка инструмента {block.name}: {exc}",
                                "is_error": True,
                            }
                        )

                if not tool_results:
                    continue

                convo.append({"role": "user", "content": tool_results})
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise ProviderError(str(exc)) from exc

        return TOO_MANY_STEPS


class OpenAICompatProvider:
    """Базовый провайдер для OpenAI-совместимых API (GigaChat, YandexGPT).

    Серверного веб-поиска у этих API нет — supports_web = False. Инструменты
    конвертируются в OpenAI-формат, цикл идёт по message.tool_calls.
    """

    supports_web = False

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        *,
        verify: Any = True,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        from openai import APIConnectionError, APIError, OpenAI

        self.name = name
        self.model = model
        self._errors = (APIError, APIConnectionError)

        # verify != True означает кастомный CA-бандл (путь) или отключённую проверку —
        # тогда OpenAI-клиенту нужен свой httpx-клиент.
        http_client = None
        if verify is not True:
            import httpx

            http_client = httpx.Client(verify=verify)

        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            default_headers=default_headers or {},
            http_client=http_client,
            max_retries=3,
        )

    def run(self, messages, system, tool_schemas, dispatch):
        tools = to_openai_tools(tool_schemas)
        convo: list[dict[str, Any]] = [{"role": "system", "content": system}]
        convo += [dict(m) for m in messages]

        try:
            for _ in range(MAX_TOOL_ITERATIONS):
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": convo,
                    "max_tokens": MAX_TOKENS,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                response = self.client.chat.completions.create(**kwargs)
                message = response.choices[0].message
                tool_calls = message.tool_calls or []

                if not tool_calls:
                    return (message.content or "").strip()

                convo.append(
                    {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )

                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    try:
                        result = dispatch(tc.function.name, args)
                        content = json.dumps(result, ensure_ascii=False, default=str)
                    except Exception as exc:
                        content = f"Ошибка инструмента {tc.function.name}: {exc}"
                    convo.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": content}
                    )
        except self._errors as exc:
            raise ProviderError(str(exc)) from exc

        return TOO_MANY_STEPS


class GigaChatProvider(OpenAICompatProvider):
    """GigaChat (Сбер) через OpenAI-совместимый эндпоинт.

    Авторизация двухступенчатая: авторизационный ключ (Basic) обменивается на
    access-токен (живёт ~30 мин) на OAuth-эндпоинте, дальше токен идёт как Bearer.
    Токен обновляется лениво перед каждым запросом.

    TLS: GigaChat требует корневой сертификат Минцифры. Задайте путь к CA-бандлу в
    GIGACHAT_CA_BUNDLE, либо (не для прода) GIGACHAT_VERIFY_SSL=false.
    """

    def __init__(self) -> None:
        self._auth_key = os.environ["GIGACHAT_AUTH_KEY"]
        self._scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        self._oauth_url = os.getenv(
            "GIGACHAT_OAUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        )
        self._token: str | None = None
        self._token_exp: float = 0.0

        verify: Any = True
        ca_bundle = os.getenv("GIGACHAT_CA_BUNDLE")
        if ca_bundle:
            verify = ca_bundle
        elif os.getenv("GIGACHAT_VERIFY_SSL", "true").lower() == "false":
            verify = False
        self._verify = verify

        super().__init__(
            name="gigachat",
            base_url=os.getenv("GIGACHAT_BASE_URL", "https://gigachat.devices.sberbank.ru/api/v1"),
            api_key="pending",  # заменяется реальным токеном в _ensure_token()
            model=os.getenv("GIGACHAT_MODEL", "GigaChat-Max"),
            verify=verify,
        )

    def _ensure_token(self) -> None:
        # запас 60 с, чтобы токен не протух прямо во время запроса
        if self._token and time.time() < self._token_exp - 60:
            return

        import httpx

        response = httpx.post(
            self._oauth_url,
            headers={
                "Authorization": f"Basic {self._auth_key}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"scope": self._scope},
            verify=self._verify,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        self._token = data["access_token"]
        # expires_at приходит в миллисекундах epoch; если нет — считаем 25 минут
        expires_at = data.get("expires_at")
        self._token_exp = (expires_at / 1000) if expires_at else (time.time() + 1500)
        self.client.api_key = self._token

    def run(self, messages, system, tool_schemas, dispatch):
        try:
            self._ensure_token()
        except Exception as exc:
            raise ProviderError(f"GigaChat OAuth: {exc}") from exc
        return super().run(messages, system, tool_schemas, dispatch)


class YandexProvider(OpenAICompatProvider):
    """YandexGPT (Yandex Cloud Foundation Models) через OpenAI-совместимый эндпоинт.

    Модель адресуется URI вида gpt://<folder_id>/yandexgpt/latest.
    Ключ API передаётся как Bearer (OpenAI-совместимый режим).
    """

    def __init__(self) -> None:
        folder = os.environ["YANDEX_FOLDER_ID"]
        model_name = os.getenv("YANDEX_MODEL", "yandexgpt/latest")
        super().__init__(
            name="yandex",
            base_url=os.getenv("YANDEX_BASE_URL", "https://llm.api.cloud.yandex.net/v1"),
            api_key=os.environ["YANDEX_API_KEY"],
            model=f"gpt://{folder}/{model_name}",
        )


# --- реестр провайдеров и переключение в рантайме ---

PROVIDER_FACTORIES: dict[str, Callable[[], LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "gigachat": GigaChatProvider,
    "yandex": YandexProvider,
}

PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Claude (Anthropic)",
    "gigachat": "GigaChat (Сбер)",
    "yandex": "YandexGPT (Яндекс)",
}

# Построенные провайдеры кэшируются по имени, чтобы не переинициализировать
# клиента (и не пере-авторизовываться) при каждом переключении туда-обратно.
_instances: dict[str, LLMProvider] = {}
_current_name: str | None = None


def available_providers() -> list[str]:
    return list(PROVIDER_FACTORIES)


def provider_label(name: str) -> str:
    return PROVIDER_LABELS.get(name, name)


def _default_provider_name() -> str:
    name = os.getenv("LLM_PROVIDER", "anthropic").lower()
    return name if name in PROVIDER_FACTORIES else "anthropic"


def current_provider_name() -> str:
    global _current_name
    if _current_name is None:
        _current_name = _default_provider_name()
    return _current_name


def get_provider(name: str | None = None) -> LLMProvider:
    key = (name or current_provider_name()).lower()
    if key not in PROVIDER_FACTORIES:
        raise ValueError(
            f"Неизвестный провайдер: {key!r}. Доступно: {', '.join(PROVIDER_FACTORIES)}."
        )
    if key not in _instances:
        try:
            _instances[key] = PROVIDER_FACTORIES[key]()
        except KeyError as exc:
            raise ProviderError(
                f"Провайдер {key} не настроен — не задана переменная окружения {exc}."
            ) from exc
        except Exception as exc:  # ImportError SDK, ошибка конфигурации и т.п.
            raise ProviderError(
                f"Не удалось инициализировать провайдера {key}: {exc}"
            ) from exc
    return _instances[key]


def set_provider(name: str) -> LLMProvider:
    """Переключить активного провайдера в рантайме.

    Бросает ValueError (неизвестное имя) или ProviderError (нет ключей / не поднялся).
    Делает провайдера текущим только при успешной инициализации.
    """
    key = name.lower()
    provider = get_provider(key)
    global _current_name
    _current_name = key
    return provider
