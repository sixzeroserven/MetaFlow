from pathlib import Path
import os
import sys

from dotenv import load_dotenv

from openai_content_client import OpenAIContentClient


def masked(value: str) -> str:
    if not value:
        return "<not set>"
    if len(value) <= 8:
        return "<set>"
    return f"{value[:4]}...{value[-4:]}"


def is_placeholder(value: str) -> bool:
    return not value or "你的" in value or "your_" in value.lower()


def main() -> int:
    load_dotenv(".env")

    image_api_key = os.getenv("OPENAI_IMAGE_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    image_base_url = os.getenv("OPENAI_IMAGE_BASE_URL") or "https://api.openai.com/v1"
    image_endpoint_url = os.getenv("OPENAI_IMAGE_ENDPOINT_URL") or ""
    image_model = os.getenv("OPENAI_IMAGE_MODEL") or "gpt-image-2"
    image_api = os.getenv("OPENAI_IMAGE_API") or "images"
    size = os.getenv("OPENAI_IMAGE_SIZE") or "1024x1024"
    quality = os.getenv("OPENAI_IMAGE_QUALITY") or "medium"
    output = os.getenv("IMAGE_OUTPUT") or "generated/test_gpt_image_2.png"

    print("Image API test config:")
    print(f"- OPENAI_IMAGE_API_KEY: {masked(image_api_key)}")
    print(f"- OPENAI_IMAGE_BASE_URL: {image_base_url or '<not set>'}")
    print(f"- OPENAI_IMAGE_ENDPOINT_URL: {image_endpoint_url or '<auto>'}")
    print(f"- OPENAI_IMAGE_MODEL: {image_model}")
    print(f"- OPENAI_IMAGE_API: {image_api}")
    print(f"- OPENAI_IMAGE_SIZE: {size}")
    print(f"- OPENAI_IMAGE_QUALITY: {quality}")
    print(f"- IMAGE_OUTPUT: {output}")

    if is_placeholder(image_api_key) or is_placeholder(image_base_url):
        print("\nPlease fill OPENAI_IMAGE_API_KEY in .env first.")
        print("Example:")
        print("OPENAI_IMAGE_API_KEY=你的官方OpenAI图片Key")
        print("OPENAI_IMAGE_BASE_URL=https://api.openai.com/v1")
        return 2

    prompt = (
        os.getenv("IMAGE_PROMPT")
        or "A cute orange kitten sitting by a sunny window, warm natural light, high quality."
    )
    client = OpenAIContentClient()
    result = client.generate_image(prompt, output, size=size, quality=quality)
    if not result:
        print("\nImage generation failed.")
        print("Last error:")
        print(client.last_error or "<empty response or no image returned>")
        return 1

    path = Path(result)
    print(f"\nSuccess: image saved to {path}")
    print(f"Bytes: {path.stat().st_size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
