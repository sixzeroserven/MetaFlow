import base64
import json
import mimetypes
import os
import random
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def openai_timeout_seconds() -> float:
    raw = (os.getenv("OPENAI_TIMEOUT_MS") or os.getenv("AI_ATTRIBUTION_TIMEOUT_MS") or "60000").strip()
    try:
        timeout_ms = float(raw)
    except ValueError:
        timeout_ms = 60000.0
    return max(5.0, min(timeout_ms / 1000.0, 300.0))


def extract_response_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    parts = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
    return "\n".join(parts).strip()


def extract_response_stream_text(response: requests.Response) -> str:
    parts = []
    completed_payload: Optional[Dict[str, Any]] = None
    for raw_line in response.iter_lines(decode_unicode=True):
        line = (raw_line or "").strip()
        if not line or not line.startswith("data:"):
            continue
        data_text = line[len("data:") :].strip()
        if not data_text or data_text == "[DONE]":
            continue
        try:
            event = json.loads(data_text)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                parts.append(delta)
        elif event_type == "response.completed":
            payload = event.get("response")
            if isinstance(payload, dict):
                completed_payload = payload

    text = "".join(parts).strip()
    if text:
        return text
    return extract_response_text(completed_payload)


def extract_response_stream_payload(response: requests.Response) -> Tuple[str, Optional[Dict[str, Any]]]:
    parts = []
    completed_payload: Optional[Dict[str, Any]] = None
    for raw_line in response.iter_lines(decode_unicode=True):
        line = (raw_line or "").strip()
        if not line or not line.startswith("data:"):
            continue
        data_text = line[len("data:") :].strip()
        if not data_text or data_text == "[DONE]":
            continue
        try:
            event = json.loads(data_text)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                parts.append(delta)
        elif event_type == "response.completed":
            payload = event.get("response")
            if isinstance(payload, dict):
                completed_payload = payload
    return "".join(parts).strip(), completed_payload


def find_image_reference(data: Any) -> Tuple[Optional[str], Optional[str]]:
    if isinstance(data, dict):
        for key in ("b64_json", "image_base64", "base64", "data"):
            value = data.get(key)
            if isinstance(value, str) and len(value) > 200:
                return value, None
        for key in ("url", "image_url"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return None, value
        for value in data.values():
            b64_value, url_value = find_image_reference(value)
            if b64_value or url_value:
                return b64_value, url_value
    elif isinstance(data, list):
        for item in data:
            b64_value, url_value = find_image_reference(item)
            if b64_value or url_value:
                return b64_value, url_value
    return None, None


def extract_image_urls_from_context(text: str, limit: int = 4) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"https?://[^\s,'\")]+", text or ""):
        url = match.group(0).rstrip(".,;")
        lower = url.lower()
        if any(ext in lower for ext in (".png", ".jpg", ".jpeg", ".webp")) and url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    return sorted(urls, key=lambda item: ("_400x" in item.lower(), "_200x" in item.lower()))


def extract_chat_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return content.strip() if isinstance(content, str) else ""


