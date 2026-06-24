import argparse
import base64
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException, StaleElementReferenceException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from openai_content_client import (
    OpenAIContentClient,
    build_post_image_prompt,
    build_product_scene_image_prompt,
    choose_product_photo_composition,
    extract_image_urls_from_context,
)


DEFAULT_LOGIN_URL = "https://www.facebook.com/login"
APP_CODE_VERSION = "login-cookie-debug-2026-06-17"
DEFAULT_USERNAME_SELECTOR = 'input[name="email"]'
DEFAULT_PASSWORD_SELECTOR = 'input[name="pass"]'
DEFAULT_COMMENT_BOX_LOCATORS = (
    (By.CSS_SELECTOR, 'div[role="textbox"][contenteditable="true"][aria-label*="评论"]'),
    (By.CSS_SELECTOR, 'div[role="textbox"][contenteditable="true"][aria-label*="Comment"]'),
    (By.CSS_SELECTOR, 'div[role="textbox"][contenteditable="true"][aria-label*="comment"]'),
    (By.CSS_SELECTOR, 'div[role="textbox"][contenteditable="true"][aria-placeholder*="评论"]'),
    (By.CSS_SELECTOR, 'div[role="textbox"][contenteditable="true"][aria-placeholder*="Comment"]'),
    (By.CSS_SELECTOR, 'div[role="textbox"][contenteditable="true"][aria-placeholder*="comment"]'),
    (
        By.XPATH,
        "//div[@role='textbox' and @contenteditable='true' and "
        "(contains(@aria-label,'评论') or contains(@aria-label,'Comment') or "
        "contains(@aria-label,'comment') or contains(@aria-placeholder,'评论') or "
        "contains(@aria-placeholder,'Comment') or contains(@aria-placeholder,'comment'))]",
    ),
    (By.CSS_SELECTOR, 'div[role="textbox"][contenteditable="true"]'),
)
DEFAULT_POST_CONTENT_LOCATORS = (
    (By.CSS_SELECTOR, '[role="article"]'),
    (By.CSS_SELECTOR, '[data-ad-preview="message"]'),
    (By.CSS_SELECTOR, '[data-ad-comet-preview="message"]'),
)
FACEBOOK_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "l.facebook.com",
    "lm.facebook.com",
    "web.facebook.com",
}
NON_PRODUCT_HOSTS = {
    "app.salesmartly.com",
    "salesmartly.com",
    "www.salesmartly.com",
    "instagram.com",
    "www.instagram.com",
    "messenger.com",
    "www.messenger.com",
    "whatsapp.com",
    "www.whatsapp.com",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "tiktok.com",
    "www.tiktok.com",
    "twitter.com",
    "x.com",
}

DEFAULT_COMMENT_ANGLES = (
    "主打“好看 & 实用”这种短评，像随手夸一句，不要展开。",
    "说产品看着顺眼又省事，语气轻松，别像广告。",
    "强调这个产品放家里/门口不会突兀，还挺有用。",
    "用 cute & practical / pretty & easy / nice & useful 这类短而有力的感觉。",
    "围绕产品一个小特点，直接说好看、实用、省心、方便中的两个点。",
    "像朋友聊天：这个产品看着还挺值/挺省事/挺顺眼，但不要说已购买。",
    "偏生活化：这个产品让一个小角落变好看一点，也不用太费心。",
    "用个人偏好口吻：I'd put it by the door / 我会放门口这种，但不要说已经用过。",
    "写家人视角时用推测口吻：my mom would like it / 家里人应该会喜欢，别说已经喜欢。",
    "评论要短、有力、不完整也可以，别把理由解释太满。",
    "可以用 &，最后会本地加一个安全表情；句子要像人写的，不要有 slogan 感。",
)

COMMENT_OPENING_STYLES = (
    "开头用 honestly / okay / lowkey / not gonna lie / this kind of thing / I mean 之一，但不要每次一样。",
    "不要用固定开头；可以直接从产品用途、摆放位置、颜色、低维护、节日氛围中任一点切入。",
    "用很短的碎片句，像刷到帖子随手回复，不要完整广告句。",
    "用个人偏好口吻，但只表达 would / looks / seems，不声称已经买过或用过。",
    "用轻微惊喜口吻，比如 didn't expect / actually / kinda，但不要夸张。",
)

COMMENT_DETAIL_FOCUS = (
    "本次只提一个细节：颜色、摆放位置、省心、门口氛围、节日感、清洁维护、角落装饰、整体顺眼，随机取其一，不要面面俱到。",
    "避免重复 by the door；可换成 porch / entryway / patio / little corner / front step / hallway / outside spot。",
    "避免重复 cute colors + no fuss 组合；换成 bright but easy / simple and cheerful / looks tidy / low effort / nice little pop。",
    "不要总说 product；多用 this / it / these / that little setup。",
    "允许轻微口语省略，比如 would look good there / easy win / nice little touch。",
)

COMMENT_LENGTH_STYLES = (
    "长度控制在 8-13 个英文词。",
    "长度控制在 12-18 个英文词。",
    "写成一个短句，不要逗号超过 1 个。",
    "写成两个很短的片段，可以用 & 连接。",
    "句子节奏要和上次不同：开头、词序、场景词都换掉。",
)

EXPERIENCE_COMMENT_ANGLES = (
    "基于真实体验素材，写收到实物后和预期差不多或更好的感觉，别夸过头。",
    "基于真实体验素材，写产品使用/摆放比较方便，以及当时心情。",
    "基于真实体验素材，写朋友或家人真实反馈不错，但保持一句话、像随口说。",
    "基于真实体验素材，写实物的一个基础特点和使用场景，比想象中顺眼。",
    "基于真实体验素材，写我喜欢把它放在哪里，或者家人也喜欢这个小布置。",
)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def validate_credentials(username: str, password: str) -> None:
    example_values = {
        "your_email_or_phone",
        "your_password",
        "your_password_here",
        "你的邮箱或手机号",
        "你的密码",
    }
    if username in example_values or password in example_values:
        raise ValueError("Please edit .env first and replace LOGIN_USERNAME / LOGIN_PASSWORD with your real values.")


def mask_account_value(value: str) -> str:
    if not value:
        return "<empty>"
    if "@" in value:
        name, domain = value.split("@", 1)
        return f"{name[:2]}***@{domain}"
    return f"{value[:3]}***"


def normalize_account_config(raw_config) -> dict[str, str]:
    if not isinstance(raw_config, dict):
        raise ValueError("Account config must be a JSON object.")

    aliases = {
        "username": "LOGIN_USERNAME",
        "login_username": "LOGIN_USERNAME",
        "email": "LOGIN_USERNAME",
        "phone": "LOGIN_USERNAME",
        "password": "LOGIN_PASSWORD",
        "login_password": "LOGIN_PASSWORD",
        "profile_dir": "CHROME_PROFILE_DIR",
        "chrome_profile_dir": "CHROME_PROFILE_DIR",
        "attach_existing": "CHROME_ATTACH_EXISTING",
        "chrome_attach_existing": "CHROME_ATTACH_EXISTING",
        "skip_login": "SKIP_LOGIN",
        "experience_notes": "AI_COMMENT_EXPERIENCE_NOTES",
        "comment_experience_notes": "AI_COMMENT_EXPERIENCE_NOTES",
        "comment_angles": "AI_COMMENT_ANGLES",
    }
    allowed = {
        "LOGIN_USERNAME",
        "LOGIN_PASSWORD",
        "CHROME_PROFILE_DIR",
        "CHROME_ATTACH_EXISTING",
        "SKIP_LOGIN",
        "AI_COMMENT_EXPERIENCE_NOTES",
        "AI_COMMENT_ANGLES",
    }
    normalized: dict[str, str] = {}
    for key, value in raw_config.items():
        env_key = aliases.get(str(key).lower(), str(key).upper())
        if env_key not in allowed:
            continue
        if isinstance(value, bool):
            normalized[env_key] = "true" if value else "false"
        elif isinstance(value, (list, dict)):
            normalized[env_key] = json.dumps(value, ensure_ascii=False)
        elif value is not None:
            normalized[env_key] = str(value)
    return normalized


def apply_account_config(accounts_file: str, account_name: str | None) -> None:
    if not account_name:
        return

    path = Path(accounts_file).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Accounts file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Accounts file must be a JSON object.")

    accounts = data.get("accounts", data)
    if not isinstance(accounts, dict):
        raise ValueError("Accounts file must contain an object or an 'accounts' object.")
    if account_name not in accounts:
        available = ", ".join(sorted(str(name) for name in accounts.keys()))
        raise ValueError(f"Account {account_name!r} not found in {path}. Available: {available or '<none>'}")

    config = normalize_account_config(accounts[account_name])
    for key, value in config.items():
        os.environ[key] = value

    print(
        "Using account "
        f"{account_name!r}: username={mask_account_value(config.get('LOGIN_USERNAME', os.getenv('LOGIN_USERNAME', '')))}, "
        f"profile_dir={config.get('CHROME_PROFILE_DIR', os.getenv('CHROME_PROFILE_DIR', '<not set>'))}",
        flush=True,
    )


def parse_comment_items(value: str) -> list[str]:
    raw = (value or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]

    separators = ("||", "\n", ";", "；")
    items = [raw]
    for separator in separators:
        if separator in raw:
            items = raw.split(separator)
            break
    return [item.strip() for item in items if item.strip()]


def build_comment_style(base_style: str) -> str:
    experience_notes = (os.getenv("AI_COMMENT_EXPERIENCE_NOTES") or "").strip()
    custom_angles = parse_comment_items(os.getenv("AI_COMMENT_ANGLES") or "")
    angles = custom_angles or list(DEFAULT_COMMENT_ANGLES)
    if experience_notes:
        angles = angles + list(EXPERIENCE_COMMENT_ANGLES)
    angle = random.choice(angles)
    opening_style = random.choice(COMMENT_OPENING_STYLES)
    detail_focus = random.choice(COMMENT_DETAIL_FOCUS)
    length_style = random.choice(COMMENT_LENGTH_STYLES)

    if experience_notes:
        experience_rule = (
            "真实体验素材："
            f"{experience_notes[:600]}。可以基于这些素材写收到实物、使用便捷、朋友/家人反馈等亲历内容。"
        )
    else:
        experience_rule = (
            "真实体验素材：未提供。禁止声称自己已经收到、购买、使用过，"
            "也不要说朋友或家人已经夸过；可以写 I'd put it... / my family would probably like... / "
            "looks / would / should / 看起来 / 我会放在... / 家里人应该会喜欢 这类偏好或推测。"
        )

    return (
        f"{base_style}\n"
        f"本次随机评论角度：{angle}\n"
        f"本次随机开头/语气：{opening_style}\n"
        f"本次随机细节焦点：{detail_focus}\n"
        f"本次随机长度/节奏：{length_style}\n"
        f"{experience_rule}\n"
        "强随机要求：每次都换句式、换开头、换场景词、换形容词组合；"
        "不要连续使用 by the door / cute colors / no fuss 这类固定组合；"
        "如果上一条可能像 I'd put this by the door，就换成 porch、front step、entryway、patio、little corner 等不同说法。"
    )


def wait_visible(driver, selector: str, timeout: int):
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, selector))
    )


def wait_first_clickable(driver, locators, timeout: int):
    def find_clickable(_driver):
        for locator in locators:
            try:
                element = _driver.find_element(*locator)
                if element.is_displayed() and element.is_enabled():
                    return element
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
        return False

    return WebDriverWait(driver, timeout).until(find_clickable)


def find_visible_comment_box(driver):
    script = """
const terms = ['comment', 'komentar', 'tulis komentar', 'write a comment', '评论'];
const blocked = ['search', 'cari', 'message', 'pesan'];
const viewportW = window.innerWidth || document.documentElement.clientWidth;
const viewportH = window.innerHeight || document.documentElement.clientHeight;
const candidates = Array.from(document.querySelectorAll('div[role="textbox"][contenteditable="true"]'))
  .map((el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const label = [
      el.getAttribute('aria-label') || '',
      el.getAttribute('aria-placeholder') || '',
      el.getAttribute('placeholder') || '',
      el.innerText || ''
    ].join(' ').toLowerCase();
    const visible = style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0;
    const inViewport = rect.bottom > 0 && rect.right > 0 && rect.top < viewportH && rect.left < viewportW;
    return {el, rect, label, visible, inViewport};
  })
  .filter((item) => {
    if (!item.visible || !item.inViewport) return false;
    const dialog = item.el.closest('[role="dialog"]');
    const dialogText = dialog ? (dialog.innerText || '').toLowerCase() : '';
    if (
      dialogText.includes('buat postingan') ||
      dialogText.includes('create post') ||
      dialogText.includes('apa yang anda pikirkan') ||
      dialogText.includes("what's on your mind")
    ) return false;
    if (item.rect.width < 140 || item.rect.height < 18) return false;
    if (blocked.some((term) => item.label.includes(term))) return false;
    return terms.some((term) => item.label.includes(term)) || item.rect.top > viewportH * 0.45;
  })
  .map((item) => {
    let score = 0;
    if (terms.some((term) => item.label.includes(term))) score += 1000;
    if (item.rect.top > viewportH * 0.45) score += 200;
    score += Math.min(item.rect.width, 800) / 10;
    score -= Math.abs(item.rect.bottom - viewportH) / 20;
    return {...item, score};
  })
  .sort((a, b) => b.score - a.score);
return candidates.length ? candidates[0].el : null;
"""
    try:
        return driver.execute_script(script)
    except Exception:
        return None


def wait_visible_comment_box(driver, timeout: int):
    return WebDriverWait(driver, timeout).until(lambda current_driver: find_visible_comment_box(current_driver))


def wait_first_present(driver, locators, timeout: int):
    def find_present(_driver):
        for locator in locators:
            try:
                elements = _driver.find_elements(*locator)
                visible = [element for element in elements if element.is_displayed()]
                if visible:
                    return visible
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
        return False

    return WebDriverWait(driver, timeout).until(find_present)


def page_has_chrome_network_error(driver) -> bool:
    try:
        body_text = driver.execute_script("return document.body ? document.body.innerText : '';") or ""
    except Exception:
        return False
    markers = (
        "ERR_NETWORK_CHANGED",
        "ERR_INTERNET_DISCONNECTED",
        "ERR_PROXY_CONNECTION_FAILED",
        "您的连接已中断",
        "检测到了网络变化",
        "无法访问此网站",
    )
    return any(marker in body_text for marker in markers)


