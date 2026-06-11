import base64
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests


def env_bool(name: str, default: bool = False) -> bool:
    import os

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def openai_timeout_seconds() -> float:
    import os

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
        self.image_api = (os.getenv("OPENAI_IMAGE_API") or "auto").strip().lower()
        self.image_endpoint_url = (os.getenv("OPENAI_IMAGE_ENDPOINT_URL") or "").strip()
        self.image_endpoint_path = (os.getenv("OPENAI_IMAGE_ENDPOINT_PATH") or "/images/generations").strip()
        self.image_output_format = (os.getenv("OPENAI_IMAGE_OUTPUT_FORMAT") or "").strip().lower()
        self.image_payload_json = (os.getenv("OPENAI_IMAGE_PAYLOAD_JSON") or "").strip()
        self.wire_api = (os.getenv("OPENAI_WIRE_API") or os.getenv("AI_ATTRIBUTION_WIRE_API") or "responses").strip().lower()
        self.timeout = openai_timeout_seconds()
        self.comment_cache: Dict[str, Optional[Dict[str, str]]] = {}
        self.session = requests.Session()
        self.last_error = ""
        self._warn_if_key_looks_wrong()

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

    def _post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        api_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        for attempt in range(1, 3):
            try:
                response = self.session.post(url, headers=self._headers(api_key), json=payload, timeout=self.timeout)
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

    def _post_responses_text(self, url: str, payload: Dict[str, Any]) -> str:
        # The relay used by the reference project requires stream=true for /responses.
        payload = {**payload, "stream": True}
        for attempt in range(1, 3):
            try:
                response = self.session.post(
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
                response = self.session.post(
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
            "- 评论要像普通人看到这个产品后的即时感受，重点表达心情，不要展开介绍产品。\n"
            "- 可以用“看到这个就觉得...”“这个颜色/质感看着...”“心情一下...”这类自然表达。\n"
            "- 只点到一个产品细节即可，例如颜色、质感、摆放氛围、实用感，不要堆功能和卖点。\n"
            "- 避免机械套话、官方腔、过度完美的形容词和感叹号堆砌。\n"
            "- 不要伪装成亲历者，不要编造自己做过、买过、见过。\n"
            "- 不要包含链接、广告、标签、诱导关注或批量营销语气。\n"
            "- 保持简短，1 个短句，18 个英文词或 35 个中文字符以内。\n"
            f"- 输出语言：{language}。\n"
            f"- 风格：{style}。\n"
            "- 输出格式必须是：{\"comment\":\"...\",\"rationale\":\"...\"}\n\n"
            f"帖子/产品内容：\n{text[:6000]}"
        )
        use_responses = self.wire_api in {"responses", "response"}
        if use_responses:
            url = f"{self.base_url}/responses"
            payload = {
                "model": self.text_model,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}],
                    }
                ],
                "text": {"format": {"type": "json_object"}},
            }
        else:
            url = f"{self.base_url}/chat/completions"
            payload = {
                "model": self.text_model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "你是社交媒体评论草稿助手。只能输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
            }

        if use_responses:
            content = self._post_responses_text(url, payload)
        else:
            data = self._post_json(url, payload)
            content = extract_chat_text(data)
        parsed = parse_json_object(content)
        comment = normalize_comment(parsed.get("comment"))
        if not comment:
            comment = normalize_comment(content)
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
        data = self._post_json(self._image_endpoint_url(), payload, api_key=self.image_api_key)
        if not data:
            return None
        return self._save_image_response(data, output_path)

    def _image_endpoint_url(self) -> str:
        if self.image_endpoint_url:
            return self.image_endpoint_url
        path = self.image_endpoint_path if self.image_endpoint_path.startswith("/") else f"/{self.image_endpoint_path}"
        return f"{self.image_base_url}{path}"

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
        data = self._post_json(self._image_endpoint_url(), payload, api_key=self.image_api_key)
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
            response = self.session.get(image_url, timeout=self.timeout)
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


def build_product_scene_image_prompt(post_content: str, product_context: str, use_cases: str, style: str) -> str:
    post = " ".join((post_content or "").split())
    product = " ".join((product_context or "").split())
    scenarios = " ".join((use_cases or "").split())
    return (
        "Create a polished product-focused image for social media. "
        "The product must be the clear hero of the image, shown fully and attractively with accurate visible details. "
        "Do not include any people, faces, hands, arms, body parts, silhouettes, or crowds. "
        "Use a simple believable setting only to support the product, not to distract from it. "
        "Make the image realistic, clean, warm, and focused on the product's color, texture, shape, and use context. "
        "Do not include Facebook UI, platform logos, watermarks, price tags, QR codes, or readable text. "
        f"Visual style: {style}. "
        f"Suggested product setting: {scenarios or 'derive the most relevant simple setting from the product details'}. "
        f"Post context: {post[:1800]}. "
        f"Product context: {product[:3200]}."
    )