def parse_json_object(text: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(text or "{}")
    except ValueError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except ValueError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_comment(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    comment = " ".join(value.split()).strip().strip('"')
    if not comment:
        return None
    return comment[:280]


def soften_comment_start(comment: str) -> str:
    if comment.lower().startswith("the "):
        return "This " + comment[4:]
    return comment


def soften_mechanical_phrases(comment: str) -> str:
    replacements = {
        " and boom,": ",",
        " and boom": "",
        "boom,": "",
        "boom": "",
        "July mood handled": "feels pretty nice",
        "mood handled": "feels pretty nice",
        "instantly cheerful": "pretty nice",
        "must-have": "nice",
        "game changer": "nice",
        "perfect for": "nice for",
    }
    softened = comment
    for old, new in replacements.items():
        softened = softened.replace(old, new)
        softened = softened.replace(old.capitalize(), new)
    return " ".join(softened.split()).strip(" ,")


def soften_product_keywords(comment: str) -> str:
    replacements = {
        "patriotic hanging basket": "porch piece",
        "Patriotic hanging basket": "Porch piece",
        "hanging basket": "porch decor",
        "Hanging basket": "Porch decor",
        "patriotic flowers": "red white & blue flowers",
        "Patriotic flowers": "Red white & blue flowers",
        "artificial flowers": "low-maintenance flowers",
        "Artificial flowers": "Low-maintenance flowers",
        "tricolor": "red white & blue",
        "Tricolor": "Red white & blue",
        "4th of July": "summer holiday",
        "Independence Day": "summer holiday",
    }
    softened = comment
    for old, new in replacements.items():
        softened = softened.replace(old, new)
    return " ".join(softened.split()).strip()


def clean_broken_emoji_text(comment: str) -> str:
    # Mojibake from UTF-8 emoji often starts with "ð" or includes replacement chars.
    cleaned = comment.replace("\ufffd", "")
    cleaned = re.sub(r"ð\S*", "", cleaned)
    cleaned = re.sub(r"[\ud800-\udfff]", "", cleaned)
    return " ".join(cleaned.split()).strip()


def append_safe_emoji(comment: str) -> str:
    if not env_bool("AI_COMMENT_EMOJI_ENABLED", True):
        return comment

    mode = os.getenv("AI_COMMENT_EMOJI_MODE", "safe").strip().lower()
    # BMP symbols avoid the non-BMP emoji encoding path that can turn into "ð���".
    emoji_sets = {
        "safe": ("☺", "♡", "♥"),
        "heart": ("❤", "♥", "♡"),
        "modern": ("😊", "😍", "🥰", "😂", "🙌", "❤️"),
    }
    emojis = emoji_sets.get(mode, emoji_sets["heart"])
    if any(comment.endswith(item) for item in emojis):
        return comment
    return f"{comment.rstrip(' .,!')} {random.choice(emojis)}"


def ascii_for_multipart(value: str) -> str:
    replacements = {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "—": "-",
        "–": "-",
        "；": ";",
        "，": ",",
        "。": ".",
        "：": ":",
        "、": ",",
    }
    cleaned = value
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    return cleaned.encode("ascii", errors="ignore").decode("ascii")


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def render_template(value: str, variables: Dict[str, str]) -> str:
    rendered = value
    for key, item in variables.items():
        rendered = rendered.replace("{" + key + "}", item)
    return rendered


def render_payload_template(template: str, variables: Dict[str, str]) -> Optional[Dict[str, Any]]:
    if not template.strip():
        return None
    rendered = render_template(template, {key: json.dumps(value)[1:-1] for key, value in variables.items()})
    try:
        parsed = json.loads(rendered)
    except ValueError as exc:
        raise ValueError(f"OPENAI_IMAGE_PAYLOAD_JSON is not valid JSON after rendering: {exc}") from exc
    return parsed if isinstance(parsed, dict) else None


class OpenAIContentClient:
    """OpenAI-compatible client modeled after the dispute attribution integration."""

    def __init__(self) -> None:
        import os

        self.enabled = env_bool("OPENAI_ENABLED", env_bool("AI_ATTRIBUTION_ENABLED", True))
        self.api_key = (
            os.getenv("OPENAI_TEXT_API_KEY")
            or os.getenv("AI_ATTRIBUTION_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip()
        self.base_url = (
            os.getenv("OPENAI_TEXT_BASE_URL")
            or os.getenv("AI_ATTRIBUTION_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).strip().rstrip("/")
        self.image_api_key = (os.getenv("OPENAI_IMAGE_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
        self.image_base_url = (os.getenv("OPENAI_IMAGE_BASE_URL") or "https://api.openai.com/v1").strip().rstrip("/")
        self.text_model = (
            os.getenv("OPENAI_TEXT_MODEL")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("AI_ATTRIBUTION_MODEL")
            or "gpt-5.5"
        ).strip()
        self.image_model = (os.getenv("OPENAI_IMAGE_MODEL") or "gpt-image-2").strip()
        self.image_edit_model = (os.getenv("OPENAI_IMAGE_EDIT_MODEL") or self.image_model).strip()
        self.image_api = (os.getenv("OPENAI_IMAGE_API") or "auto").strip().lower()
        self.image_endpoint_url = (os.getenv("OPENAI_IMAGE_ENDPOINT_URL") or "").strip()
        self.image_endpoint_path = (os.getenv("OPENAI_IMAGE_ENDPOINT_PATH") or "/images/generations").strip()
        self.image_edit_endpoint_url = (os.getenv("OPENAI_IMAGE_EDIT_ENDPOINT_URL") or "").strip()
        self.image_edit_endpoint_path = (os.getenv("OPENAI_IMAGE_EDIT_ENDPOINT_PATH") or "/images/edits").strip()
        self.image_reference_enabled = env_bool("OPENAI_IMAGE_REFERENCE_ENABLED", True)
        self.image_reference_field = (os.getenv("OPENAI_IMAGE_REFERENCE_FIELD") or "image[]").strip() or "image[]"
        try:
            reference_limit = int(os.getenv("OPENAI_IMAGE_REFERENCE_LIMIT", "2"))
        except ValueError:
            reference_limit = 2
        self.image_reference_limit = max(1, min(reference_limit, 4))
        self.image_reference_max_side = env_int("OPENAI_IMAGE_REFERENCE_MAX_SIDE", 1024, 256, 2048)
        self.image_reference_jpeg_quality = env_int("OPENAI_IMAGE_REFERENCE_JPEG_QUALITY", 85, 50, 95)
        self.image_edit_retries = env_int("OPENAI_IMAGE_EDIT_RETRIES", 3, 1, 6)
        self.image_output_format = (os.getenv("OPENAI_IMAGE_OUTPUT_FORMAT") or "").strip().lower()
        self.image_payload_json = (os.getenv("OPENAI_IMAGE_PAYLOAD_JSON") or "").strip()
        self.wire_api = (os.getenv("OPENAI_WIRE_API") or os.getenv("AI_ATTRIBUTION_WIRE_API") or "responses").strip().lower()
        self.timeout = openai_timeout_seconds()
        self.comment_cache: Dict[str, Optional[Dict[str, str]]] = {}
        self.session = requests.Session()
        self.openai_session = requests.Session()
        self.openai_image_session = requests.Session()
        self.openai_proxy_mode = (os.getenv("OPENAI_PROXY_MODE") or "env").strip().lower()
        self.openai_image_proxy_mode = (os.getenv("OPENAI_IMAGE_PROXY_MODE") or self.openai_proxy_mode).strip().lower()
        self._apply_proxy_mode(self.openai_session, self.openai_proxy_mode)
        self._apply_proxy_mode(self.openai_image_session, self.openai_image_proxy_mode)
        self.last_error = ""
        self._warn_if_key_looks_wrong()

    @staticmethod
    def _apply_proxy_mode(session: requests.Session, mode: str) -> None:
        if mode in {"direct", "none", "off", "disable", "disabled"}:
            session.trust_env = False

    def ready(self) -> bool:
        return bool(self.enabled and self.api_key and self.base_url)

    def image_ready(self) -> bool:
        return bool(self.enabled and self.image_api_key and self.image_base_url)

    def _warn_if_key_looks_wrong(self) -> None:
        if not self.api_key:
            return
        if "api.openai.com" not in self.base_url:
            return
        if self.api_key.startswith("sk-"):
            return
        print(
            "Warning: OPENAI_BASE_URL points to api.openai.com, but OPENAI_API_KEY does not look like an OpenAI key. "
            "Use a valid OpenAI API key, or change OPENAI_BASE_URL/OPENAI_*_MODEL to match your OpenAI-compatible provider.",
            flush=True,
        )

    def _headers(self, api_key: Optional[str] = None) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key or self.api_key}",
            "Content-Type": "application/json",
        }

    def _multipart_headers(self, api_key: Optional[str] = None) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key or self.api_key}",
        }

    def _post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        api_key: Optional[str] = None,
        use_image_session: bool = False,
    ) -> Optional[Dict[str, Any]]:
        session = self.openai_image_session if use_image_session else self.openai_session
        for attempt in range(1, 3):
            try:
                response = session.post(
                    url,
                    headers=self._headers(api_key),
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code >= 400:
                    self.last_error = response.text[:600]
                    print(
                        f"OpenAI request failed status={response.status_code} attempt={attempt} body={response.text[:600]}",
                        flush=True,
                    )
                    if response.status_code == 401:
                        print(
                            "OpenAI authentication failed. Check OPENAI_API_KEY, or set OPENAI_BASE_URL for your compatible provider.",
                            flush=True,
                        )
                    time.sleep(min(5.0, attempt * 1.5))
                    continue
                data = response.json()
                return data if isinstance(data, dict) else None
            except (requests.RequestException, ValueError) as exc:
                self.last_error = str(exc)
                print(f"OpenAI request error attempt={attempt} error={exc}", flush=True)
                time.sleep(min(5.0, attempt * 1.5))
        return None

    def _post_responses_text(
        self,
        url: str,
        payload: Dict[str, Any],
        api_key: Optional[str] = None,
        use_image_session: bool = False,
    ) -> str:
        # The relay used by the reference project requires stream=true for /responses.
        payload = {**payload, "stream": True}
        session = self.openai_image_session if use_image_session else self.openai_session
        for attempt in range(1, 3):
            try:
                response = session.post(
                    url,
                    headers=self._headers(api_key),
                    json=payload,
                    timeout=self.timeout,
                    stream=True,
                )
                if response.status_code >= 400:
                    self.last_error = response.text[:600]
                    print(
                        f"OpenAI request failed status={response.status_code} attempt={attempt} body={response.text[:600]}",
                        flush=True,
                    )
                    if response.status_code == 401:
                        print(
                            "OpenAI authentication failed. Check OPENAI_API_KEY, or set OPENAI_BASE_URL for your compatible provider.",
                            flush=True,
                        )
                    time.sleep(min(5.0, attempt * 1.5))
                    continue
                return extract_response_stream_text(response)
            except requests.RequestException as exc:
                self.last_error = str(exc)
                print(f"OpenAI streaming request error attempt={attempt} error={exc}", flush=True)
                time.sleep(min(5.0, attempt * 1.5))
        return ""

    def _post_responses_payload(self, url: str, payload: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
        payload = {**payload, "stream": True}
        for attempt in range(1, 3):
            try:
                response = self.openai_session.post(
                    url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                    stream=True,
                )
                if response.status_code >= 400:
                    self.last_error = response.text[:600]
                    print(
                        f"OpenAI request failed status={response.status_code} attempt={attempt} body={response.text[:600]}",
                        flush=True,
                    )
                    time.sleep(min(5.0, attempt * 1.5))
                    continue
                return extract_response_stream_payload(response)
            except requests.RequestException as exc:
                self.last_error = str(exc)
                print(f"OpenAI streaming request error attempt={attempt} error={exc}", flush=True)
                time.sleep(min(5.0, attempt * 1.5))
        return "", None

    def generate_comment(self, post_content: str, language: str, style: str) -> Optional[Dict[str, str]]:
        text = (post_content or "").strip()
        if not text or not self.ready():
            return None
        cache_key = f"{language}\n{style}\n{text}"
        if cache_key in self.comment_cache:
            return self.comment_cache[cache_key]

        prompt = (
            "你是社交媒体评论草稿助手。根据帖子和产品信息生成一条简短、真实、自然的心情式评论。\n"
            "只能输出 JSON，不要解释。\n\n"
            "要求：\n"
            "- 评论要像普通人刷到产品后随手写的一句，不要像 AI、客服、品牌号、广告语或精修文案。\n"
            "- 语气随意一点，有生活气，可以用 &、缩写、口语、轻微不完整句，允许一点点小错字或不完美表达。\n"
            "- 更偏短而有力的夸法：好看 & 实用、pretty & useful、cute & practical、nice & easy、looks handy 这类感觉。\n"
            "- 优先表达两个简单优点，例如好看+实用、顺眼+省事、漂亮+方便、颜色好+好打理。\n"
            "- 不要自己输出 emoji；程序会在最后本地追加一个能表达情绪的表情，避免 Facebook 显示乱码。\n"
            "- 评论要表达看到产品后的即时感受，也可以自然带到一个适合的生活场景，但不要硬凑满所有元素。\n"
            "- 不要直接照搬链接、URL、产品标题里的关键词或完整产品名；要换成自己的理解和自然说法。\n"
            "- 可以用泛称或改写，例如 this porch piece、these flowers、this decor、this little setup、门口这个小装饰、这组花。\n"
            "- 评论重点是自己的感受和理解，不是复述标题、链接词、卖点词。\n"
            "- 禁止和产品介绍高度重合：不要连续复用产品介绍里的短语，不要照搬卖点顺序，不要把标题/描述换几个词后当评论。\n"
            "- 要有一句基础短描述，例如颜色、材质感、大小、造型、节日感、摆放效果、方便、省心、实用等，但不要展开介绍。\n"
            "- 场景可以是门口、阳台、院子、厨房、客厅、通勤、周末、节日布置、送礼等，必须贴合帖子或产品。\n"
            "- 可以写自己的摆放偏好或使用想法，例如 I'd put it by the door / I'd use it on the porch / 我会放门口。\n"
            "- 可以写家人视角的推测，例如 my mom would like this / 家里人应该会喜欢；只有真实体验素材提供时，才写 my family likes it / 家人也喜欢。\n"
            "- 可以自然使用 it / this / that，不用刻意说产品名；像平时聊天一样，例如 “it looks pretty easy to use”。\n"
            "- 可以用“ngl...”“honestly...”“I'd put it...”“my mom would...”“it looks...”“it would...”“... kinda works for...”这类自然表达。\n"
            "- 不要固定套用“产品简称 + 基础特点 + 场景 + 心情”的结构；这只是信息参考，不是模板。\n"
            "- 不要长篇描述产品，不要堆功能和卖点。\n"
            "- 英文评论不要以 The/the 开头，可以用 it、ngl、honestly、this、that、kinda、looks、would、so 等更随手的开头。\n"
            "- 禁止使用 boom、mood handled、July mood、instantly cheerful、perfect for、must-have、game changer 这类 slogan/广告感表达。\n"
            "- 每次都要换表达角度、开头、场景和用词，避免生成和常见模板差不多的评论。\n"
            "- 只有当风格或真实体验素材明确提供时，才可以写 received/got/used/mine/my family likes/朋友家人夸/收到/实物/用起来/家人也喜欢 等亲历内容。\n"
            "- 避免机械套话、官方腔、过度完美的形容词和感叹号堆砌。\n"
            "- 不要伪装成亲历者，不要编造自己做过、买过、见过。\n"
            "- 不要包含链接、广告、标签、诱导关注或批量营销语气。\n"
            "- 保持简短，1 个短句，18 个英文词或 40 个中文字符以内。\n"
            f"- 输出语言：{language}。\n"
            f"- 风格：{style}。\n"
            "- 输出格式必须是：{\"comment\":\"...\",\"rationale\":\"...\"}\n\n"
            f"帖子/产品内容：\n{text[:6000]}"
        )
        def request_comment_content(
            *,
            base_url: str,
            model: str,
            wire_api: str,
            api_key: str,
            use_image_session: bool,
            label: str,
        ) -> str:
            use_responses = wire_api in {"responses", "response"}
            if use_responses:
                url = f"{base_url}/responses"
                payload = {
                    "model": model,
                    "input": [
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": prompt}],
                        }
                    ],
                    "text": {"format": {"type": "json_object"}},
                }
                print(f"Generating comment with {label}: model={model} wire=responses", flush=True)
                return self._post_responses_text(
                    url,
                    payload,
                    api_key=api_key,
                    use_image_session=use_image_session,
                )

            url = f"{base_url}/chat/completions"
            payload = {
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "你是社交媒体评论草稿助手。只能输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
            }
            print(f"Generating comment with {label}: model={model} wire=chat", flush=True)
            data = self._post_json(
                url,
                payload,
                api_key=api_key,
                use_image_session=use_image_session,
            )
            return extract_chat_text(data)

        content = request_comment_content(
            base_url=self.base_url,
            model=self.text_model,
            wire_api=self.wire_api,
            api_key=self.api_key,
            use_image_session=False,
            label="primary text key",
        )
        if not normalize_comment(parse_json_object(content).get("comment")) and not normalize_comment(content):
            fallback_enabled = env_bool("OPENAI_TEXT_FALLBACK_TO_IMAGE_KEY", True)
            if fallback_enabled and self.image_api_key:
                fallback_base_url = (os.getenv("OPENAI_TEXT_FALLBACK_BASE_URL") or self.image_base_url).strip().rstrip("/")
                fallback_model = (os.getenv("OPENAI_TEXT_FALLBACK_MODEL") or self.text_model).strip()
                fallback_wire_api = (os.getenv("OPENAI_TEXT_FALLBACK_WIRE_API") or self.wire_api).strip().lower()
                print("Primary comment generation failed; retrying with image OpenAI key.", flush=True)
                content = request_comment_content(
                    base_url=fallback_base_url,
                    model=fallback_model,
                    wire_api=fallback_wire_api,
                    api_key=self.image_api_key,
                    use_image_session=True,
                    label="fallback image key",
                )
        parsed = parse_json_object(content)
        comment = normalize_comment(parsed.get("comment"))
        if not comment:
            comment = normalize_comment(content)
        if comment:
            comment = soften_comment_start(comment)
            comment = clean_broken_emoji_text(comment)
            comment = soften_mechanical_phrases(comment)
            comment = soften_product_keywords(comment)
            comment = append_safe_emoji(comment)
        result = {"comment": comment or "", "rationale": str(parsed.get("rationale") or "").strip()}
        if not result["comment"]:
            self.comment_cache[cache_key] = None
            return None
        self.comment_cache[cache_key] = result
        return result

    def generate_image(self, prompt: str, output_path: str, size: str = "1024x1024", quality: str = "auto") -> Optional[str]:
        image_prompt = (prompt or "").strip()
        if not image_prompt:
            return None
        if not self.image_ready():
            self.last_error = "Image generation requires OPENAI_IMAGE_API_KEY or OPENAI_API_KEY plus OPENAI_IMAGE_BASE_URL."
            return None

        if self.image_payload_json:
            return self._generate_image_custom(image_prompt, output_path, size, quality)

        if self.image_api not in {"auto", "images", "image", "generations", "images_generations"}:
            self.last_error = f"Unsupported OPENAI_IMAGE_API for image generation: {self.image_api}"
            return None

        payload: Dict[str, Any] = {
            "model": self.image_model,
            "prompt": image_prompt[:8000],
            "size": size,
            "n": 1,
        }
        if quality:
            payload["quality"] = quality
        if self.image_output_format:
            payload["output_format"] = self.image_output_format
        data = self._post_json(
            self._image_endpoint_url(),
            payload,
            api_key=self.image_api_key,
            use_image_session=True,
        )
        if not data:
            return None
        return self._save_image_response(data, output_path)

    def generate_image_with_references(
        self,
        prompt: str,
        output_path: str,
        reference_urls: list[str],
        size: str = "1024x1024",
        quality: str = "auto",
    ) -> Optional[str]:
        image_prompt = (prompt or "").strip()
        if not image_prompt:
            return None
        if not self.image_ready():
            self.last_error = "Image generation requires OPENAI_IMAGE_API_KEY or OPENAI_API_KEY plus OPENAI_IMAGE_BASE_URL."
            return None
        if not self.image_reference_enabled:
            self.last_error = "Reference image generation is disabled by OPENAI_IMAGE_REFERENCE_ENABLED=false."
            return None

        references = self._download_reference_images(reference_urls[: self.image_reference_limit])
        if not references:
            self.last_error = "No usable landing-page product image references could be downloaded."
            return None

        attempts: list[tuple[str, list[tuple[str, bytes, str]]]] = [
            ("image", references[:1]),
            ("image[]", references[:1]),
        ]
        if len(references) > 1:
            attempts.append(("image[]", references[: self.image_reference_limit]))
        if self.image_reference_field not in {"image", "image[]"}:
            attempts.append((self.image_reference_field, references[: self.image_reference_limit]))

        seen: set[tuple[str, tuple[str, ...]]] = set()
        for image_field, attempt_refs in attempts:
            key = (image_field, tuple(name for name, _, _ in attempt_refs))
            if key in seen:
                continue
            seen.add(key)
            result = self._generate_image_edit(image_prompt, output_path, attempt_refs, size, quality, image_field)
            if result:
                return result
        return None

    def _image_endpoint_url(self) -> str:
        if self.image_endpoint_url:
            return self.image_endpoint_url
        path = self.image_endpoint_path if self.image_endpoint_path.startswith("/") else f"/{self.image_endpoint_path}"
        return f"{self.image_base_url}{path}"

    def _image_edit_endpoint_url(self) -> str:
        if self.image_edit_endpoint_url:
            return self.image_edit_endpoint_url
        path = self.image_edit_endpoint_path if self.image_edit_endpoint_path.startswith("/") else f"/{self.image_edit_endpoint_path}"
        return f"{self.image_base_url}{path}"

    def _download_reference_images(self, urls: list[str]) -> list[tuple[str, bytes, str]]:
        references: list[tuple[str, bytes, str]] = []
        for index, url in enumerate(urls, start=1):
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                print(f"Could not download product reference image: {url} error={exc}", flush=True)
                continue

            content = response.content
            if len(content) < 1000:
                print(f"Skipping tiny product reference image: {url}", flush=True)
                continue
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                content_type = mimetypes.guess_type(urlparse(url).path)[0] or "image/png"
            if content_type not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
                print(f"Skipping unsupported reference image type={content_type}: {url}", flush=True)
                continue
            if content_type == "image/jpg":
                content_type = "image/jpeg"
            suffix = ".jpg" if content_type == "image/jpeg" else ".webp" if content_type == "image/webp" else ".png"
            name, content, content_type = self._prepare_reference_image(
                f"product_reference_{index}{suffix}",
                content,
                content_type,
            )
            references.append((name, content, content_type))
        return references

    def _prepare_reference_image(self, name: str, content: bytes, content_type: str) -> tuple[str, bytes, str]:
        try:
            from PIL import Image, ImageOps
        except ImportError:
            print("Pillow is not installed; uploading original product reference image.", flush=True)
            return name, content, content_type

        try:
            with Image.open(BytesIO(content)) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                    background = Image.new("RGB", image.size, (255, 255, 255))
                    background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
                    image = background
                else:
                    image = image.convert("RGB")
                image.thumbnail((self.image_reference_max_side, self.image_reference_max_side))
                output = BytesIO()
                image.save(output, format="JPEG", quality=self.image_reference_jpeg_quality, optimize=True)
        except Exception as exc:
            print(f"Could not optimize product reference image {name}: {exc}; uploading original.", flush=True)
            return name, content, content_type

        optimized = output.getvalue()
        optimized_name = f"{Path(name).stem}.jpg"
        print(
            "Optimized product reference image "
            f"{name} -> {optimized_name} {len(content)} bytes -> {len(optimized)} bytes",
            flush=True,
        )
        return optimized_name, optimized, "image/jpeg"

    def _generate_image_edit(
        self,
        prompt: str,
        output_path: str,
        references: list[tuple[str, bytes, str]],
        size: str,
        quality: str,
        image_field: str,
    ) -> Optional[str]:
        url = self._image_edit_endpoint_url()
        safe_prompt = ascii_for_multipart(prompt[:8000])
        data_fields: Dict[str, str] = {
            "model": self.image_edit_model,
            "prompt": safe_prompt,
            "size": size,
            "n": "1",
        }
        if quality:
            data_fields["quality"] = quality
        if self.image_output_format:
            data_fields["output_format"] = self.image_output_format

        files = [(image_field, (name, content, content_type)) for name, content, content_type in references]
        headers = self._multipart_headers(self.image_api_key)
        refs_label = ", ".join(f"{name}:{content_type}:{len(content)}B" for name, content, content_type in references)
        for attempt in range(1, self.image_edit_retries + 1):
            print(
                "Sending reference image edit request "
                f"attempt={attempt}/{self.image_edit_retries} model={self.image_edit_model} "
                f"field={image_field} refs={refs_label}",
                flush=True,
            )
            try:
                response = self.openai_image_session.post(
                    url,
                    headers=headers,
                    data=data_fields,
                    files=files,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                self.last_error = str(exc)
                print(
                    f"OpenAI image edit request error attempt={attempt}/{self.image_edit_retries}: {exc}",
                    flush=True,
                )
                if attempt < self.image_edit_retries:
                    time.sleep(min(20.0, 3.0 * attempt * attempt))
                    continue
                return None

            if response.status_code in {408, 409, 425, 429} or response.status_code >= 500:
                self.last_error = response.text[:1000]
                print(
                    "OpenAI image edit retryable failure "
                    f"attempt={attempt}/{self.image_edit_retries} status={response.status_code} "
                    f"body={response.text[:1000]}",
                    flush=True,
                )
                if attempt < self.image_edit_retries:
                    time.sleep(min(20.0, 3.0 * attempt * attempt))
                    continue
                return None
            break

        if response.status_code >= 400:
            self.last_error = response.text[:1000]
            print(
                f"OpenAI image edit failed status={response.status_code} body={response.text[:1000]}",
                flush=True,
            )
            return None

        try:
            data = response.json()
        except ValueError:
            self.last_error = response.text[:1000]
            return None
        return self._save_image_response(data, output_path)

    def _generate_image_custom(self, prompt: str, output_path: str, size: str, quality: str) -> Optional[str]:
        variables = {
            "model": self.image_model,
            "prompt": prompt[:8000],
            "size": size,
            "quality": quality,
        }
        payload = render_payload_template(self.image_payload_json, variables)
        if not payload:
            return None
        data = self._post_json(
            self._image_endpoint_url(),
            payload,
            api_key=self.image_api_key,
            use_image_session=True,
        )
        if not data:
            return None
        b64_json, image_url = find_image_reference(data)
        if b64_json or image_url:
            return self._save_image_reference(b64_json, image_url, output_path)
        content = extract_chat_text(data) or extract_response_text(data)
        parsed = parse_json_object(content)
        b64_json, image_url = find_image_reference(parsed)
        if b64_json or image_url:
            return self._save_image_reference(b64_json, image_url, output_path)
        return None

    def _save_image_response(self, data: Dict[str, Any], output_path: str) -> Optional[str]:
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            return None
        first = rows[0] if isinstance(rows[0], dict) else {}
        b64_json, image_url = find_image_reference(first)
        return self._save_image_reference(b64_json, image_url, output_path)

    def _save_image_reference(self, b64_json: Optional[str], image_url: Optional[str], output_path: str) -> Optional[str]:
        if b64_json:
            path = Path(output_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(b64_json))
            return str(path)

        if image_url:
            response = self.openai_image_session.get(image_url, timeout=self.timeout)
            response.raise_for_status()
            path = Path(output_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
            return str(path)
        return None


def build_post_image_prompt(post_content: str, style: str) -> str:
    content = " ".join((post_content or "").split())
    return (
        "Create a tasteful, non-clickbait social media illustration inspired by the following post. "
        "Do not include platform logos, UI chrome, watermarks, or readable text unless essential. "
        f"Style: {style}. "
        f"Post content: {content[:2500]}"
    )


PRODUCT_PHOTO_COMPOSITION_PRESETS = [
    (
        "diagonal walk-by phone shot",
        "Shoot from a diagonal walk-by angle, like someone noticed the product while passing by and snapped a quick phone photo.",
    ),
    (
        "noticeably top-down casual shot",
        "Use a clearly top-down but still natural phone-camera angle, as if standing close and looking down at the product.",
    ),
    (
        "low-angle near-floor shot",
        "Place the camera near floor or ground level, looking slightly upward and across the product without distorting its shape.",
    ),
    (
        "wide off-axis environmental shot",
        "Use a wider off-axis shot that shows more doorway, patio, floor, or room around the product; do not center it like a catalog image.",
    ),
    (
        "close-up detail but still recognizable",
        "Use a closer detail-oriented crop from an angled side, showing texture and material while keeping enough of the product visible to identify it.",
    ),
    (
        "strong side-angle patio shot",
        "Shoot from a strong side angle on a porch, patio, table, or hallway so the product is clearly not straight-on or catalog-like.",
    ),
    (
        "crooked off-center corner placement",
        "Compose the product off-center near a wall, doorframe, railing, table edge, or corner, with a slightly imperfect handheld frame.",
    ),
    (
        "casual tilted handheld shot",
        "Use a visible but realistic handheld phone-photo tilt and natural framing, avoiding perfect symmetry while keeping the product accurate.",
    ),
    (
        "from-behind doorway angle",
        "Shoot from a back-corner or doorway-side angle so the product is seen from a less obvious side, still recognizable and accurate.",
    ),
    (
        "low diagonal close phone shot",
        "Use a low diagonal phone angle from very close to the surface, with the product rising naturally in the frame.",
    ),
    (
        "high corner glance shot",
        "Use a high corner angle, as if the phone is held casually from one side above the product, not directly centered overhead.",
    ),
    (
        "messy real-life snapshot angle",
        "Use an imperfect real-life snapshot angle with natural uneven framing; the product should feel found in a normal space, not staged.",
    ),
]


def choose_product_photo_composition() -> tuple[str, str]:
    viewpoint_name, viewpoint = random.choice(PRODUCT_PHOTO_COMPOSITION_PRESETS)
    distance_name, distance = random.choice(
        [
            ("tight crop", "Use a tight crop with the product filling most of the frame, while still recognizable."),
            ("medium complete-product shot", "Use a medium distance so the entire product is visible with a small amount of surrounding context."),
            ("wide lifestyle shot", "Use a wider lifestyle distance with more environment visible, but keep the product clearly dominant."),
            ("foreground-edge shot", "Place a small foreground edge such as a table edge, door mat, or railing near the frame to make it feel casually taken."),
            ("partial off-frame crop", "Let a harmless edge of the environment crop the frame naturally, but do not crop away key product features."),
            ("awkward quick-snapshot distance", "Use a slightly awkward quick-snapshot distance, neither perfectly close nor perfectly wide, like a real phone photo."),
            ("diagonal depth shot", "Use diagonal depth in the frame, with the product not parallel to the camera plane."),
        ]
    )
    height_name, height = random.choice(
        [
            ("standing eye-level", "Camera height is like a person standing nearby with a phone."),
            ("waist-level", "Camera height is around waist level, natural and informal."),
            ("near-ground", "Camera height is low near the floor or ground."),
            ("tabletop height", "Camera height is level with a table, shelf, step, or porch surface."),
            ("slightly overhead", "Camera is slightly overhead but not a flat lay unless the product naturally sits on a surface."),
            ("knee-height", "Camera height is around knee level, like someone crouched only a little to take a quick photo."),
            ("high handheld corner", "Camera is held high from one corner, angled down but not directly overhead."),
        ]
    )
    placement_name, placement = random.choice(
        [
            ("left third", "Place the product on the left third of the image."),
            ("right third", "Place the product on the right third of the image."),
            ("lower third", "Place the product lower in the frame with real environment above or behind it."),
            ("corner placement", "Place the product near a realistic corner, doorway, railing, shelf edge, or patio edge."),
            ("asymmetric center", "Keep the product near center but with an intentionally imperfect, handheld asymmetric frame."),
            ("partly near frame edge", "Place the product close to one frame edge, with breathing room on the opposite side."),
            ("diagonal floor-line composition", "Use a diagonal floor, tabletop, railing, or wall line to break up straight-on symmetry."),
        ]
    )
    scene_name, scene = random.choice(
        [
            ("front door threshold", "Use a front door threshold with a doormat, door frame, or porch texture."),
            ("side porch", "Use a side porch or patio corner with railing, siding, or concrete floor."),
            ("hallway entry", "Use an indoor hallway or entryway with natural window light."),
            ("garden edge", "Use a garden edge, yard path, planter area, or outdoor step."),
            ("kitchen or dining table", "Use a kitchen, dining table, shelf, or ordinary home surface if appropriate for the product."),
            ("unboxing area", "Use a casual unpacking area with a plain box, floor, or table nearby, without covering the product."),
        ]
    )
    light_name, light = random.choice(
        [
            ("soft morning light", "Lighting is soft morning daylight with gentle shadows."),
            ("late afternoon light", "Lighting is warm late-afternoon natural light."),
            ("overcast daylight", "Lighting is neutral overcast daylight, not dramatic."),
            ("indoor window light", "Lighting is indoor natural window light."),
            ("mixed realistic home light", "Lighting is a realistic mix of room light and daylight, like a quick phone photo."),
        ]
    )
    name = " / ".join([viewpoint_name, distance_name, height_name, placement_name, scene_name, light_name])
    instruction = " ".join([viewpoint, distance, height, placement, scene, light])
    return name, instruction


def build_product_scene_image_prompt(
    post_content: str,
    product_context: str,
    use_cases: str,
    style: str,
    composition: str = "",
) -> str:
    post = " ".join((post_content or "").split())
    product = " ".join((product_context or "").split())
    scenarios = " ".join((use_cases or "").split())
    composition_text = " ".join((composition or "").split())
    return (
        "Use the provided product reference image(s) as the source of truth. "
        "Create a realistic user-taken product photo by keeping the exact referenced product and placing it in a completely new everyday surrounding scene. "
        "Highest priority: the product must match the landing-page product as closely as possible. "
        "Do not redesign, beautify, simplify, recolor, resize, or reinterpret the product. "
        "Keep the same product type, shape, structure, color pattern, material feel, density/fullness, proportions, visible parts, and overall look shown in the landing-page context and image references. "
        "Use the reference image only for the product appearance, not for the background. "
        "Do not copy, reuse, extend, or imitate the reference image background, props, surface, lighting setup, camera angle, packaging layout, studio setup, or original scene elements. "
        "Remove/replace the original reference background entirely with a new realistic life scene. "
        "Only the product should remain consistent; the surrounding environment must be newly generated. "
        "Add a simple real-life surrounding scene around the product; the product itself should look like the exact same item from the landing page. "
        "If product image reference URLs are listed in the context, treat them as the visual source of truth for product appearance. "
        "The product should be the main subject, shown clearly with accurate visible details, but not staged like a studio ad. "
        "Do not include any people, faces, hands, arms, body parts, silhouettes, or crowds. "
        "Show an everyday real-life setting: porch, doorway, patio, balcony, yard, kitchen table, hallway, or unpacking area as appropriate. "
        "The scene should feel casual and lived-in, with small normal details such as a doormat, railing, box, table edge, floor texture, garden tools, or natural clutter when relevant. "
        "Keep the added scene minimal and grounded; do not let props cover, replace, or distract from the product. "
        "Avoid overly perfect symmetry, luxury styling, spotless showroom composition, dramatic lighting, or deliberate advertising poses. "
        "Do not blur the background; keep the whole scene naturally in focus with normal phone-camera depth of field. "
        "Use realistic indoor/outdoor lighting, natural shadows, and ordinary phone photo framing. "
        "Camera and composition: use a different natural phone-camera viewpoint for this image. "
        f"Selected viewpoint: {composition_text or 'natural varied phone-camera product angle'}. "
        "Strong variation requirement: the camera angle, camera height, product placement, scene type, lighting, and crop should noticeably differ from a typical centered straight-on product photo. "
        "Make the viewpoint feel like a different real customer photo each time, not a repeated template. "
        "The image should feel more casual and spontaneous than polished: off-axis, imperfectly framed, slightly diagonal, and not too front-facing. "
        "Avoid the product facing the camera squarely in the exact center; prefer a side, diagonal, high-corner, low-corner, or walk-by viewpoint. "
        "Do not make a clean product catalog shot, studio shot, or perfectly centered hero image. "
        "Only vary camera angle, distance, framing, crop, and background. "
        "Do not vary the product design, colors, structure, pattern, proportions, material, or visible details. "
        "The full product should remain recognizable; if using a close-up detail crop, keep enough of the item visible to identify it. "
        "Avoid repeating a straight-on centered catalog angle unless the selected viewpoint explicitly requires it. "
        "Do not include Facebook UI, platform logos, watermarks, price tags, QR codes, readable text, or captions. "
        f"Visual style: {style}. "
        f"Suggested customer-use setting: {scenarios or 'derive a realistic everyday setting from the product details'}. "
        f"Post context: {post[:1800]}. "
        f"Product context: {product[:3200]}."
    )