def facebook_cookie_names(driver) -> set[str]:
    """Return Facebook cookie names visible to the whole Chrome session."""
    names: set[str] = set()
    try:
        data = driver.execute_cdp_cmd("Network.getAllCookies", {})
        for cookie in data.get("cookies", []):
            domain = str(cookie.get("domain") or "")
            name = str(cookie.get("name") or "")
            if "facebook.com" in domain and name:
                names.add(name)
    except Exception:
        pass

    try:
        for cookie in driver.get_cookies():
            domain = str(cookie.get("domain") or "")
            name = str(cookie.get("name") or "")
            if (not domain or "facebook.com" in domain) and name:
                names.add(name)
    except Exception:
        pass

    return names


def facebook_session_cookies_present(driver) -> bool:
    """Return True when the active Chrome profile has a Facebook login session."""
    names = facebook_cookie_names(driver)
    return bool({"c_user", "xs"}.issubset(names))


def visible_facebook_login_fields(driver) -> bool:
    selectors = (
        'input[name="email"]',
        'input[name="pass"]',
        'input[type="password"]',
    )
    for selector in selectors:
        try:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed():
                    return True
        except StaleElementReferenceException:
            continue
        except Exception:
            continue
    return False


def page_looks_logged_out(driver) -> bool:
    if facebook_session_cookies_present(driver):
        return False
    return visible_facebook_login_fields(driver)


def facebook_session_debug(driver) -> str:
    names = facebook_cookie_names(driver)
    important = [name for name in ("c_user", "xs", "fr", "datr", "sb") if name in names]
    try:
        current_url = driver.current_url
    except Exception:
        current_url = "<unknown>"
    login_fields = visible_facebook_login_fields(driver)
    return f"url={current_url}; facebook_cookies={important or '<none>'}; login_fields_visible={login_fields}"


def wait_until_facebook_session_saved(driver, wait_timeout: int, post_url: str = "") -> bool:
    deadline = time.time() + max(10, wait_timeout)
    last_log = 0.0
    while time.time() < deadline:
        if facebook_session_cookies_present(driver):
            print("Facebook login cookies detected. Verifying session on facebook.com...", flush=True)
            try:
                driver.get("https://www.facebook.com/")
                time.sleep(4)
            except Exception:
                pass
            print(f"Facebook session check: {facebook_session_debug(driver)}", flush=True)
            if facebook_session_cookies_present(driver) and not visible_facebook_login_fields(driver):
                print("Facebook login session detected and verified.", flush=True)
                time.sleep(3)
                if post_url:
                    driver.get(post_url)
                return True

        now = time.time()
        if now - last_log >= 10:
            print(f"Still waiting for Facebook login session... {facebook_session_debug(driver)}", flush=True)
            last_log = now
        time.sleep(2)
    return False


def ensure_logged_in_before_comment(
    driver,
    screenshot_path: str = "not_logged_in.png",
    wait_if_needed: bool = False,
    post_url: str = "",
    wait_timeout: int = 300,
) -> None:
    if facebook_session_cookies_present(driver):
        print(f"Facebook session already present: {facebook_session_debug(driver)}", flush=True)
        return

    if wait_if_needed:
        driver.save_screenshot(screenshot_path)
        print(
            "No saved Facebook login session was detected in this Chrome profile. "
            "Please log in manually in the opened Chrome window. "
            f"Waiting up to {wait_timeout} seconds before continuing...",
            flush=True,
        )
        if wait_until_facebook_session_saved(driver, wait_timeout=wait_timeout, post_url=post_url):
            return

    driver.save_screenshot(screenshot_path)
    raise RuntimeError(
        "The browser still has no saved Facebook login session, so Facebook will not show a comment box. "
        f"Saved screenshot: {screenshot_path}. Log in in this Chrome profile first, then rerun."
    )


def try_click_login_button(driver, selector: str | None, timeout: int) -> bool:
    if not selector:
        return False

    try:
        button = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
        )
        button.click()
        return True
    except TimeoutException:
        return False


def cleanup_stale_chrome_profile_locks(profile_path: Path) -> None:
    lock_paths = [
        profile_path / "SingletonLock",
        profile_path / "SingletonSocket",
        profile_path / "SingletonCookie",
    ]
    existing_locks = [path for path in lock_paths if path.exists() or path.is_symlink()]
    if not existing_locks:
        return

    socket_path = profile_path / "SingletonSocket"
    socket_target_missing = socket_path.is_symlink() and not socket_path.exists()
    if not socket_target_missing:
        return

    for path in existing_locks:
        try:
            path.unlink()
        except OSError:
            pass
    print(f"Removed stale Chrome profile lock files from {profile_path}.", flush=True)


def chrome_proxy_settings() -> dict:
    raw_server = os.getenv("CHROME_PROXY_SERVER", "").strip()
    host = os.getenv("CHROME_PROXY_HOST", "").strip()
    port = os.getenv("CHROME_PROXY_PORT", "").strip()
    scheme = os.getenv("CHROME_PROXY_SCHEME", "http").strip().lower() or "http"
    username = os.getenv("CHROME_PROXY_USERNAME", "").strip()
    password = os.getenv("CHROME_PROXY_PASSWORD", "")
    bypass = os.getenv("CHROME_PROXY_BYPASS", "localhost,127.0.0.1,::1").strip()

    if raw_server:
        parsed = urlparse(raw_server if "://" in raw_server else f"{scheme}://{raw_server}")
        scheme = parsed.scheme or scheme
        host = host or (parsed.hostname or "")
        port = port or (str(parsed.port) if parsed.port else "")
        if not username and parsed.username:
            username = unquote(parsed.username)
        if not password and parsed.password:
            password = unquote(parsed.password)

    return {
        "scheme": scheme,
        "host": host,
        "port": int(port) if str(port).isdigit() else 0,
        "username": username,
        "password": password,
        "bypass": bypass,
    }


