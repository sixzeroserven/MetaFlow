import os
import shlex
import subprocess
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def imagegen_cli_enabled() -> bool:
    return env_bool("IMAGEGEN_CLI_ENABLED", False)


def generate_with_imagegen_cli(prompt: str, output_path: str) -> str | None:
    if not imagegen_cli_enabled():
        return None

    script = os.getenv(
        "IMAGEGEN_CLI_SCRIPT",
        "/Users/apple/.codex/skills/.system/imagegen/scripts/image_gen.py",
    )
    env_file = os.getenv("IMAGEGEN_CLI_ENV", "~/.openai_env")
    model = os.getenv("IMAGEGEN_CLI_MODEL", "gpt-image-2")

    script_path = Path(script).expanduser()
    env_path = Path(env_file).expanduser()
    out_path = Path(output_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not script_path.exists():
        print(f"Imagegen CLI script not found: {script_path}", flush=True)
        return None
    if not env_path.exists():
        print(f"Imagegen CLI env file not found: {env_path}", flush=True)
        return None

    command = " ".join(
        [
            "source",
            shlex.quote(str(env_path)),
            "&&",
            "python3",
            shlex.quote(str(script_path)),
            "generate",
            "--model",
            shlex.quote(model),
            "--prompt",
            shlex.quote(prompt),
            "--out",
            shlex.quote(str(out_path)),
        ]
    )

    try:
        result = subprocess.run(
            ["zsh", "-lc", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("IMAGEGEN_CLI_TIMEOUT_SECONDS", "300")),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Imagegen CLI failed to run: {exc}", flush=True)
        return None

    if result.returncode != 0:
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        print(f"Imagegen CLI failed with exit code {result.returncode}: {output[:1200]}", flush=True)
        return None

    if out_path.exists() and out_path.stat().st_size > 0:
        return str(out_path)

    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
    print(f"Imagegen CLI completed but output file was not found: {output[:1200]}", flush=True)
    return None
