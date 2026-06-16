import argparse
import json
import os
import random
import subprocess
import sys
import time
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
    extract_image_urls_from_context,
)


DEFAULT_LOGIN_URL = "https://www.facebook.com/login"
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
        f"{experience_rule}\n"
        "每次都换句式、换开头、换场景细节，避免和之前评论长得很像。"
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


def page_looks_logged_out(driver) -> bool:
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


def ensure_logged_in_before_comment(driver, screenshot_path: str = "not_logged_in.png") -> None:
    if not page_looks_logged_out(driver):
        return
    driver.save_screenshot(screenshot_path)
    raise RuntimeError(
        "The browser still looks logged out, so Facebook will not show a comment box. "
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


def build_driver(profile_dir: str | None, headless: bool) -> webdriver.Chrome:
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")

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
) -> tuple[str, str, str]:
    post_content = extract_post_content(driver, post_url, timeout)
    product_links = [product_url] if product_url else extract_product_links(driver, post_url)
    ensure_logged_in_before_comment(driver)

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


def click_comment_photo_button(driver) -> bool:
    button_xpaths = [
        "//*[@role='button' and (contains(@aria-label,'照片') or contains(@aria-label,'图片') or contains(@aria-label,'Photo') or contains(@aria-label,'photo') or contains(@aria-label,'Image') or contains(@aria-label,'image'))]",
        "//*[@aria-label and (contains(@aria-label,'照片') or contains(@aria-label,'图片') or contains(@aria-label,'Photo') or contains(@aria-label,'photo') or contains(@aria-label,'Image') or contains(@aria-label,'image'))]",
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
    if sys.platform != "darwin":
        print("Clipboard image paste is currently implemented for macOS only.", flush=True)
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


def paste_image_from_clipboard(driver, comment_box, image_path: Path, timeout: int) -> bool:
    if not copy_image_to_clipboard(image_path):
        return False

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", comment_box)
        comment_box.click()
        modifier = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
        ActionChains(driver).key_down(modifier).send_keys("v").key_up(modifier).perform()
    except Exception as exc:
        print(f"Could not paste image from clipboard: {exc}", flush=True)
        return False

    time.sleep(min(max(timeout / 4, 3), 8))
    print("Image pasted from clipboard; continuing after preview wait.", flush=True)
    return True


def attach_image_with_file_input(driver, image_path: Path, timeout: int) -> bool:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        print(f"Comment image not found: {path}", flush=True)
        return False

    print(f"Attaching image to comment: {path}", flush=True)
    file_input = find_file_input(driver)
    if file_input is None:
        click_comment_photo_button(driver)
        try:
            file_input = WebDriverWait(driver, 5).until(
                lambda current_driver: find_file_input(current_driver)
            )
        except TimeoutException:
            file_input = None

    if file_input is None:
        screenshot_path = "comment_image_input_not_found.png"
        driver.save_screenshot(screenshot_path)
        print(f"Could not find a file input for comment image. Saved screenshot: {screenshot_path}", flush=True)
        return False

    try:
        driver.execute_script(
            "arguments[0].style.display='block'; arguments[0].style.visibility='visible'; arguments[0].style.opacity=1;",
            file_input,
        )
    except Exception:
        pass

    try:
        file_input.send_keys(str(path))
    except Exception as exc:
        screenshot_path = "comment_image_upload_failed.png"
        driver.save_screenshot(screenshot_path)
        print(f"Could not upload comment image: {exc}. Saved screenshot: {screenshot_path}", flush=True)
        return False

    # Give Facebook a moment to render the uploaded preview before submission.
    time.sleep(min(max(timeout / 4, 3), 8))
    print("Image attached to comment; continuing after preview wait.", flush=True)
    return True


def attach_image_to_comment(driver, comment_box, image_path: str, timeout: int) -> bool:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        print(f"Comment image not found: {path}", flush=True)
        return False

    attach_mode = os.getenv("COMMENT_IMAGE_ATTACH_MODE", "paste").strip().lower()
    if attach_mode in {"paste", "clipboard", "auto"}:
        if paste_image_from_clipboard(driver, comment_box, path, timeout):
            return True
        if attach_mode in {"paste", "clipboard"}:
            return False

    return attach_image_with_file_input(driver, path, timeout)


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

    print("Clipboard text paste is currently implemented for macOS only.", flush=True)
    return False


def type_comment_text(driver, comment_box, comment_text: str) -> None:
    input_mode = os.getenv("COMMENT_TEXT_INPUT_MODE", "paste").strip().lower()
    if input_mode in {"paste", "clipboard", "auto"} and copy_text_to_clipboard(comment_text):
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", comment_box)
        comment_box.click()
        modifier = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
        ActionChains(driver).key_down(modifier).send_keys("v").key_up(modifier).perform()
        print("Comment text pasted from clipboard.", flush=True)
        return

    if input_mode in {"paste", "clipboard"}:
        raise RuntimeError("Could not paste comment text from clipboard.")

    comment_box.send_keys(comment_text)


def comment_on_post(
    driver,
    post_url: str,
    comment_text: str,
    timeout: int,
    confirm: bool,
    submit: bool,
    image_path: str | None = None,
) -> None:
    print(f"Opening post page: {post_url}", flush=True)
    driver.get(post_url)

    print("Waiting for comment box...", flush=True)
    try:
        ensure_logged_in_before_comment(driver)
    except RuntimeError as exc:
        print(str(exc), flush=True)
        return

    try:
        comment_box = wait_first_clickable(driver, DEFAULT_COMMENT_BOX_LOCATORS, timeout)
    except TimeoutException:
        screenshot_path = "comment_box_not_found.png"
        driver.save_screenshot(screenshot_path)
        print(f"Could not find a comment box. Saved screenshot: {screenshot_path}")
        return

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", comment_box)
    comment_box.click()
    try:
        type_comment_text(driver, comment_box, comment_text)
    except RuntimeError as exc:
        screenshot_path = "comment_text_input_failed.png"
        driver.save_screenshot(screenshot_path)
        print(f"{exc} Saved screenshot: {screenshot_path}", flush=True)
        return
    print(f"Comment text typed: {comment_text!r}", flush=True)

    image_attached = False
    if image_path:
        image_attached = attach_image_to_comment(driver, comment_box, image_path, timeout)
        if not image_attached:
            print("Image was not attached, so the comment was left typed without submitting.", flush=True)
            return

    should_submit = submit
    if confirm and not submit:
        prompt = "Submit this comment now?"
        if image_path:
            prompt += f" Image attached: {'yes' if image_attached else 'no'}."
        answer = input(f"{prompt} Type y and press Enter to submit, or just press Enter to leave it typed: ")
        should_submit = answer.strip().lower() in {"y", "yes"}

    if should_submit:
        comment_box.send_keys(Keys.ENTER)
        print("Comment submitted.", flush=True)
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
    if not skip_login:
        username = required_env("LOGIN_USERNAME")
        password = required_env("LOGIN_PASSWORD")
        validate_credentials(username, password)

    print("Starting Chrome with Selenium...", flush=True)
    driver = build_driver(profile_dir=profile_dir, headless=headless)

    try:
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
                )
            else:
                post_content = extract_post_content(driver, post_url, timeout)
                ensure_logged_in_before_comment(driver)
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

                product_prompt = build_product_scene_image_prompt(
                    post_content=post_content,
                    product_context=product_context,
                    use_cases=product_use_cases,
                    style=image_style,
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
            )
        elif not ai_comment and not ai_product_promo and not generated_image and (post_url or comment_text):
            print("POST_URL and COMMENT_TEXT must both be set to comment on a post.")

    finally:
        if not keep_open:
            driver.quit()


if __name__ == "__main__":
    main()