def write_chrome_proxy_auth_extension(proxy: dict, extension_dir: Path) -> Path:
    bypass_list = [item.strip() for item in str(proxy.get("bypass") or "").split(",") if item.strip()]
    background_js = f"""
const config = {{
  mode: "fixed_servers",
  rules: {{
    singleProxy: {{
      scheme: {json.dumps(proxy["scheme"])},
      host: {json.dumps(proxy["host"])},
      port: {int(proxy["port"])},
    }},
    bypassList: {json.dumps(bypass_list)},
  }},
}};

chrome.proxy.settings.set({{value: config, scope: "regular"}});

chrome.webRequest.onAuthRequired.addListener(
  function(details) {{
    return {{
      authCredentials: {{
        username: {json.dumps(proxy["username"])},
        password: {json.dumps(proxy["password"])},
      }},
    }};
  }},
  {{urls: ["<all_urls>"]}},
  ["blocking"]
);
"""
    manifest = {
        "manifest_version": 2,
        "name": "MetaFlow Proxy Auth",
        "version": "1.0.0",
        "permissions": [
            "proxy",
            "webRequest",
            "webRequestBlocking",
            "<all_urls>",
        ],
        "background": {"scripts": ["background.js"]},
    }
    extension_dir.mkdir(parents=True, exist_ok=True)
    (extension_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (extension_dir / "background.js").write_text(background_js, encoding="utf-8")
    return extension_dir


def create_chrome_proxy_auth_extension(proxy: dict) -> Path:
    extension_dir = Path(tempfile.mkdtemp(prefix="metaflow_proxy_auth_"))
    return write_chrome_proxy_auth_extension(proxy, extension_dir)


def chrome_proxy_command_args() -> list[str]:
    proxy = chrome_proxy_settings()
    if not proxy["host"] or not proxy["port"]:
        return []

    proxy_server = f'{proxy["scheme"]}://{proxy["host"]}:{proxy["port"]}'
    args = [f"--proxy-server={proxy_server}"]
    if proxy["bypass"]:
        args.append(f"--proxy-bypass-list={proxy['bypass']}")

    if proxy["username"] or proxy["password"]:
        extension_path = create_chrome_proxy_auth_extension(proxy)
        args.append(f"--load-extension={extension_path}")
    return args


def configure_chrome_proxy(options: Options) -> None:
    proxy = chrome_proxy_settings()
    if not proxy["host"] or not proxy["port"]:
        return

    proxy_server = f'{proxy["scheme"]}://{proxy["host"]}:{proxy["port"]}'
    options.add_argument(f"--proxy-server={proxy_server}")
    if proxy["bypass"]:
        options.add_argument(f"--proxy-bypass-list={proxy['bypass']}")

    if proxy["username"] or proxy["password"]:
        extension_path = create_chrome_proxy_auth_extension(proxy)
        options.add_argument(f"--load-extension={extension_path}")
        print(f"Chrome proxy enabled with auth: {proxy['scheme']}://{proxy['host']}:{proxy['port']}", flush=True)
    else:
        print(f"Chrome proxy enabled: {proxy_server}", flush=True)


def build_driver(profile_dir: str | None, headless: bool) -> webdriver.Chrome:
    options = Options()
    chrome_binary = os.getenv("CHROME_BINARY", "").strip()
    if chrome_binary:
        options.binary_location = chrome_binary

    options.add_argument("--start-maximized")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    for arg in parse_comment_items(os.getenv("CHROME_EXTRA_ARGS", "")):
        options.add_argument(arg)
    configure_chrome_proxy(options)

    debugger_address = os.getenv("CHROME_DEBUGGER_ADDRESS", "").strip()
    if not debugger_address and profile_dir and env_bool("CHROME_ATTACH_EXISTING", False):
        devtools_path = Path(profile_dir).expanduser().resolve() / "DevToolsActivePort"
        if devtools_path.exists():
            try:
                port = devtools_path.read_text(encoding="utf-8").splitlines()[0].strip()
            except (IndexError, OSError):
                port = ""
            if port:
                debugger_address = f"127.0.0.1:{port}"

    if debugger_address:
        print(f"Attaching to existing Chrome at {debugger_address}...", flush=True)
        options.add_experimental_option("debuggerAddress", debugger_address)
        return webdriver.Chrome(options=options)

    options.add_argument("--remote-debugging-port=0")

    if profile_dir:
        profile_path = Path(profile_dir).expanduser().resolve()
        profile_path.mkdir(parents=True, exist_ok=True)
        cleanup_stale_chrome_profile_locks(profile_path)
        print(f"Using Chrome profile directory: {profile_path}", flush=True)
        options.add_argument(f"--user-data-dir={profile_path}")

    if headless:
        options.add_argument("--headless=new")

    try:
        return webdriver.Chrome(options=options)
    except SessionNotCreatedException as exc:
        hint = (
            "\nChrome failed to start. If CHROME_PROFILE_DIR is set, close every Chrome "
            "window opened by this script before running again, or change CHROME_PROFILE_DIR "
            "to a new folder such as ./chrome-profile-2."
        )
        raise RuntimeError(hint) from exc


def wait_for_manual_login(driver, login_url: str, wait_timeout: int = 300) -> None:
    print(f"Opening login page for manual login: {login_url}", flush=True)
    driver.get(login_url)
    time.sleep(2)
    if facebook_session_cookies_present(driver):
        print(f"This Chrome profile already has a saved Facebook login session: {facebook_session_debug(driver)}", flush=True)
        return

    print(
        "Please finish Facebook login/verification in the opened Chrome window. "
        f"Waiting up to {wait_timeout} seconds...",
        flush=True,
    )
    if wait_until_facebook_session_saved(driver, wait_timeout=wait_timeout):
        return

    screenshot_path = "manual_login_timeout.png"
    driver.save_screenshot(screenshot_path)
    raise RuntimeError(f"Manual login was not detected before timeout. Saved screenshot: {screenshot_path}")


def clean_post_text(text: str, max_chars: int = 5000) -> str:
    ignored_lines = {
        "like",
        "comment",
        "share",
        "send",
        "all reactions:",
        "赞",
        "评论",
        "分享",
        "发送",
    }
    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        if line.strip().lower() in ignored_lines:
            continue
        lines.append(line)

    cleaned = "\n".join(dict.fromkeys(lines))
    return cleaned[:max_chars]


def extract_post_content(driver, post_url: str, timeout: int) -> str:
    print(f"Opening post page: {post_url}", flush=True)
    driver.get(post_url)

    try:
        print("Waiting for post content...", flush=True)
        elements = wait_first_present(driver, DEFAULT_POST_CONTENT_LOCATORS, timeout)
    except TimeoutException:
        elements = []

    if elements:
        text_candidates = []
        for element in elements:
            try:
                text = clean_post_text(element.text)
                if text:
                    text_candidates.append(text)
            except StaleElementReferenceException:
                continue
        if text_candidates:
            content = max(text_candidates, key=len)
            print("Post content extracted from page article.", flush=True)
            return content

    body_text = driver.execute_script("return document.body ? document.body.innerText : '';") or ""
    content = clean_post_text(body_text)
    if content:
        print("Post content extracted from page body fallback.", flush=True)
        return content

    screenshot_path = "post_content_not_found.png"
    driver.save_screenshot(screenshot_path)
    print(f"Could not extract post content. Saved screenshot: {screenshot_path}", flush=True)
    return ""


def normalize_external_url(href: str, base_url: str) -> str:
    if not href:
        return ""
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return ""

    host = parsed.netloc.lower().split(":")[0]
    if host in {"l.facebook.com", "lm.facebook.com"} and parsed.path.startswith("/l.php"):
        target = parse_qs(parsed.query).get("u", [""])[0]
        if target:
            return unquote(target)
    return absolute


def is_probable_product_link(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    if not host or host in FACEBOOK_HOSTS or host in NON_PRODUCT_HOSTS:
        return False
    if host.endswith(".facebook.com") or host.endswith(".fbcdn.net"):
        return False
    path = parsed.path.lower()
    product_markers = ("/products/", "/product/", "/collections/", "/item/", "/shop/", "/pages/")
    if any(marker in path for marker in product_markers):
        return True
    return bool(path and path not in {"/", ""})


def extract_product_links(driver, post_url: str) -> list[str]:
    links = []
    seen = set()
    for anchor in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
        try:
            url = normalize_external_url(anchor.get_attribute("href") or "", post_url)
        except StaleElementReferenceException:
            continue
        if not url or url in seen or not is_probable_product_link(url):
            continue
        seen.add(url)
        links.append(url)
    return links


def extract_product_context(driver, product_url: str, timeout: int) -> str:
    print(f"Opening product page: {product_url}", flush=True)
    driver.get(product_url)

    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    except TimeoutException:
        pass

    data = driver.execute_script(
        """
        const pick = (selector, attr) => {
          const node = document.querySelector(selector);
          return node ? (attr ? node.getAttribute(attr) : node.innerText) : "";
        };
        const title = document.title || "";
        const h1 = pick("h1", null);
        const description =
          pick('meta[property="og:description"]', "content") ||
          pick('meta[name="description"]', "content");
        const ogImage = pick('meta[property="og:image"]', "content");
        const body = document.body ? document.body.innerText : "";
        const images = Array.from(document.querySelectorAll("img"))
          .map((img) => img.currentSrc || img.src || img.getAttribute("data-src") || "")
          .filter(Boolean)
          .slice(0, 8);
        return {title, h1, description, ogImage, body, images};
        """
    )
    if not isinstance(data, dict):
        data = {}

    parts = [
        f"Product URL: {product_url}",
        f"Title: {data.get('title') or ''}",
        f"H1: {data.get('h1') or ''}",
        f"Description: {data.get('description') or ''}",
    ]
    image_urls = [url for url in [data.get("ogImage"), *(data.get("images") or [])] if isinstance(url, str) and url]
    if image_urls:
        parts.append("Image references: " + ", ".join(dict.fromkeys(image_urls[:6])))

    body = clean_post_text(str(data.get("body") or ""), max_chars=5000)
    if body:
        parts.append("Visible product page text:\n" + body)
    return "\n".join(part for part in parts if part.strip())


def collect_post_and_product_context(
    driver,
    post_url: str,
    timeout: int,
    product_url: str | None = None,
    wait_login_if_needed: bool = False,
    login_wait_timeout: int = 300,
) -> tuple[str, str, str]:
    post_content = extract_post_content(driver, post_url, timeout)
    product_links = [product_url] if product_url else extract_product_links(driver, post_url)
    ensure_logged_in_before_comment(
        driver,
        wait_if_needed=wait_login_if_needed,
        post_url=post_url,
        wait_timeout=login_wait_timeout,
    )

    print("\nExtracted post content preview:")
    print("-" * 40)
    print(post_content[:1200] or "<empty>")
    print("-" * 40)

    if product_links:
        print("Product link selected:", product_links[0], flush=True)
    else:
        print("No external product link found in the post.", flush=True)
        return post_content, "", ""

    product_context = extract_product_context(driver, product_links[0], timeout)
    print("\nExtracted product context preview:")
    print("-" * 40)
    print(product_context[:1200] or "<empty>")
    print("-" * 40)
    return post_content, product_context, product_links[0]


def return_to_post(driver, post_url: str) -> None:
    if not post_url:
        return
    try:
        print("Returning browser to Facebook post page...", flush=True)
        driver.get(post_url)
    except Exception as exc:
        print(f"Could not return to post page: {exc}", flush=True)


def find_file_input(driver):
    inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
    if not inputs:
        return None
    image_inputs = []
    for file_input in inputs:
        accept = (file_input.get_attribute("accept") or "").lower()
        if "image" in accept or not accept:
            image_inputs.append(file_input)
    return image_inputs[0] if image_inputs else inputs[0]


def known_file_input_ids(driver) -> set[str]:
    script = """
window.__metaflowFileInputSeq = window.__metaflowFileInputSeq || 1;
return Array.from(document.querySelectorAll('input[type="file"]')).map((el) => {
  if (!el.dataset.metaflowFileInputId) {
    el.dataset.metaflowFileInputId = String(window.__metaflowFileInputSeq++);
  }
  return el.dataset.metaflowFileInputId;
});
"""
    try:
        return {str(value) for value in (driver.execute_script(script) or [])}
    except Exception:
        return set()


def find_file_input_by_ids(driver, known_ids: set[str] | None = None):
    script = """
const known = new Set(arguments[0] || []);
window.__metaflowFileInputSeq = window.__metaflowFileInputSeq || 1;
const inputs = Array.from(document.querySelectorAll('input[type="file"]')).map((el) => {
  if (!el.dataset.metaflowFileInputId) {
    el.dataset.metaflowFileInputId = String(window.__metaflowFileInputSeq++);
  }
  const accept = (el.getAttribute('accept') || '').toLowerCase();
  const rect = el.getBoundingClientRect();
  return {
    el,
    id: el.dataset.metaflowFileInputId,
    image: !accept || accept.includes('image'),
    fresh: !known.has(el.dataset.metaflowFileInputId),
    visible: rect.width > 0 && rect.height > 0
  };
});
const imageInputs = inputs.filter((item) => item.image);
const fresh = imageInputs.filter((item) => item.fresh);
if (fresh.length) return fresh[fresh.length - 1].el;
const visible = imageInputs.filter((item) => item.visible);
if (visible.length) return visible[visible.length - 1].el;
if (imageInputs.length) return imageInputs[imageInputs.length - 1].el;
return null;
"""
    try:
        return driver.execute_script(script, list(known_ids or set()))
    except Exception:
        return None


def find_file_inputs_by_ids(driver, known_ids: set[str] | None = None, comment_box=None) -> list:
    script = """
const known = new Set(arguments[0] || []);
const box = arguments[1] || null;
const boxRect = box ? box.getBoundingClientRect() : null;
const commentForm = box ? box.closest('form') : null;
window.__metaflowFileInputSeq = window.__metaflowFileInputSeq || 1;
const items = Array.from(document.querySelectorAll('input[type="file"]')).map((el, index) => {
  if (!el.dataset.metaflowFileInputId) {
    el.dataset.metaflowFileInputId = String(window.__metaflowFileInputSeq++);
  }
  const accept = (el.getAttribute('accept') || '').toLowerCase();
  const rect = el.getBoundingClientRect();
  const inCommentForm = Boolean(commentForm && commentForm.contains(el));
  let ancestor = el.parentElement;
  let ancestorText = '';
  let nearComment = false;
  for (let i = 0; i < 8 && ancestor; i++, ancestor = ancestor.parentElement) {
    ancestorText += ' ' + (ancestor.innerText || '').slice(0, 300).toLowerCase();
    if (boxRect) {
      const ar = ancestor.getBoundingClientRect();
      if (
        ar.width > 0 && ar.height > 0 &&
        ar.bottom >= boxRect.top - 80 &&
        ar.top <= boxRect.bottom + 160 &&
        ar.right >= boxRect.left - 120 &&
        ar.left <= boxRect.right + 180
      ) nearComment = true;
    }
  }
  const createPost = ancestorText.includes('buat postingan') || ancestorText.includes('create post') ||
    ancestorText.includes('apa yang anda pikirkan') || ancestorText.includes("what's on your mind");
  return {
    el,
    index,
    id: el.dataset.metaflowFileInputId,
    image: !accept || accept.includes('image'),
    fresh: !known.has(el.dataset.metaflowFileInputId),
    visible: rect.width > 0 && rect.height > 0,
    accept,
    inCommentForm,
    nearComment,
    createPost
  };
}).filter((item) => item.image);
const scoped = items.filter((item) => (item.inCommentForm || item.nearComment) && !item.createPost);
const sortable = scoped.length ? scoped : items.filter((item) => !item.createPost);
if (!sortable.length) return [];
sortable.sort((a, b) => {
  if (a.inCommentForm !== b.inCommentForm) return a.inCommentForm ? -1 : 1;
  if (a.nearComment !== b.nearComment) return a.nearComment ? -1 : 1;
  if (a.fresh !== b.fresh) return a.fresh ? -1 : 1;
  if (a.visible !== b.visible) return a.visible ? -1 : 1;
  const aImageFirst = a.accept.trim().startsWith('image') ? 0 : 1;
  const bImageFirst = b.accept.trim().startsWith('image') ? 0 : 1;
  if (aImageFirst !== bImageFirst) return aImageFirst - bImageFirst;
  return b.index - a.index;
});
return sortable.map((item) => item.el);
"""
    try:
        return list(driver.execute_script(script, list(known_ids or set()), comment_box) or [])
    except Exception:
        return []


def find_comment_photo_buttons(driver, comment_box) -> list:
    script = """
const box = arguments[0];
const boxRect = box.getBoundingClientRect();
const allowed = ['photo', 'foto', 'gambar', 'image', 'camera', 'kamera', 'attach', 'lampir', '照片', '图片', '相机'];
const blocked = ['gif', 'emoji', 'sticker', 'stiker', 'avatar', 'profile', 'send', 'kirim'];
const viewportW = window.innerWidth || document.documentElement.clientWidth;
const viewportH = window.innerHeight || document.documentElement.clientHeight;
const items = Array.from(document.querySelectorAll('[role="button"], button, [aria-label]'))
  .map((el) => {
    const rect = el.getBoundingClientRect();
    const label = [
      el.getAttribute('aria-label') || '',
      el.getAttribute('title') || '',
      el.innerText || '',
      Array.from(el.querySelectorAll('[aria-label]')).map((child) => child.getAttribute('aria-label') || '').join(' ')
    ].join(' ').toLowerCase().trim();
    const style = window.getComputedStyle(el);
    const disabled = el.getAttribute('aria-disabled') === 'true' || el.disabled;
    const hasMediaGlyph = Boolean(el.querySelector('svg, img, i, [style*="mask"], [style*="background"]'));
    return {el, rect, label, style, disabled, hasMediaGlyph};
  })
  .filter((item) => {
    if (item.disabled || item.style.visibility === 'hidden' || item.style.display === 'none') return false;
    if (Number(item.style.opacity || '1') <= 0) return false;
    if (item.rect.bottom <= 0 || item.rect.right <= 0 || item.rect.top >= viewportH || item.rect.left >= viewportW) return false;
    if (item.rect.width < 10 || item.rect.height < 10 || item.rect.width > 120 || item.rect.height > 120) return false;
    if (blocked.some((term) => item.label.includes(term))) return false;
    const hasAllowedLabel = allowed.some((term) => item.label.includes(term));
    const nearY = item.rect.top >= boxRect.top - 35 && item.rect.bottom <= boxRect.bottom + 140;
    const nearX = item.rect.left >= boxRect.left - 80 && item.rect.left <= boxRect.right + 120;
    if (!nearY || !nearX) return false;
    if (hasAllowedLabel) return true;
    // Some Facebook locales render the comment photo icon without an aria-label.
    const toolbarFallback = item.hasMediaGlyph &&
      item.rect.top >= boxRect.top - 5 &&
      item.rect.left <= boxRect.left + 240 &&
      item.rect.width <= 56 &&
      item.rect.height <= 56;
    return toolbarFallback;
  })
  .sort((a, b) => {
    const aAllowed = allowed.some((term) => a.label.includes(term)) ? 0 : 1;
    const bAllowed = allowed.some((term) => b.label.includes(term)) ? 0 : 1;
    if (aAllowed !== bAllowed) return aAllowed - bAllowed;
    const aDistance = Math.abs(a.rect.top - boxRect.bottom) + Math.abs(a.rect.left - boxRect.left);
    const bDistance = Math.abs(b.rect.top - boxRect.bottom) + Math.abs(b.rect.left - boxRect.left);
    return aDistance - bDistance;
  });
return items.map((item) => item.el);
"""
    try:
        return list(driver.execute_script(script, comment_box) or [])
    except Exception:
        return []


def find_comment_photo_button(driver, comment_box):
    buttons = find_comment_photo_buttons(driver, comment_box)
    return buttons[0] if buttons else None


def close_unrelated_post_dialog(driver) -> bool:
    script = """
const clickVisibleButton = (dialog, matcher) => {
  const dialogRect = dialog.getBoundingClientRect();
  const buttons = Array.from(dialog.querySelectorAll('[role="button"], button'))
    .map((el) => ({el, rect: el.getBoundingClientRect(), label: [
      el.getAttribute('aria-label') || '',
      el.getAttribute('title') || '',
      el.innerText || ''
    ].join(' ').toLowerCase().trim()}))
    .filter((item) => item.rect.width > 10 && item.rect.height > 10);
  const match = buttons.find((item) => matcher(item, dialogRect));
  if (match) {
    match.el.click();
    return true;
  }
  return false;
};

const draftDialogs = Array.from(document.querySelectorAll('[role="dialog"]')).filter((dialog) => {
  const rect = dialog.getBoundingClientRect();
  const style = window.getComputedStyle(dialog);
  if (rect.width <= 0 || rect.height <= 0 || style.display === 'none' || style.visibility === 'hidden') return false;
  const text = (dialog.innerText || '').toLowerCase();
  return (
    text.includes('simpan postingan ini sebagai draf') ||
    text.includes('save this post as a draft') ||
    text.includes('hapus draf') ||
    text.includes('discard draft')
  );
});
for (const dialog of draftDialogs) {
  if (clickVisibleButton(dialog, (item) =>
    item.label.includes('hapus draf') ||
    item.label.includes('discard draft') ||
    item.label.includes('delete draft')
  )) return 'discarded';
}

const dialogs = Array.from(document.querySelectorAll('[role="dialog"]')).filter((dialog) => {
  const rect = dialog.getBoundingClientRect();
  const style = window.getComputedStyle(dialog);
  if (rect.width <= 0 || rect.height <= 0 || style.display === 'none' || style.visibility === 'hidden') return false;
  const text = (dialog.innerText || '').toLowerCase();
  return (
    (text.includes('buat postingan') || text.includes('create post')) &&
    (text.includes('apa yang anda pikirkan') || text.includes("what's on your mind") || text.includes('tambahkan ke postingan'))
  );
});
for (const dialog of dialogs) {
  if (clickVisibleButton(dialog, (item, dialogRect) =>
    item.label.includes('close') ||
    item.label.includes('tutup') ||
    item.label.includes('关闭') ||
    item.label.trim() === 'x' ||
    (item.rect.top < dialogRect.top + 90 && item.rect.left > dialogRect.right - 90)
  )) return 'closed';
}
return '';
"""
    try:
        result = driver.execute_script(script)
        closed = bool(result)
        if closed:
            if result == "discarded":
                print("Discarded unrelated create-post draft dialog.", flush=True)
            else:
                print("Closed unrelated create-post dialog after wrong image input.", flush=True)
            time.sleep(1)
        return closed
    except Exception:
        return False


def page_has_unrelated_post_dialog(driver) -> bool:
    script = """
return Array.from(document.querySelectorAll('[role="dialog"]')).some((dialog) => {
  const rect = dialog.getBoundingClientRect();
  const style = window.getComputedStyle(dialog);
  if (rect.width <= 0 || rect.height <= 0 || style.display === 'none' || style.visibility === 'hidden') return false;
  const text = (dialog.innerText || '').toLowerCase();
  return (
    (text.includes('buat postingan') || text.includes('create post')) &&
    (text.includes('apa yang anda pikirkan') || text.includes("what's on your mind") || text.includes('tambahkan ke postingan'))
  );
});
"""
    try:
        return bool(driver.execute_script(script))
    except Exception:
        return False


def click_comment_photo_button(driver, comment_box=None) -> bool:
    if comment_box is not None:
        button = find_comment_photo_button(driver, comment_box)
        if button is not None:
            try:
                button.click()
                return True
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", button)
                    return True
                except Exception:
                    pass
        return False

    button_xpaths = [
        "//*[@role='button' and (contains(@aria-label,'照片') or contains(@aria-label,'图片') or contains(@aria-label,'Photo') or contains(@aria-label,'photo') or contains(@aria-label,'Image') or contains(@aria-label,'image') or contains(@aria-label,'Foto') or contains(@aria-label,'foto') or contains(@aria-label,'Gambar') or contains(@aria-label,'gambar') or contains(@aria-label,'Kamera') or contains(@aria-label,'kamera'))]",
        "//*[@aria-label and (contains(@aria-label,'照片') or contains(@aria-label,'图片') or contains(@aria-label,'Photo') or contains(@aria-label,'photo') or contains(@aria-label,'Image') or contains(@aria-label,'image') or contains(@aria-label,'Foto') or contains(@aria-label,'foto') or contains(@aria-label,'Gambar') or contains(@aria-label,'gambar') or contains(@aria-label,'Kamera') or contains(@aria-label,'kamera'))]",
    ]
    for xpath in button_xpaths:
        for button in driver.find_elements(By.XPATH, xpath):
            try:
                if button.is_displayed() and button.is_enabled():
                    button.click()
                    return True
            except Exception:
                continue
    return False


def applescript_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def copy_image_to_clipboard(image_path: Path) -> bool:
    if sys.platform.startswith("linux"):
        process = start_x11_image_clipboard_provider(image_path)
        if process is None:
            return False
        cleanup_clipboard_provider(process)
        return True

    if sys.platform != "darwin":
        print("Clipboard image paste is currently implemented for macOS and Linux/X11 only.", flush=True)
        return False

    left_guillemet = chr(0x00AB)
    right_guillemet = chr(0x00BB)
    suffix = image_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        picture_type = "JPEG picture"
    elif suffix in {".tif", ".tiff"}:
        picture_type = "TIFF picture"
    else:
        picture_type = f"{left_guillemet}class PNGf{right_guillemet}"

    script = f'set the clipboard to (read (POSIX file "{applescript_quote(str(image_path))}") as {picture_type})'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Could not copy image to clipboard: {exc}", flush=True)
        return False

    if result.returncode != 0:
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        print(f"Could not copy image to clipboard: {output[:800]}", flush=True)
        return False
    return True


def image_png_bytes_for_clipboard(image_path: Path) -> bytes | None:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        print(f"Clipboard image not found: {path}", flush=True)
        return None

    try:
        from PIL import Image
    except ImportError:
        if path.suffix.lower() == ".png":
            return path.read_bytes()
        print("Pillow is required to copy non-PNG images to the Linux clipboard.", flush=True)
        return None

    max_side = env_int("COMMENT_CLIPBOARD_IMAGE_MAX_SIDE", 768, 256, 2048)
    try:
        with Image.open(path) as image:
            image.load()
            original_size = image.size
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA")
            if max(image.size) > max_side:
                image.thumbnail((max_side, max_side))
            output = BytesIO()
            image.save(output, format="PNG", optimize=True, compress_level=6)
            data = output.getvalue()
            print(
                "Prepared image for X11 clipboard: "
                f"{path.name} {original_size[0]}x{original_size[1]} -> {image.size[0]}x{image.size[1]} "
                f"({len(data)} bytes PNG)",
                flush=True,
            )
            return data
    except Exception as exc:
        print(f"Could not prepare image for clipboard: {exc}", flush=True)
        return None


def start_x11_image_clipboard_provider(image_path: Path) -> subprocess.Popen | None:
    content = image_png_bytes_for_clipboard(image_path)
    if not content:
        return None
    try:
        process = subprocess.Popen(
            ["xclip", "-selection", "clipboard", "-target", "image/png", "-in", "-loops", "50", "-quiet"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        print(f"Could not start X11 image clipboard provider: {exc}", flush=True)
        return None

    try:
        assert process.stdin is not None
        process.stdin.write(content)
        process.stdin.close()
    except OSError as exc:
        cleanup_clipboard_provider(process)
        print(f"Could not write image to X11 clipboard provider: {exc}", flush=True)
        return None

    time.sleep(0.2)
    if process.poll() is not None:
        stderr = ""
        try:
            stderr = (process.stderr.read() if process.stderr else b"").decode("utf-8", errors="replace").strip()
        except OSError:
            stderr = ""
        print(f"X11 image clipboard provider exited before paste: {stderr[:800]}", flush=True)
        return None
    print(f"Copied image to X11 clipboard: {Path(image_path).name} ({len(content)} bytes PNG)", flush=True)
    return process


def prepare_comment_upload_image(image_path: Path) -> Path:
    path = Path(image_path).expanduser().resolve()
    try:
        from PIL import Image
    except ImportError:
        return path

    output_dir = Path(tempfile.gettempdir()) / "metaflow_comment_uploads"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{path.stem}.jpg"
    try:
        with Image.open(path) as image:
            image.load()
            if image.mode in {"RGBA", "LA"}:
                background = Image.new("RGB", image.size, (255, 255, 255))
                alpha = image.getchannel("A") if image.mode == "RGBA" else image.getchannel("A")
                background.paste(image.convert("RGBA"), mask=alpha)
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            max_dimension = env_int("COMMENT_UPLOAD_MAX_DIMENSION", 600, 320, 1600)
            if max(image.size) > max_dimension:
                image.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
            # Facebook comments can silently ignore some generated 1024px images.
            # A small baseline JPEG is more reliable for comment attachments.
            image.save(output_path, format="JPEG", quality=85, optimize=False, progressive=False)
        if output_path != path:
            print(f"Prepared upload-safe comment image: {path.name} -> {output_path.name}", flush=True)
        return output_path
    except Exception as exc:
        print(f"Could not prepare upload-safe comment image, using original: {exc}", flush=True)
        return path


def comment_image_attachment_state(driver, comment_box) -> dict:
    script = """
const box = arguments[0];
const boxRect = box.getBoundingClientRect();
let root = box;
let best = box.parentElement || box;
for (let i = 0; i < 12 && root.parentElement; i++) {
  root = root.parentElement;
  const rect = root.getBoundingClientRect();
  if (rect.width >= boxRect.width && rect.height >= boxRect.height && rect.height <= 1000) {
    best = root;
  }
  if (rect.height > 1200) break;
}
root = best;

const isVisible = (el) => {
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  return rect.width >= 48 && rect.height >= 48 &&
    style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0;
};
const isNearComposer = (el) => {
  const rect = el.getBoundingClientRect();
  const vertical = rect.bottom >= boxRect.top - 260 && rect.top <= boxRect.bottom + 320;
  const horizontal = rect.right >= boxRect.left - 120 && rect.left <= boxRect.right + 220;
  return vertical && horizontal;
};
const isProbablyAvatar = (el) => {
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  const label = [
    el.getAttribute('alt') || '',
    el.getAttribute('aria-label') || '',
    el.getAttribute('title') || '',
    el.closest('[aria-label]')?.getAttribute('aria-label') || ''
  ].join(' ').toLowerCase();
  const radius = parseFloat(style.borderTopLeftRadius || '0') || 0;
  const circular = radius >= Math.min(rect.width, rect.height) * 0.45;
  const squareSmall = Math.abs(rect.width - rect.height) <= 4 && rect.width <= 72;
  if (label.includes('profile') || label.includes('avatar') || label.includes('个人资料') || label.includes('头像')) return true;
  return circular && squareSmall && rect.left < boxRect.left + 30;
};
const candidates = [];
for (const img of Array.from(root.querySelectorAll('img'))) {
  const src = (img.getAttribute('src') || '').toLowerCase();
  if (!isVisible(img) || !isNearComposer(img) || isProbablyAvatar(img)) continue;
  if (src.includes('/emoji') || src.includes('emoji.php') || src.includes('static.xx.fbcdn.net/images/emoji')) continue;
  candidates.push(img);
}
for (const el of Array.from(root.querySelectorAll('[style*="background-image"]'))) {
  const style = window.getComputedStyle(el);
  if (!style.backgroundImage || style.backgroundImage === 'none') continue;
  if (!isVisible(el) || !isNearComposer(el) || isProbablyAvatar(el)) continue;
  candidates.push(el);
}
const busyNodes = Array.from(root.querySelectorAll('[role="progressbar"], [aria-busy="true"]'));
const rootText = (root.innerText || '').toLowerCase();
const busyText = [
  'uploading', 'processing', 'attaching', 'finishing',
  '正在上传', '上传中', '处理中', '正在处理'
].some((term) => rootText.includes(term));
const busy = busyText || busyNodes.some((el) => {
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
});
return {
  previewCount: candidates.length,
  busy,
  rootText: (root.innerText || '').slice(0, 300)
};
"""
    try:
        state = driver.execute_script(script, comment_box) or {}
        return {
            "previewCount": int(state.get("previewCount") or 0),
            "busy": bool(state.get("busy")),
            "rootText": str(state.get("rootText") or ""),
        }
    except StaleElementReferenceException:
        try:
            fresh_box = wait_visible_comment_box(driver, 5)
            return comment_image_attachment_state(driver, fresh_box)
        except Exception:
            return {"previewCount": 0, "busy": False, "rootText": ""}
    except Exception:
        return {"previewCount": 0, "busy": False, "rootText": ""}


def wait_for_comment_image_ready(
    driver,
    comment_box,
    baseline_count: int = 0,
    timeout: int | None = None,
    require_new_preview: bool = True,
) -> bool:
    wait_seconds = timeout
    if wait_seconds is None:
        wait_seconds = env_int("COMMENT_IMAGE_READY_WAIT_SECONDS", 35, 5, 120)
    stable_required = env_int("COMMENT_IMAGE_READY_STABLE_CHECKS", 3, 1, 8)
    deadline = time.time() + wait_seconds
    stable_count = 0
    last_state = {"previewCount": 0, "busy": False, "rootText": ""}
    while time.time() <= deadline:
        last_state = comment_image_attachment_state(driver, comment_box)
        preview_count = int(last_state.get("previewCount") or 0)
        has_preview = preview_count > baseline_count if require_new_preview else preview_count > 0
        if has_preview and not last_state.get("busy"):
            stable_count += 1
            if stable_count >= stable_required:
                print(
                    "Comment image preview is ready: "
                    f"previews={preview_count} baseline={baseline_count} stable_checks={stable_count}",
                    flush=True,
                )
                return True
        else:
            stable_count = 0
        time.sleep(1)

    print(
        "Timed out waiting for comment image preview/upload readiness: "
        f"last_state={last_state} baseline={baseline_count} require_new_preview={require_new_preview}",
        flush=True,
    )
    return False


def paste_image_from_clipboard(driver, comment_box, image_path: Path, timeout: int) -> bool:
    baseline_count = comment_image_attachment_state(driver, comment_box).get("previewCount", 0)
    clipboard_process = None
    if sys.platform.startswith("linux"):
        clipboard_process = start_x11_image_clipboard_provider(image_path)
        if clipboard_process is None:
            return False
    elif not copy_image_to_clipboard(image_path):
        return False

    pasted = False
    try:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", comment_box)
            comment_box.click()
        except Exception:
            focus_comment_box(driver, comment_box)
        if sys.platform.startswith("linux"):
            click_element_with_xdotool(driver, comment_box)
            try:
                result = subprocess.run(
                    ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    print("Image paste Ctrl+V sent with xdotool.", flush=True)
                else:
                    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
                    print(f"xdotool image paste failed, trying CDP Ctrl+V: {output[:500]}", flush=True)
                    raise RuntimeError("xdotool paste failed")
            except Exception as xdotool_exc:
                print(f"Could not paste image with xdotool, trying CDP Ctrl+V: {xdotool_exc}", flush=True)
                driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                    "type": "keyDown",
                    "key": "Control",
                    "code": "ControlLeft",
                    "windowsVirtualKeyCode": 17,
                    "nativeVirtualKeyCode": 17,
                    "modifiers": 2,
                })
                driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                    "type": "keyDown",
                    "key": "v",
                    "code": "KeyV",
                    "windowsVirtualKeyCode": 86,
                    "nativeVirtualKeyCode": 86,
                    "modifiers": 2,
                })
                driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                    "type": "keyUp",
                    "key": "v",
                    "code": "KeyV",
                    "windowsVirtualKeyCode": 86,
                    "nativeVirtualKeyCode": 86,
                    "modifiers": 2,
                })
                driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                    "type": "keyUp",
                    "key": "Control",
                    "code": "ControlLeft",
                    "windowsVirtualKeyCode": 17,
                    "nativeVirtualKeyCode": 17,
                })
        else:
            modifier = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
            ActionChains(driver).key_down(modifier).send_keys("v").key_up(modifier).perform()
        pasted = True
    except Exception as exc:
        print(f"Could not paste image from clipboard: {exc}", flush=True)
        return False
    finally:
        # If paste failed before Ctrl+V, stop the clipboard provider now. On a
        # successful paste, keep it alive through the preview wait because Chrome
        # can request image bytes asynchronously after probing clipboard targets.
        if clipboard_process is not None and not pasted:
            time.sleep(3)
            cleanup_clipboard_provider(clipboard_process)

    try:
        if wait_for_comment_image_ready(driver, comment_box, baseline_count=baseline_count):
            print("Image pasted from clipboard and preview/upload is ready.", flush=True)
            return True
        screenshot_path = "comment_image_paste_not_ready.png"
        driver.save_screenshot(screenshot_path)
        print(f"Image paste did not produce a ready preview. Saved screenshot: {screenshot_path}", flush=True)
        return False
    finally:
        cleanup_clipboard_provider(clipboard_process)


