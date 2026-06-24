import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, request

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
WORKER_VERSION = "worker-queue-2026-06-17"


def read_accounts(accounts_file: Path) -> list[str]:
    if not accounts_file.exists():
        raise FileNotFoundError(f"Accounts file not found: {accounts_file}")
    data = json.loads(accounts_file.read_text(encoding="utf-8"))
    accounts = data.get("accounts", data) if isinstance(data, dict) else {}
    if not isinstance(accounts, dict):
        raise ValueError("accounts.json must contain an object or an 'accounts' object.")
    return [str(name) for name in accounts.keys()]


def post_json(server: str, path: str, payload: dict, token: str = "") -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["X-Worker-Token"] = token
    req = request.Request(server.rstrip("/") + path, data=body, headers=headers, method="POST")
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def send_task_log(server: str, task_id: str, line: str, token: str = "") -> None:
    try:
        post_json(server, f"/api/tasks/{task_id}/log", {"line": line}, token=token)
    except Exception:
        # Keep local execution moving even if transient log upload fails.
        pass


def finish_task(server: str, task_id: str, status: str, message: str, exit_code: int | None, token: str = "") -> None:
    post_json(
        server,
        f"/api/tasks/{task_id}/result",
        {"status": status, "message": message, "exit_code": exit_code},
        token=token,
    )


def build_command(task: dict, env_file: str) -> list[str]:
    account = task["account"]
    post_url = task["post_url"]
    mode = task["mode"]
    cmd = [
        sys.executable,
        str(ROOT / "auto_login.py"),
        "--env",
        env_file,
        "--account",
        account,
        "--post-url",
        post_url,
        "--skip-login",
        "--wait-login-if-needed",
    ]

    if mode == "ai-comment":
        cmd.append("--ai-comment")
    elif mode == "ai-product-promo":
        image_output = f"generated/worker_{task['id'][:8]}_{account}.png"
        cmd.extend(["--ai-product-promo", "--image-output", image_output])
        if task.get("product_url"):
            cmd.extend(["--product-url", task["product_url"]])
    else:
        raise ValueError(f"Unsupported task mode: {mode}")
    return cmd


def run_task(server: str, task: dict, env_file: str, token: str = "") -> None:
    task_id = task["id"]
    cmd = build_command(task, env_file)
    command_line = " ".join(shlex.quote(part) for part in cmd)
    print(f"Running task {task_id}: {command_line}", flush=True)
    send_task_log(server, task_id, f"Worker command: {command_line}\n", token=token)

    process = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        send_task_log(server, task_id, line, token=token)
    code = process.wait()
    if code == 0:
        finish_task(server, task_id, "completed", "Worker completed task successfully.", code, token=token)
    else:
        finish_task(server, task_id, "failed", f"Worker command exited with code {code}.", code, token=token)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local MetaFlow worker that executes queued Facebook tasks.")
    parser.add_argument("--server", default=os.getenv("METAFLOW_SERVER", "http://127.0.0.1:8765"), help="MetaFlow server URL.")
    parser.add_argument("--worker-id", default=os.getenv("WORKER_ID") or socket.gethostname(), help="Worker ID shown in the server UI.")
    parser.add_argument("--accounts-file", default=os.getenv("WORKER_ACCOUNTS_FILE", "accounts.json"), help="Local accounts JSON.")
    parser.add_argument("--accounts", default=os.getenv("WORKER_ACCOUNTS", ""), help="Comma-separated account names handled by this worker.")
    parser.add_argument("--env", default=os.getenv("WORKER_ENV_FILE", ".env"), help="Env file passed to auto_login.py.")
    parser.add_argument("--poll-seconds", type=float, default=float(os.getenv("WORKER_POLL_SECONDS", "5")), help="Polling interval.")
    parser.add_argument("--token", default=os.getenv("WORKER_TOKEN", ""), help="Optional shared worker token.")
    args = parser.parse_args()

    load_dotenv(args.env)
    accounts_file = Path(args.accounts_file).expanduser()
    if not accounts_file.is_absolute():
        accounts_file = ROOT / accounts_file
    accounts = [item.strip() for item in args.accounts.split(",") if item.strip()] if args.accounts else read_accounts(accounts_file)
    if not accounts:
        raise ValueError("No worker accounts configured.")

    print(f"MetaFlow worker {args.worker_id} starting ({WORKER_VERSION}).", flush=True)
    print(f"Server: {args.server.rstrip('/')}", flush=True)
    print(f"Accounts: {', '.join(accounts)}", flush=True)

    while True:
        try:
            response = post_json(
                args.server,
                f"/api/workers/{args.worker_id}/claim",
                {
                    "accounts": accounts,
                    "hostname": socket.gethostname(),
                    "version": WORKER_VERSION,
                },
                token=args.token,
            )
            task = response.get("task")
            if task:
                run_task(args.server, task, args.env, token=args.token)
            else:
                time.sleep(max(1.0, args.poll_seconds))
        except KeyboardInterrupt:
            print("\nWorker stopped.", flush=True)
            return
        except (error.URLError, TimeoutError) as exc:
            print(f"Server connection error: {exc}", flush=True)
            time.sleep(max(3.0, args.poll_seconds))
        except Exception as exc:
            print(f"Worker error: {exc}", flush=True)
            time.sleep(max(3.0, args.poll_seconds))


if __name__ == "__main__":
    main()