def paste_image_with_chromium_clipboard(driver, comment_box, image_path: Path, timeout: int) -> bool:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        print(f"Comment image not found for Chromium clipboard copy: {path}", flush=True)
        return False

    if not sys.platform.startswith("linux"):
        return False

    baseline_count = comment_image_attachment_state(driver, comment_box).get("previewCount", 0)
    original_handle = driver.current_window_handle
    image_handle = None
    try:
        driver.switch_to.new_window("tab")
        image_handle = driver.current_window_handle
        driver.get(path.as_uri())
        WebDriverWait(driver, 10).until(
            lambda current_driver: current_driver.execute_script(
                "const img = document.querySelector('img'); return !!img && img.complete && img.naturalWidth > 0;"
            )
        )
        try:
            image_element = driver.find_element(By.CSS_SELECTOR, "img")
            click_element_with_xdotool(driver, image_element)
        except Exception as exc:
            print(f"Could not focus Chromium image tab with xdotool, continuing: {exc}", flush=True)
        try:
            driver.execute_cdp_cmd(
                "Browser.grantPermissions",
                {"permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]},
            )
        except Exception as exc:
            print(f"Could not grant Chromium clipboard permission, continuing: {exc}", flush=True)

        result = driver.execute_async_script(
            """
const done = arguments[arguments.length - 1];
(async function() {
  try {
    const img = document.querySelector('img');
    if (!img || !img.complete || !img.naturalWidth) {
      done({ok: false, error: 'image-not-loaded'});
      return;
    }
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);
    canvas.toBlob(async (blob) => {
      try {
        if (!blob) {
          done({ok: false, error: 'canvas-to-blob-failed'});
          return;
        }
        const item = new ClipboardItem({'image/png': blob});
        await navigator.clipboard.write([item]);
        done({
          ok: true,
          type: blob.type,
          size: blob.size,
          secure: window.isSecureContext,
          hasClipboard: !!navigator.clipboard
        });
      } catch (error) {
        done({
          ok: false,
          error: String(error && error.name ? error.name + ': ' + error.message : error),
          secure: window.isSecureContext,
          hasClipboard: !!navigator.clipboard
        });
      }
    }, 'image/png');
  } catch (error) {
    done({
      ok: false,
      error: String(error && error.name ? error.name + ': ' + error.message : error),
      secure: window.isSecureContext,
      hasClipboard: !!navigator.clipboard
    });
  }
})();
"""
        ) or {}
        print(f"Chromium image clipboard write result: {result}", flush=True)
        if not result.get("ok"):
            return False
    except Exception as exc:
        print(f"Could not copy image through Chromium clipboard API: {exc}", flush=True)
        return False
    finally:
        try:
            if image_handle and image_handle in driver.window_handles:
                driver.switch_to.window(image_handle)
                driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(original_handle)
        except Exception:
            pass

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", comment_box)
        comment_box.click()
    except Exception:
        try:
            focus_comment_box(driver, comment_box)
        except Exception:
            pass
    click_element_with_xdotool(driver, comment_box)
    try:
        result = subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        print(f"Could not paste Chromium-copied image with xdotool: {exc}", flush=True)
        return False
    if result.returncode != 0:
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        print(f"xdotool paste of Chromium-copied image failed: {output[:500]}", flush=True)
        return False
    print("Chromium-copied image pasted with xdotool Ctrl+V.", flush=True)

    if wait_for_comment_image_ready(driver, comment_box, baseline_count=baseline_count, timeout=min(max(timeout, 15), 45)):
        print("Image copied through Chromium clipboard and preview/upload is ready.", flush=True)
        return True
    screenshot_path = "comment_image_chromium_clipboard_not_ready.png"
    driver.save_screenshot(screenshot_path)
    print(f"Chromium clipboard paste did not produce a ready preview. Saved screenshot: {screenshot_path}", flush=True)
    return False


def paste_image_with_chromium_context_copy(driver, comment_box, image_path: Path, timeout: int) -> bool:
    path = Path(image_path).expanduser().resolve()
    if not path.exists() or not sys.platform.startswith("linux"):
        return False

    baseline_count = comment_image_attachment_state(driver, comment_box).get("previewCount", 0)
    original_handle = driver.current_window_handle
    image_handle = None
    try:
        driver.switch_to.new_window("tab")
        image_handle = driver.current_window_handle
        driver.get(path.as_uri())
        WebDriverWait(driver, 10).until(
            lambda current_driver: current_driver.execute_script(
                "var im = document.querySelector('img'); return !!im && im.complete && im.naturalWidth > 0;"
            )
        )
        image_element = driver.find_element(By.CSS_SELECTOR, "img")
        click_element_with_xdotool(driver, image_element)
        coords = driver.execute_script(
            """
	const rect = arguments[0].getBoundingClientRect();
	return [
	  Math.round(Math.max(window.screenX || 0, 0) + rect.left + rect.width / 2),
	  Math.round(Math.max(window.screenY || 0, 0) + rect.top + rect.height / 2)
	];
	""",
            image_element,
        )
        subprocess.run(
            ["xdotool", "mousemove", str(int(coords[0])), str(int(coords[1])), "click", "3"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        time.sleep(0.5)
        # In Chromium's image context menu this selects "Copy image".
        subprocess.run(
            ["xdotool", "key", "Down", "Down", "Down", "Return"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        time.sleep(1)
        targets = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        print(
            "Chromium context-menu copy targets: "
            + ", ".join(line for line in targets.splitlines() if line)[:500],
            flush=True,
        )
        if "image/png" not in targets and "text/html" not in targets:
            return False
    except Exception as exc:
        print(f"Could not copy image through Chromium context menu: {exc}", flush=True)
        return False
    finally:
        try:
            subprocess.run(["xdotool", "key", "Escape"], check=False, capture_output=True, text=True, timeout=5)
        except Exception:
            pass
        try:
            if image_handle and image_handle in driver.window_handles:
                driver.switch_to.window(image_handle)
                driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(original_handle)
        except Exception:
            pass

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", comment_box)
        comment_box.click()
    except Exception:
        try:
            focus_comment_box(driver, comment_box)
        except Exception:
            pass
    click_element_with_xdotool(driver, comment_box)
    try:
        result = subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        print(f"Could not paste Chromium context-copied image: {exc}", flush=True)
        return False
    if result.returncode != 0:
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        print(f"xdotool paste of Chromium context-copied image failed: {output[:500]}", flush=True)
        return False
    print("Chromium context-copied image pasted with xdotool Ctrl+V.", flush=True)

    if wait_for_comment_image_ready(driver, comment_box, baseline_count=baseline_count, timeout=min(max(timeout, 15), 45)):
        print("Image copied through Chromium context menu and preview/upload is ready.", flush=True)
        return True
    screenshot_path = "comment_image_chromium_context_copy_not_ready.png"
    driver.save_screenshot(screenshot_path)
    print(f"Chromium context-menu paste did not produce a ready preview. Saved screenshot: {screenshot_path}", flush=True)
    return False


def click_element_with_xdotool(driver, element) -> bool:
    script = """
const el = arguments[0];
el.scrollIntoView({block: 'center'});
const rect = el.getBoundingClientRect();
const x = Math.round(Math.max(window.screenX || 0, 0) + rect.left + rect.width / 2);
const y = Math.round(Math.max(window.screenY || 0, 0) + rect.top + rect.height / 2);
return {x, y, rect: {left: rect.left, top: rect.top, width: rect.width, height: rect.height}, screenX: window.screenX, screenY: window.screenY, outerHeight: window.outerHeight, innerHeight: window.innerHeight};
"""
    try:
        coords = driver.execute_script(script, element) or {}
        x = int(coords.get("x") or 0)
        y = int(coords.get("y") or 0)
    except Exception as exc:
        print(f"Could not calculate xdotool click coordinates: {exc}", flush=True)
        return False
    if x <= 0 or y <= 0:
        print(f"Invalid xdotool click coordinates: {coords}", flush=True)
        return False
    try:
        result = subprocess.run(
            ["xdotool", "mousemove", str(x), str(y), "click", "1"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        print(f"Could not click comment box with xdotool: {exc}", flush=True)
        return False
    if result.returncode != 0:
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        print(f"xdotool click failed: {output[:500]}", flush=True)
        return False
    print(f"Comment box clicked with xdotool at ({x}, {y}).", flush=True)
    time.sleep(0.4)
    return True


def paste_image_with_dom_event(driver, comment_box, image_path: Path, timeout: int) -> bool:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        print(f"Comment image not found for DOM paste: {path}", flush=True)
        return False

    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        print(f"Could not read image for DOM paste: {exc}", flush=True)
        return False

    baseline_count = comment_image_attachment_state(driver, comment_box).get("previewCount", 0)
    script = """
const box = arguments[0];
const name = arguments[1];
const mime = arguments[2];
const b64 = arguments[3];
const done = arguments[arguments.length - 1];
try {
  box.scrollIntoView({block: 'center'});
  box.focus();
  const range = document.createRange();
  range.selectNodeContents(box);
  range.collapse(false);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);

  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const file = new File([bytes], name, {type: mime, lastModified: Date.now()});
  const dt = new DataTransfer();
  dt.items.add(file);

  const event = new Event('paste', {bubbles: true, cancelable: true, composed: true});
  Object.defineProperty(event, 'clipboardData', {value: dt});
  Object.defineProperty(event, 'dataTransfer', {value: dt});
  const dispatched = box.dispatchEvent(event);
  done({ok: true, dispatched, files: dt.files.length, target: box.tagName});
} catch (error) {
  done({ok: false, error: String(error && error.message ? error.message : error)});
}
"""
    try:
        result = driver.execute_async_script(script, comment_box, path.name, mime, encoded) or {}
        print(f"DOM paste event result: {result}", flush=True)
    except Exception as exc:
        print(f"Could not dispatch DOM paste event for image: {exc}", flush=True)
        return False

    if wait_for_comment_image_ready(driver, comment_box, baseline_count=baseline_count, timeout=min(max(timeout, 15), 45)):
        print("Image pasted with DOM paste event and preview/upload is ready.", flush=True)
        return True
    screenshot_path = "comment_image_dom_paste_not_ready.png"
    driver.save_screenshot(screenshot_path)
    print(f"DOM paste event did not produce a ready preview. Saved screenshot: {screenshot_path}", flush=True)
    return False


def set_file_input_with_cdp(driver, file_input, path: Path) -> bool:
    marker = f"metaflow-upload-{uuid.uuid4().hex}"
    try:
        driver.execute_script("arguments[0].dataset.metaflowUploadMarker = arguments[1];", file_input, marker)
        document = driver.execute_cdp_cmd("DOM.getDocument", {"depth": 0, "pierce": True})
        root_id = document.get("root", {}).get("nodeId")
        if not root_id:
            return False
        node = driver.execute_cdp_cmd(
            "DOM.querySelector",
            {"nodeId": root_id, "selector": f'input[type="file"][data-metaflow-upload-marker="{marker}"]'},
        )
        node_id = node.get("nodeId")
        if not node_id:
            return False
        driver.execute_cdp_cmd("DOM.setFileInputFiles", {"nodeId": node_id, "files": [str(path)]})
        state = driver.execute_script(
            """
const el = arguments[0];
for (const name of ['input', 'change']) {
  el.dispatchEvent(new Event(name, {bubbles: true, cancelable: true, composed: true}));
}
return {files: el.files ? el.files.length : 0, value: el.value || ''};
""",
            file_input,
        )
        print(f"Set comment image file input through Chrome DevTools: {state}", flush=True)
        return True
    except Exception as exc:
        print(f"Could not set file input through Chrome DevTools: {exc}", flush=True)
        return False


def set_file_input_by_id_with_cdp(driver, input_id: str, path: Path) -> bool:
    if not input_id:
        return False
    selector = f'input[type="file"][data-metaflow-file-input-id="{input_id}"]'
    try:
        document = driver.execute_cdp_cmd("DOM.getDocument", {"depth": 0, "pierce": True})
        root_id = document.get("root", {}).get("nodeId")
        if not root_id:
            return False
        node = driver.execute_cdp_cmd("DOM.querySelector", {"nodeId": root_id, "selector": selector})
        node_id = node.get("nodeId")
        if not node_id:
            return False
        driver.execute_cdp_cmd("DOM.setFileInputFiles", {"nodeId": node_id, "files": [str(path)]})
        expression = f"""
(() => {{
  const el = document.querySelector({json.dumps(selector)});
  if (!el) return {{files: -1, value: ''}};
  for (const name of ['input', 'change']) {{
    el.dispatchEvent(new Event(name, {{bubbles: true, cancelable: true, composed: true}}));
  }}
  return {{files: el.files ? el.files.length : 0, value: el.value || ''}};
}})()
"""
        state = driver.execute_cdp_cmd("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        value = state.get("result", {}).get("value")
        print(f"Set comment image file input through Chrome DevTools by id={input_id}: {value}", flush=True)
        return True
    except Exception as exc:
        print(f"Could not set file input through Chrome DevTools by id={input_id}: {exc}", flush=True)
        return False


def attach_image_with_file_input(driver, image_path: Path, timeout: int, comment_box=None) -> bool:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        print(f"Comment image not found: {path}", flush=True)
        return False

    try:
        if comment_box is None:
            comment_box = wait_visible_comment_box(driver, 5)
        baseline_count = comment_image_attachment_state(driver, comment_box).get("previewCount", 0)
    except Exception:
        baseline_count = 0

    print(f"Attaching image to comment: {path}", flush=True)
    closed_dialog = False
    for _ in range(3):
        if not close_unrelated_post_dialog(driver):
            break
        closed_dialog = True
    if closed_dialog:
        try:
            comment_box = wait_visible_comment_box(driver, 10)
            baseline_count = comment_image_attachment_state(driver, comment_box).get("previewCount", 0)
        except Exception:
            pass

    button_candidates = find_comment_photo_buttons(driver, comment_box) if comment_box is not None else []
    if not button_candidates and comment_box is None:
        click_comment_photo_button(driver, None)
        try:
            file_inputs = WebDriverWait(driver, 5).until(lambda current_driver: find_file_inputs_by_ids(current_driver, set()))
        except TimeoutException:
            file_inputs = []
        button_candidates = []
    else:
        file_inputs = []

    tried_any_input = False
    if button_candidates:
        print(f"Found {len(button_candidates)} nearby comment photo button candidate(s).", flush=True)

    # Try each nearby comment toolbar button separately. This avoids sending the
    # image to Facebook's unrelated "Create post" file input.
    for button_index, button in enumerate(button_candidates, start=1):
        close_unrelated_post_dialog(driver)
        known_inputs = known_file_input_ids(driver)
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
            time.sleep(0.2)
            try:
                button.click()
            except Exception:
                driver.execute_script("arguments[0].click();", button)
            print(f"Clicked comment photo button candidate {button_index}/{len(button_candidates)}.", flush=True)
        except Exception as exc:
            print(f"Could not click comment photo button candidate {button_index}: {exc}", flush=True)
            continue

        try:
            file_inputs = WebDriverWait(driver, 5).until(
                lambda current_driver: find_file_inputs_by_ids(current_driver, known_inputs, comment_box)
            )
        except TimeoutException:
            file_inputs = []

        if not file_inputs:
            if attach_image_with_native_file_dialog(driver, button, comment_box, path, baseline_count):
                return True
            continue

        for input_index, file_input in enumerate(file_inputs, start=1):
            tried_any_input = True
            try:
                metadata = driver.execute_script(
                    "return {accept: arguments[0].accept || '', id: arguments[0].dataset.metaflowFileInputId || ''};",
                    file_input,
                )
            except Exception:
                metadata = {}
            print(
                "Trying comment image file input "
                f"{input_index}/{len(file_inputs)} from button {button_index}/{len(button_candidates)}: {metadata}",
                flush=True,
            )
            try:
                driver.execute_script(
                    "arguments[0].style.display='block'; arguments[0].style.visibility='visible'; arguments[0].style.opacity=1;",
                    file_input,
                )
            except Exception:
                pass

            input_id = str(metadata.get("id") or "") if isinstance(metadata, dict) else ""
            if not set_file_input_by_id_with_cdp(driver, input_id, path) and not set_file_input_with_cdp(
                driver, file_input, path
            ):
                try:
                    file_input.send_keys(str(path))
                except Exception as exc:
                    print(f"Could not send image path to file input {input_index}: {exc}", flush=True)
                    continue

            if page_has_unrelated_post_dialog(driver):
                close_unrelated_post_dialog(driver)
                try:
                    comment_box = wait_visible_comment_box(driver, 8)
                except TimeoutException:
                    pass
                print("File input opened the create-post composer, not the comment uploader; trying next candidate.", flush=True)
                continue

            try:
                comment_box = wait_visible_comment_box(driver, min(max(timeout // 3, 5), 15))
            except TimeoutException:
                pass
            if comment_box is None:
                screenshot_path = "comment_box_after_image_upload_not_found.png"
                driver.save_screenshot(screenshot_path)
                print(f"Could not find comment box after image upload. Saved screenshot: {screenshot_path}", flush=True)
                return False
            if wait_for_comment_image_ready(driver, comment_box, baseline_count=baseline_count, timeout=25):
                print("Image attached to comment and preview/upload is ready.", flush=True)
                return True
            print("File input did not attach a ready comment image; trying next candidate if available.", flush=True)
            close_unrelated_post_dialog(driver)

    if button_candidates:
        screenshot_path = "comment_image_upload_not_ready.png"
        driver.save_screenshot(screenshot_path)
        detail = "after trying nearby comment photo button candidates"
        print(f"Image upload did not produce a ready preview {detail}. Saved screenshot: {screenshot_path}", flush=True)
        return False

    if not file_inputs:
        button = find_comment_photo_button(driver, comment_box) if comment_box is not None else None
        if button is not None and attach_image_with_native_file_dialog(driver, button, comment_box, path, baseline_count):
            return True
        screenshot_path = "comment_image_input_not_found.png"
        driver.save_screenshot(screenshot_path)
        print(f"Could not find a file input for comment image. Saved screenshot: {screenshot_path}", flush=True)
        return False

    for index, file_input in enumerate(file_inputs, start=1):
        tried_any_input = True
        try:
            metadata = driver.execute_script(
                "return {accept: arguments[0].accept || '', id: arguments[0].dataset.metaflowFileInputId || ''};",
                file_input,
            )
        except Exception:
            metadata = {}
        print(f"Trying comment image file input {index}/{len(file_inputs)}: {metadata}", flush=True)
        try:
            driver.execute_script(
                "arguments[0].style.display='block'; arguments[0].style.visibility='visible'; arguments[0].style.opacity=1;",
                file_input,
            )
        except Exception:
            pass

        input_id = str(metadata.get("id") or "") if isinstance(metadata, dict) else ""
        if not set_file_input_by_id_with_cdp(driver, input_id, path) and not set_file_input_with_cdp(
            driver, file_input, path
        ):
            try:
                file_input.send_keys(str(path))
            except Exception as exc:
                print(f"Could not send image path to file input {index}: {exc}", flush=True)
                continue

        if page_has_unrelated_post_dialog(driver):
            close_unrelated_post_dialog(driver)
            try:
                comment_box = wait_visible_comment_box(driver, 8)
            except TimeoutException:
                pass
            print("File input opened the create-post composer, not the comment uploader; trying next candidate.", flush=True)
            continue

        try:
            comment_box = wait_visible_comment_box(driver, min(max(timeout // 3, 5), 15))
        except TimeoutException:
            pass
        if comment_box is None:
            screenshot_path = "comment_box_after_image_upload_not_found.png"
            driver.save_screenshot(screenshot_path)
            print(f"Could not find comment box after image upload. Saved screenshot: {screenshot_path}", flush=True)
            return False
        if wait_for_comment_image_ready(driver, comment_box, baseline_count=baseline_count, timeout=25):
            print("Image attached to comment and preview/upload is ready.", flush=True)
            return True
        print(f"File input {index} did not attach a ready comment image; trying next candidate if available.", flush=True)

    screenshot_path = "comment_image_upload_not_ready.png"
    driver.save_screenshot(screenshot_path)
    detail = "after trying file inputs" if tried_any_input else "because no comment file input was found"
    print(f"Image upload did not produce a ready preview {detail}. Saved screenshot: {screenshot_path}", flush=True)
    return False


def attach_image_with_native_file_dialog(driver, button, comment_box, path: Path, baseline_count: int) -> bool:
    if not sys.platform.startswith("linux"):
        return False
    print("Trying native file dialog upload via xdotool.", flush=True)
    try:
        if not click_element_with_xdotool(driver, button):
            return False
        time.sleep(2.5)
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "ctrl+l"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        time.sleep(0.2)
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "1", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        time.sleep(0.3)
        subprocess.run(
            ["xdotool", "key", "Return"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        print(f"Could not drive native file dialog: {exc}", flush=True)
        return False

    if wait_for_comment_image_ready(driver, comment_box, baseline_count=baseline_count, timeout=45):
        print("Image attached through native file dialog and preview/upload is ready.", flush=True)
        return True
    screenshot_path = "comment_image_native_dialog_not_ready.png"
    driver.save_screenshot(screenshot_path)
    print(f"Native file dialog upload did not produce a ready preview. Saved screenshot: {screenshot_path}", flush=True)
    return False


def attach_image_to_comment(driver, comment_box, image_path: str, timeout: int) -> bool:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        print(f"Comment image not found: {path}", flush=True)
        return False
    path = prepare_comment_upload_image(path)

    attach_mode = os.getenv("COMMENT_IMAGE_ATTACH_MODE", "auto").strip().lower()
    if attach_mode in {"paste", "clipboard", "auto"}:
        if paste_image_from_clipboard(driver, comment_box, path, timeout):
            return True
        print("X11 clipboard image paste was not ready; trying Chromium context-menu copy/paste.", flush=True)
        if paste_image_with_chromium_context_copy(driver, comment_box, path, timeout):
            return True
        print("Chromium context-menu paste was not ready; trying Chromium clipboard API copy/paste.", flush=True)
        if paste_image_with_chromium_clipboard(driver, comment_box, path, timeout):
            return True
        if attach_mode in {"paste", "clipboard"}:
            return False
        print("Chromium clipboard paste was not ready; trying DOM paste event.", flush=True)

    if attach_mode in {"dom", "dom-paste", "auto"}:
        if paste_image_with_dom_event(driver, comment_box, path, timeout):
            return True
        if attach_mode in {"dom", "dom-paste"}:
            return False
        print("DOM paste event was not ready; falling back to file input upload.", flush=True)

    return attach_image_with_file_input(driver, path, timeout, comment_box=comment_box)


def find_comment_submit_button(driver, comment_box):
    script = """
const box = arguments[0];
const exactLabels = new Set(['send', 'kirim', 'post', 'submit', 'comment', 'komentar']);
const allowed = ['send', 'kirim', 'submit', 'comment', 'komentar'];
const blocked = [
  'avatar', 'sticker', 'stiker', 'gif', 'emoji', 'emoticon',
  'photo', 'foto', 'gambar', 'image', 'attach', 'attachment', 'lampir',
  'action', 'tindakan', 'menu', 'more', 'lainnya', 'posting-an', 'postingan',
  'shop', 'seedsunrise', 'share', 'bagikan', 'save', 'simpan'
];
const boxRect = box.getBoundingClientRect();
let root = box;
for (let i = 0; i < 8 && root.parentElement; i++) {
  root = root.parentElement;
  const rect = root.getBoundingClientRect();
  if (rect.width >= boxRect.width && rect.height > boxRect.height && rect.height < 420) break;
}
const items = Array.from(root.querySelectorAll('[role="button"], button'))
  .map((el) => {
    const rect = el.getBoundingClientRect();
    const label = [
      el.getAttribute('aria-label') || '',
      el.getAttribute('title') || '',
      el.innerText || ''
    ].join(' ').toLowerCase().trim();
    const style = window.getComputedStyle(el);
    const disabled = el.getAttribute('aria-disabled') === 'true' || el.disabled;
    return {el, rect, label, style, disabled};
  })
  .filter((item) => {
    if (item.disabled || item.style.visibility === 'hidden' || item.style.display === 'none') return false;
    if (item.rect.width < 8 || item.rect.height < 8) return false;
    if (blocked.some((label) => item.label.includes(label))) return false;
    if (!allowed.some((label) => item.label === label || item.label.includes(label))) return false;
    const nearY = Math.abs((item.rect.top + item.rect.bottom) / 2 - (boxRect.top + boxRect.bottom) / 2) < 140;
    const nearX = item.rect.left > boxRect.left - 30 && item.rect.left < boxRect.right + 180;
    return nearY && nearX;
  })
  .sort((a, b) => {
    const aExact = exactLabels.has(a.label.trim()) ? 0 : 1;
    const bExact = exactLabels.has(b.label.trim()) ? 0 : 1;
    if (aExact !== bExact) return aExact - bExact;
    const ay = Math.abs((a.rect.top + a.rect.bottom) / 2 - (boxRect.top + boxRect.bottom) / 2);
    const by = Math.abs((b.rect.top + b.rect.bottom) / 2 - (boxRect.top + boxRect.bottom) / 2);
    if (Math.abs(ay - by) > 8) return ay - by;
    return b.rect.left - a.rect.left;
  });
return items.length ? items[0].el : null;
"""
    try:
        return driver.execute_script(script, comment_box)
    except Exception:
        return None


def comment_box_text(driver, comment_box) -> str:
    try:
        value = driver.execute_script(
            "return (arguments[0].innerText || arguments[0].textContent || '').trim();",
            comment_box,
        )
        return str(value or "").strip()
    except StaleElementReferenceException:
        return ""
    except Exception:
        return ""


def normalize_comment_probe(value: str) -> str:
    return " ".join((value or "").replace("\xa0", " ").split()).strip()


def repair_comment_text_encoding(value: str) -> str:
    replacements = {
        "â\x80\x99": "'",
        "â€™": "'",
        "â\x80\x98": "'",
        "â€˜": "'",
        "â\x80\x9c": '"',
        "â€œ": '"',
        "â\x80\x9d": '"',
        "â€\x9d": '"',
        "â\x80\x93": "-",
        "â€“": "-",
        "â\x80\x94": "-",
        "â€”": "-",
        "â\x80¦": "...",
        "â€¦": "...",
        "Â\xa0": " ",
        "Â ": " ",
    }
    repaired = value or ""
    for old, new in replacements.items():
        repaired = repaired.replace(old, new)
    return repaired


def comment_text_visible(driver, comment_box, comment_text: str) -> bool:
    actual = normalize_comment_probe(comment_box_text(driver, comment_box))
    expected = normalize_comment_probe(repair_comment_text_encoding(comment_text))
    return bool(expected and actual and (expected in actual or actual in expected))


def focus_comment_box(driver, comment_box) -> None:
    driver.execute_script(
        """
arguments[0].scrollIntoView({block: 'center'});
arguments[0].focus();
const range = document.createRange();
range.selectNodeContents(arguments[0]);
range.collapse(false);
const selection = window.getSelection();
selection.removeAllRanges();
selection.addRange(range);
""",
        comment_box,
    )


def insert_comment_text_with_cdp(driver, comment_box, comment_text: str) -> bool:
    try:
        focus_comment_box(driver, comment_box)
        driver.execute_cdp_cmd("Input.insertText", {"text": comment_text})
        time.sleep(0.8)
    except Exception as exc:
        print(f"Could not insert comment text with CDP: {exc}", flush=True)
        return False
    if comment_text_visible(driver, comment_box, comment_text):
        print("Comment text inserted with CDP.", flush=True)
        return True
    print(f"CDP insert did not appear in comment box. Current text={comment_box_text(driver, comment_box)!r}", flush=True)
    return False


def page_contains_comment_text(driver, comment_text: str) -> bool:
    expected = normalize_comment_probe(repair_comment_text_encoding(comment_text))
    if not expected:
        return False
    try:
        body_text = driver.execute_script("return document.body ? document.body.innerText : '';")
    except Exception:
        return False
    body = normalize_comment_probe(repair_comment_text_encoding(str(body_text or "")))
    return expected in body


def wait_before_comment_submit(driver, comment_box, comment_text: str) -> None:
    wait_seconds = env_int("COMMENT_BEFORE_SUBMIT_WAIT_SECONDS", 5, 0, 30)
    if wait_seconds > 0:
        print(f"Waiting {wait_seconds}s before submit so Facebook can render text/image...", flush=True)
    deadline = time.time() + wait_seconds
    last_text = ""
    stable_visible_count = 0
    while time.time() <= deadline:
        current = comment_box_text(driver, comment_box)
        visible = comment_text_visible(driver, comment_box, comment_text)
        if visible and normalize_comment_probe(current) == normalize_comment_probe(last_text):
            stable_visible_count += 1
        elif visible:
            stable_visible_count = 1
        else:
            stable_visible_count = 0
        last_text = current
        time.sleep(1)

    if not comment_text_visible(driver, comment_box, comment_text):
        print(
            f"Comment text is not visible immediately before submit. Current composer text={comment_box_text(driver, comment_box)!r}. "
            "Trying CDP insertText again.",
            flush=True,
        )
        insert_comment_text_with_cdp(driver, comment_box, comment_text)
        time.sleep(1)

    before_text = comment_box_text(driver, comment_box)
    print(
        "Before submit composer check: "
        f"visible={comment_text_visible(driver, comment_box, comment_text)} "
        f"stable_checks={stable_visible_count} text={before_text!r}",
        flush=True,
    )
    screenshot_path = "before_comment_submit.png"
    driver.save_screenshot(screenshot_path)
    print(f"Saved before-submit screenshot: {screenshot_path}", flush=True)
    focus_comment_box(driver, comment_box)
    time.sleep(0.5)


def submit_comment_with_enter(driver, comment_box) -> bool:
    try:
        focus_comment_box(driver, comment_box)
        driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
            "type": "keyDown",
            "key": "Enter",
            "code": "Enter",
            "windowsVirtualKeyCode": 13,
            "nativeVirtualKeyCode": 13,
        })
        driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
            "type": "keyUp",
            "key": "Enter",
            "code": "Enter",
            "windowsVirtualKeyCode": 13,
            "nativeVirtualKeyCode": 13,
        })
        print("Comment submit Enter sent with CDP.", flush=True)
        return True
    except Exception as exc:
        print(f"Could not send Enter with CDP, trying Selenium Enter: {exc}", flush=True)

    try:
        focus_comment_box(driver, comment_box)
        comment_box.send_keys(Keys.ENTER)
        print("Comment submit Enter sent.", flush=True)
        return True
    except Exception as exc:
        print(f"Could not send Enter to comment box directly, trying active element: {exc}", flush=True)
        try:
            ActionChains(driver).send_keys(Keys.ENTER).perform()
            print("Comment submit Enter sent to active element.", flush=True)
            return True
        except Exception as active_exc:
            print(f"Could not submit with Enter: {active_exc}", flush=True)
            return False


def submit_comment(driver, comment_box, comment_text: str, image_required: bool = False) -> None:
    wait_seconds = env_int("COMMENT_POST_SUBMIT_WAIT_SECONDS", 20, 3, 60)
    wait_before_comment_submit(driver, comment_box, comment_text)
    if image_required and not wait_for_comment_image_ready(driver, comment_box, require_new_preview=False):
        screenshot_path = "comment_image_missing_before_submit.png"
        driver.save_screenshot(screenshot_path)
        raise RuntimeError(
            "Comment image preview was not ready immediately before submit, so I did not submit. "
            f"Saved screenshot: {screenshot_path}"
        )
    submitted = False
    if image_required:
        button = find_comment_submit_button(driver, comment_box)
        if button is not None:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                button.click()
                print("Comment submit button clicked for image comment.", flush=True)
                submitted = True
            except Exception as exc:
                print(f"Could not click image comment submit button, trying Enter: {exc}", flush=True)

    if not submitted:
        submitted = submit_comment_with_enter(driver, comment_box)
    if not submitted:
        button = find_comment_submit_button(driver, comment_box)
        if button is not None:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                button.click()
                print("Comment submit button clicked.", flush=True)
                submitted = True
            except Exception as exc:
                print(f"Could not click comment submit button normally, trying JavaScript click: {exc}", flush=True)
                try:
                    driver.execute_script("arguments[0].click();", button)
                    print("Comment submit button clicked with JavaScript.", flush=True)
                    submitted = True
                except Exception as js_exc:
                    print(f"Could not click comment submit button: {js_exc}", flush=True)
        if not submitted:
            raise RuntimeError("Could not submit the Facebook comment.")

    print(f"Waiting {wait_seconds}s for Facebook to finish submitting the comment...", flush=True)
    time.sleep(wait_seconds)
    try:
        fresh_comment_box = wait_visible_comment_box(driver, 5)
    except TimeoutException:
        fresh_comment_box = comment_box
    composer_still_has_text = comment_text_visible(driver, fresh_comment_box, comment_text)
    if page_contains_comment_text(driver, comment_text) and not composer_still_has_text:
        print("Comment submitted and found on page.", flush=True)
        return
    if composer_still_has_text:
        screenshot_path = "comment_submit_unverified.png"
        driver.save_screenshot(screenshot_path)
        raise RuntimeError(
            "Comment text still appears in the composer after submit; "
            f"Facebook may not have accepted it yet. Saved screenshot: {screenshot_path}"
        )
    if page_contains_comment_text(driver, comment_text):
        print("Comment submitted and found on page.", flush=True)
    else:
        screenshot_path = "comment_submit_not_found.png"
        driver.save_screenshot(screenshot_path)
        if not env_bool("COMMENT_STRICT_SUBMIT_VERIFY", False):
            print(
                "Comment composer cleared after submit, but the exact submitted text was not found on the page. "
                "Treating as submitted because Facebook accepted/cleared the composer. "
                f"Saved screenshot for review: {screenshot_path}",
                flush=True,
            )
            return
        raise RuntimeError(
            "Comment composer cleared, but the submitted comment text was not found on the page. "
            f"Saved screenshot: {screenshot_path}"
        )

def copy_text_to_clipboard(text: str) -> bool:
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["pbcopy"],
                input=text.encode("utf-8"),
                check=False,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"Could not copy comment text to clipboard: {exc}", flush=True)
            return False
        if result.returncode != 0:
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            output = "\n".join(part for part in [stdout, stderr] if part)
            print(f"Could not copy comment text to clipboard: {output[:800]}", flush=True)
            return False
        return True

    if sys.platform.startswith("linux"):
        process = start_x11_clipboard_provider(text)
        if process is None:
            return False
        cleanup_clipboard_provider(process)
        return True

    print("Clipboard text paste is currently implemented for macOS and Linux/X11 only.", flush=True)
    return False


def start_x11_clipboard_provider(text: str) -> subprocess.Popen | None:
    try:
        process = subprocess.Popen(
            ["xclip", "-selection", "clipboard", "-in", "-loops", "5", "-quiet"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        print(f"Could not start X11 clipboard provider: {exc}", flush=True)
        return None

    try:
        assert process.stdin is not None
        process.stdin.write(text.encode("utf-8"))
        process.stdin.close()
    except OSError as exc:
        cleanup_clipboard_provider(process)
        print(f"Could not write comment text to X11 clipboard provider: {exc}", flush=True)
        return None

    # xclip remains alive while it owns the selection. More than one loop is
    # needed because Chrome/X11 may probe clipboard formats before the paste.
    time.sleep(0.2)
    if process.poll() is not None:
        stderr = ""
        try:
            stderr = (process.stderr.read() if process.stderr else b"").decode("utf-8", errors="replace").strip()
        except OSError:
            stderr = ""
        print(f"X11 clipboard provider exited before paste: {stderr[:800]}", flush=True)
        return None
    return process


def cleanup_clipboard_provider(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def paste_comment_text_from_clipboard(driver, comment_box, comment_text: str) -> bool:
    clipboard_process = None
    copied = False
    if sys.platform.startswith("linux"):
        clipboard_process = start_x11_clipboard_provider(comment_text)
        copied = clipboard_process is not None
    else:
        copied = copy_text_to_clipboard(comment_text)

    if not copied:
        return False

    try:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", comment_box)
            comment_box.click()
        except Exception:
            focus_comment_box(driver, comment_box)
        modifier = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
        ActionChains(driver).key_down(modifier).send_keys("v").key_up(modifier).perform()
        time.sleep(0.5)
        print("Comment text pasted from clipboard.", flush=True)
        return True
    finally:
        cleanup_clipboard_provider(clipboard_process)


def type_comment_text(driver, comment_box, comment_text: str) -> None:
    input_mode = os.getenv("COMMENT_TEXT_INPUT_MODE", "paste").strip().lower()
    if input_mode in {"paste", "clipboard", "auto"} and paste_comment_text_from_clipboard(
        driver, comment_box, comment_text
    ):
        return

    if input_mode in {"paste", "clipboard"}:
        raise RuntimeError("Could not paste comment text from clipboard.")

    comment_box.send_keys(selenium_safe_text(comment_text))


def type_comment_text_verified(driver, comment_box, comment_text: str) -> None:
    try:
        type_comment_text(driver, comment_box, comment_text)
    except RuntimeError as exc:
        print(f"Clipboard/send_keys text input failed: {exc}. Trying CDP insertText.", flush=True)
        if insert_comment_text_with_cdp(driver, comment_box, comment_text):
            return
        raise
    time.sleep(0.8)
    if comment_text_visible(driver, comment_box, comment_text):
        print(f"Comment text verified in composer: {comment_text!r}", flush=True)
        return

    print(
        f"Comment text was not visible after paste/send_keys. Current composer text={comment_box_text(driver, comment_box)!r}. "
        "Trying CDP insertText.",
        flush=True,
    )
    if insert_comment_text_with_cdp(driver, comment_box, comment_text):
        return

    raise RuntimeError("Comment text was not inserted into the Facebook composer.")


def selenium_safe_text(value: str) -> str:
    replacements = {
        "â\x80\x99": "'",
        "â\x80\x98": "'",
        "â\x80\x9c": '"',
        "â\x80\x9d": '"',
        "â\x80\x93": "-",
        "â\x80\x94": "-",
        "❤️": "❤",
    }
    cleaned = value or ""
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    # ChromeDriver send_keys cannot type non-BMP characters such as modern emoji.
    cleaned = "".join(ch for ch in cleaned if ord(ch) <= 0xFFFF)
    return " ".join(cleaned.split()).strip()


def comment_on_post(
    driver,
    post_url: str,
    comment_text: str,
    timeout: int,
    confirm: bool,
    submit: bool,
    image_path: str | None = None,
    wait_login_if_needed: bool = False,
    login_wait_timeout: int = 300,
) -> None:
    repaired_comment_text = repair_comment_text_encoding(comment_text)
    if repaired_comment_text != comment_text:
        print(f"Repaired comment text encoding: {repaired_comment_text!r}", flush=True)
        comment_text = repaired_comment_text

    print(f"Opening post page: {post_url}", flush=True)
    driver.get(post_url)

    print("Waiting for comment box...", flush=True)
    try:
        ensure_logged_in_before_comment(
            driver,
            wait_if_needed=wait_login_if_needed,
            post_url=post_url,
            wait_timeout=login_wait_timeout,
        )
    except RuntimeError as exc:
        print(str(exc), flush=True)
        raise

    for _ in range(3):
        if not close_unrelated_post_dialog(driver):
            break

    try:
        comment_box = wait_visible_comment_box(driver, timeout)
    except TimeoutException:
        screenshot_path = "comment_box_not_found.png"
        driver.save_screenshot(screenshot_path)
        raise RuntimeError(f"Could not find a comment box. Saved screenshot: {screenshot_path}")

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", comment_box)
    comment_box.click()

    image_attached = False
    if image_path:
        image_attached = attach_image_to_comment(driver, comment_box, image_path, timeout)
        if not image_attached:
            raise RuntimeError("Image was not attached, so the comment was not submitted.")
        # Facebook may rerender the composer after image upload, so type text after attachment.
        try:
            comment_box = wait_visible_comment_box(driver, timeout)
        except TimeoutException as exc:
            screenshot_path = "comment_box_after_image_not_found.png"
            driver.save_screenshot(screenshot_path)
            raise RuntimeError(f"Could not find comment box after image upload. Saved screenshot: {screenshot_path}") from exc

    try:
        type_comment_text_verified(driver, comment_box, comment_text)
    except RuntimeError as exc:
        screenshot_path = "comment_text_input_failed.png"
        driver.save_screenshot(screenshot_path)
        raise RuntimeError(f"{exc} Saved screenshot: {screenshot_path}") from exc
    print(f"Comment text typed: {comment_text!r}", flush=True)

    should_submit = submit
    if confirm and not submit:
        prompt = "Submit this comment now?"
        if image_path:
            prompt += f" Image attached: {'yes' if image_attached else 'no'}."
        answer = input(f"{prompt} Type y and press Enter to submit, or just press Enter to leave it typed: ")
        should_submit = answer.strip().lower() in {"y", "yes"}

    if should_submit:
        submit_comment(driver, comment_box, comment_text, image_required=bool(image_path))
    else:
        print("Comment left typed in the browser without submitting.", flush=True)


def submit_login_form(
    driver,
    login_url: str,
    username: str,
    password: str,
    username_selector: str,
    password_selector: str,
    submit_selector: str | None,
    timeout: int,
    click_button: bool,
) -> bool:
    print(f"Opening login page: {login_url}", flush=True)
    driver.get(login_url)

    try:
        print("Waiting for username field...", flush=True)
        email_input = wait_visible(driver, username_selector, timeout)
    except TimeoutException:
        if page_has_chrome_network_error(driver):
            print("Login page hit a Chrome network error. Retrying page load...", flush=True)
            for attempt in range(1, 4):
                driver.refresh()
                time.sleep(min(2 * attempt, 6))
                try:
                    email_input = wait_visible(driver, username_selector, timeout)
                    break
                except TimeoutException:
                    if attempt == 3:
                        screenshot_path = "login_field_not_found.png"
                        driver.save_screenshot(screenshot_path)
                        print(f"Username field not found after network retries. Saved screenshot: {screenshot_path}", flush=True)
                        print("Please reload/login manually in the browser before pressing Enter.", flush=True)
                        return False
            else:
                return False
        else:
            screenshot_path = "login_field_not_found.png"
            driver.save_screenshot(screenshot_path)
            print(f"Username field not found. Saved screenshot: {screenshot_path}", flush=True)
            print("You may already be logged in, or Facebook may be showing a verification/cookie page.", flush=True)
            print("If you are not logged in, log in manually before pressing Enter.", flush=True)
            return False

    email_input.clear()
    email_input.send_keys(username)

    print("Waiting for password field...", flush=True)
    password_input = wait_visible(driver, password_selector, timeout)
    password_input.clear()
    password_input.send_keys(password)

    print("Submitting login form...", flush=True)
    clicked = False
    if click_button:
        clicked = try_click_login_button(driver, submit_selector, timeout)

    if not clicked:
        password_input.send_keys(Keys.ENTER)

    print("Login form submitted.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Open a login page and submit credentials with Selenium.")
    parser.add_argument("--env", default=".env", help="Path to the env file. Defaults to .env")
    parser.add_argument("--accounts-file", default=None, help="Path to account JSON. Defaults to ACCOUNTS_FILE or accounts.json")
    parser.add_argument("--account", default=None, help="Account name to load from the account JSON.")
    parser.add_argument("--post-url", help="Optional Facebook post URL to open after login.")
    parser.add_argument("--comment", help="Optional comment text to type on the post page.")
    parser.add_argument("--comment-image", help="Optional local image path to attach to the comment.")
    parser.add_argument("--ai-comment", action="store_true", help="Extract the post and draft a comment with OpenAI.")
    parser.add_argument("--ai-product-promo", action="store_true", help="Use the post product link to draft a praise comment and scenario image.")
    parser.add_argument("--product-url", help="Optional product URL override when the post link cannot be detected.")
    parser.add_argument("--use-cases", help="Optional use cases to guide the product scenario image.")
    parser.add_argument("--image-prompt", help="Optional prompt for OpenAI image generation.")
    parser.add_argument("--ai-image-from-post", action="store_true", help="Extract the post and generate an image inspired by it.")
    parser.add_argument("--image-output", default=None, help="Image output path. Defaults to generated/post_image.png")
    parser.add_argument("--submit-comment", action="store_true", help="Submit the comment without an extra prompt.")
    parser.add_argument("--no-confirm-comment", action="store_true", help="Do not ask before submitting/leaving the comment.")
    parser.add_argument("--skip-login", action="store_true", help="Skip the login form and use the current Chrome profile session.")
    parser.add_argument("--wait-login-if-needed", action="store_true", help="If the browser is logged out, wait for manual login and then continue.")
    parser.add_argument("--login-only", action="store_true", help="Open the account Chrome profile and wait for manual Facebook login.")
    args = parser.parse_args()

    load_dotenv(args.env)
    accounts_file = args.accounts_file or os.getenv("ACCOUNTS_FILE", "accounts.json")
    apply_account_config(accounts_file, args.account or os.getenv("ACCOUNT_NAME"))

    login_url = os.getenv("LOGIN_URL", DEFAULT_LOGIN_URL)
    username_selector = os.getenv("USERNAME_SELECTOR", DEFAULT_USERNAME_SELECTOR)
    password_selector = os.getenv("PASSWORD_SELECTOR", DEFAULT_PASSWORD_SELECTOR)
    submit_selector = os.getenv("SUBMIT_SELECTOR", 'div[role="button"][aria-label="登录"]')
    success_selector = os.getenv("SUCCESS_SELECTOR")
    timeout = int(os.getenv("SELENIUM_TIMEOUT", "20"))
    profile_dir = os.getenv("CHROME_PROFILE_DIR")
    headless = env_bool("HEADLESS", False)
    keep_open = env_bool("KEEP_BROWSER_OPEN", True)
    click_button = env_bool("CLICK_LOGIN_BUTTON", False)
    post_url = args.post_url or os.getenv("POST_URL")
    comment_text = args.comment or os.getenv("COMMENT_TEXT")
    comment_image = args.comment_image or os.getenv("COMMENT_IMAGE")
    ai_comment = args.ai_comment or env_bool("AI_COMMENT", False)
    ai_product_promo = args.ai_product_promo or env_bool("AI_PRODUCT_PROMO", False)
    product_url = args.product_url or os.getenv("PRODUCT_URL")
    product_use_cases = args.use_cases or os.getenv("PRODUCT_USE_CASES", "")
    ai_language = os.getenv("AI_COMMENT_LANGUAGE", "the same language as the post")
    ai_comment_style = os.getenv(
        "AI_COMMENT_STYLE",
        "更随意一点，像平时聊天；可以自然用 it / this / that，不用刻意说产品名；不要用 The/the 开头；不要直接使用链接或标题里的关键词；偏好“好看 & 实用 / pretty & useful / cute & practical”这种短而有力的评价；表情由程序本地追加；别写成 slogan",
    )
    ai_comment_style = build_comment_style(ai_comment_style)
    image_prompt = args.image_prompt or os.getenv("IMAGE_PROMPT")
    ai_image_from_post = args.ai_image_from_post or env_bool("AI_IMAGE_FROM_POST", False)
    image_output = args.image_output or os.getenv("IMAGE_OUTPUT", "generated/post_image.png")
    image_size = os.getenv("OPENAI_IMAGE_SIZE", "1024x1024")
    image_quality = os.getenv("OPENAI_IMAGE_QUALITY", "auto")
    image_style = os.getenv(
        "AI_IMAGE_STYLE",
        "exact landing-page product match, new background, realistic customer phone photo, no people, no background blur, casual lived-in setting, natural light",
    )
    submit_comment = args.submit_comment or env_bool("SUBMIT_COMMENT", True)
    confirm_comment = not args.no_confirm_comment and env_bool("CONFIRM_BEFORE_COMMENT", False)
    skip_login = args.skip_login or env_bool("SKIP_LOGIN", False)
    wait_login_if_needed = args.wait_login_if_needed or env_bool("WAIT_LOGIN_IF_NEEDED", False)
    login_wait_timeout = int(os.getenv("MANUAL_LOGIN_TIMEOUT_SECONDS", "300"))
    if ai_product_promo and not post_url:
        raise ValueError("AI_PRODUCT_PROMO requires POST_URL or --post-url.")
    image_only = bool(
        image_prompt and not post_url and not ai_comment and not comment_text and not ai_image_from_post and not ai_product_promo
    )
    if image_only:
        ai_client = OpenAIContentClient()
        if not ai_client.ready():
            raise ValueError("Image generation requires OPENAI_API_KEY and OPENAI_ENABLED=true.")
        output_path = ai_client.generate_image(
            prompt=image_prompt,
            output_path=image_output,
            size=image_size,
            quality=image_quality,
        )
        if not output_path:
            raise RuntimeError("OpenAI did not return a usable image.")
        print(f"Generated image saved: {output_path}", flush=True)
        return

    username = os.getenv("LOGIN_USERNAME", "")
    password = os.getenv("LOGIN_PASSWORD", "")
    if not skip_login and not args.login_only:
        username = required_env("LOGIN_USERNAME")
        password = required_env("LOGIN_PASSWORD")
        validate_credentials(username, password)

    print(f"MetaFlow auto_login version: {APP_CODE_VERSION}", flush=True)
    print("Starting Chrome with Selenium...", flush=True)
    driver = build_driver(profile_dir=profile_dir, headless=headless)

    try:
        if args.login_only:
            wait_for_manual_login(driver, login_url, login_wait_timeout)
            return

        login_submitted = False
        if skip_login:
            print("Skipping login form and using the current browser session.", flush=True)
        else:
            login_submitted = submit_login_form(
                driver=driver,
                login_url=login_url,
                username=username,
                password=password,
                username_selector=username_selector,
                password_selector=password_selector,
                submit_selector=submit_selector,
                timeout=timeout,
                click_button=click_button,
            )

        if login_submitted and success_selector:
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, success_selector))
                )
                print("Success selector found. Login appears successful.")
            except TimeoutException:
                screenshot_path = "login_check_failed.png"
                driver.save_screenshot(screenshot_path)
                print(f"Success selector not found. Saved screenshot: {screenshot_path}")

        if not skip_login:
            print("If a verification, captcha, or device check appears, complete it manually in the browser.")
            input("Press Enter after login/verification to continue...")

        needs_post_content = bool(post_url and (ai_comment or ai_image_from_post or ai_product_promo))
        post_content = ""
        product_context = ""
        selected_product_url = ""
        if needs_post_content:
            if ai_product_promo:
                post_content, product_context, selected_product_url = collect_post_and_product_context(
                    driver=driver,
                    post_url=post_url,
                    timeout=timeout,
                    product_url=product_url,
                    wait_login_if_needed=wait_login_if_needed,
                    login_wait_timeout=login_wait_timeout,
                )
            else:
                post_content = extract_post_content(driver, post_url, timeout)
                ensure_logged_in_before_comment(
                    driver,
                    wait_if_needed=wait_login_if_needed,
                    post_url=post_url,
                    wait_timeout=login_wait_timeout,
                )
                print("\nExtracted post content preview:")
                print("-" * 40)
                print(post_content[:1200] or "<empty>")
                print("-" * 40)

        if ai_comment or image_prompt or ai_image_from_post or ai_product_promo:
            ai_client = OpenAIContentClient()
            if not ai_client.ready():
                return_to_post(driver, post_url or "")
                raise ValueError("OpenAI features require OPENAI_API_KEY and OPENAI_ENABLED=true.")

        if post_url and ai_product_promo:
            try:
                combined_context = (
                    f"Facebook post:\n{post_content}\n\n"
                    f"Product link:\n{selected_product_url or product_url or 'not found'}\n\n"
                    f"Product page:\n{product_context}\n\n"
                    f"Use cases to consider:\n{product_use_cases or 'derive from product details'}"
                )
                result = ai_client.generate_comment(
                    post_content=combined_context,
                    language=ai_language,
                    style=build_comment_style(
                        "随意一点，像真人刷到后随手写；不要用 The/the 开头；"
                        "可以自然用 it / this / that，不用刻意说产品名；不要直接使用链接或标题里的关键词；偏好“好看 & 实用 / pretty & useful / cute & practical”这种短而有力的评价；表情由程序本地追加；别写成 slogan"
                    ),
                )
                if not result or not result.get("comment"):
                    raise RuntimeError("OpenAI did not return a usable praise comment draft.")
                comment_text = result["comment"]
                print(f"AI praise comment draft: {comment_text!r}", flush=True)
                if result.get("rationale"):
                    print(f"Draft rationale: {result['rationale']}", flush=True)

                composition_name, composition_instruction = choose_product_photo_composition()
                print(f"Product image composition: {composition_name}", flush=True)
                product_prompt = build_product_scene_image_prompt(
                    post_content=post_content,
                    product_context=product_context,
                    use_cases=product_use_cases,
                    style=image_style,
                    composition=f"{composition_name}: {composition_instruction}",
                )
                reference_urls = extract_image_urls_from_context(product_context)
                if not reference_urls:
                    prompt_path = Path(image_output).with_suffix(".prompt.txt")
                    prompt_path.parent.mkdir(parents=True, exist_ok=True)
                    prompt_path.write_text(product_prompt, encoding="utf-8")
                    raise RuntimeError(
                        "Could not find product image references on the landing page, so I will not generate a fake product image. "
                        f"Saved the image prompt for inspection: {prompt_path}"
                    )
                print(f"Using product reference images: {', '.join(reference_urls[:2])}", flush=True)
                output_path = ai_client.generate_image_with_references(
                    prompt=product_prompt,
                    output_path=image_output,
                    reference_urls=reference_urls,
                    size=image_size,
                    quality=image_quality,
                )
                if not output_path:
                    prompt_path = Path(image_output).with_suffix(".prompt.txt")
                    prompt_path.parent.mkdir(parents=True, exist_ok=True)
                    prompt_path.write_text(product_prompt, encoding="utf-8")
                    if getattr(ai_client, "last_error", ""):
                        print(f"Image generation error: {ai_client.last_error}", flush=True)
                    raise RuntimeError(
                        "Reference-based image generation failed. I will not fall back to text-only generation because that can create a different product. "
                        f"Saved the image prompt for manual retry: {prompt_path}"
                    )
                else:
                    print(f"Generated product scenario image saved: {output_path}", flush=True)

                comment_on_post(
                    driver=driver,
                    post_url=post_url,
                    comment_text=comment_text,
                    timeout=timeout,
                    confirm=confirm_comment,
                    submit=submit_comment,
                    image_path=output_path,
                    wait_login_if_needed=wait_login_if_needed,
                    login_wait_timeout=login_wait_timeout,
                )
            except Exception:
                return_to_post(driver, post_url)
                raise

        elif post_url and ai_comment:
            result = ai_client.generate_comment(
                post_content=post_content,
                language=ai_language,
                style=ai_comment_style,
            )
            if not result or not result.get("comment"):
                raise RuntimeError("OpenAI did not return a usable comment draft.")
            comment_text = result["comment"]
            print(f"AI draft comment: {comment_text!r}", flush=True)
            if result.get("rationale"):
                print(f"Draft rationale: {result['rationale']}", flush=True)
            comment_on_post(
                driver=driver,
                post_url=post_url,
                comment_text=comment_text,
                timeout=timeout,
                confirm=confirm_comment,
                submit=submit_comment,
                wait_login_if_needed=wait_login_if_needed,
                login_wait_timeout=login_wait_timeout,
            )

        generated_image = False
        if image_prompt or ai_image_from_post:
            prompt = image_prompt or build_post_image_prompt(post_content, image_style)
            output_path = ai_client.generate_image(
                prompt=prompt,
                output_path=image_output,
                size=image_size,
                quality=image_quality,
            )
            if not output_path:
                raise RuntimeError("OpenAI did not return a usable image.")
            print(f"Generated image saved: {output_path}", flush=True)
            generated_image = True

        if post_url and comment_text and not ai_comment and not ai_product_promo:
            comment_on_post(
                driver=driver,
                post_url=post_url,
                comment_text=comment_text,
                timeout=timeout,
                confirm=confirm_comment,
                submit=submit_comment,
                image_path=comment_image,
                wait_login_if_needed=wait_login_if_needed,
                login_wait_timeout=login_wait_timeout,
            )
        elif not ai_comment and not ai_product_promo and not generated_image and (post_url or comment_text):
            print("POST_URL and COMMENT_TEXT must both be set to comment on a post.")

    finally:
        if not keep_open:
            driver.quit()


if __name__ == "__main__":
    main()
