import argparse
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv

from auto_login import chrome_proxy_command_args, cleanup_stale_chrome_profile_locks, env_bool, mask_account_value


ROOT = Path(__file__).resolve().parent
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
WORKERS: dict[str, dict] = {}
ACCOUNT_LOCKS: dict[str, threading.Lock] = {}
ACCOUNT_LOCKS_LOCK = threading.Lock()


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MetaFlow 评论控制台</title>
  <style>
    :root {
      --ink: #18221e;
      --muted: #6b756f;
      --line: #d9dfd8;
      --paper: #f7f0df;
      --card: #fffdf6;
      --accent: #1f6a4d;
      --accent-2: #d9683a;
      --gold: #b2872f;
      --good: #1f7a4f;
      --bad: #b63d2f;
      --shadow: 0 26px 80px rgba(29, 39, 32, .16);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 8% 8%, rgba(217,104,58,.20), transparent 28%),
        radial-gradient(circle at 88% 4%, rgba(31,106,77,.18), transparent 30%),
        linear-gradient(135deg, #f5ead2 0%, #fbf8ee 44%, #eaf2e7 100%);
      font-family: "Avenir Next", "Gill Sans", Candara, sans-serif;
      min-height: 100vh;
    }
    .wrap { max-width: 1120px; margin: 0 auto; padding: 36px 18px 48px; }
    .hero {
      display: grid;
      grid-template-columns: 1.1fr .9fr;
      gap: 22px;
      align-items: stretch;
      margin-bottom: 22px;
    }
    .title, .panel {
      background: rgba(255,255,255,.76);
      border: 1px solid rgba(255,255,255,.9);
      box-shadow: var(--shadow);
      border-radius: 28px;
      backdrop-filter: blur(10px);
    }
    .title { padding: 36px; position: relative; overflow: hidden; }
    .title::after {
      content: "";
      position: absolute;
      right: -40px;
      bottom: -70px;
      width: 220px;
      height: 220px;
      background: repeating-linear-gradient(45deg, rgba(36,107,82,.13), rgba(36,107,82,.13) 8px, transparent 8px, transparent 18px);
      border-radius: 50%;
    }
    .kicker { color: var(--accent-2); font-weight: 900; letter-spacing: .14em; text-transform: uppercase; font-size: 12px; margin-bottom: 16px; }
    h1 { font-family: Georgia, Cambria, "Times New Roman", serif; font-size: clamp(38px, 5vw, 68px); line-height: .92; margin: 0 0 18px; letter-spacing: -.055em; }
    .lede { color: var(--muted); font-size: 18px; line-height: 1.55; max-width: 680px; }
    .flow {
      margin-top: 24px;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
      max-width: 720px;
    }
    .flow span {
      background: rgba(255,253,246,.78);
      border: 1px solid rgba(31,106,77,.15);
      border-radius: 16px;
      padding: 12px;
      font-size: 13px;
      font-weight: 800;
    }
    .panel { padding: 24px; }
    label { display: block; font-weight: 850; margin: 18px 0 8px; }
    input[type="url"], input[type="text"], select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #fffdf8;
      color: var(--ink);
      padding: 14px 15px;
      font-size: 15px;
      outline: none;
    }
    input:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 4px rgba(36,107,82,.12); }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .accounts {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-top: 8px;
    }
    .account {
      border: 1px solid rgba(31,106,77,.16);
      background: linear-gradient(160deg, #fffdf8, #f7f3e8);
      border-radius: 20px;
      padding: 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      cursor: pointer;
      transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
    }
    .account:hover { transform: translateY(-2px); border-color: rgba(31,106,77,.42); box-shadow: 0 14px 32px rgba(31,106,77,.12); }
    .account-main { display:flex; align-items:center; gap:10px; min-width:0; }
    .account input { transform: scale(1.1); }
    .account small { color: var(--muted); display: block; margin-top: 4px; font-family: "Avenir Next", "Gill Sans", Candara, sans-serif; }
    .login-badge {
      display:inline-flex;
      align-items:center;
      border-radius:999px;
      padding:2px 7px;
      font-size:11px;
      font-weight:800;
      border:1px solid var(--line);
      background:#fff;
      color:var(--muted);
    }
    .login-badge.logged_in { color:var(--good); border-color:rgba(31,122,79,.45); background:rgba(31,122,79,.08); }
    .login-badge.online { color:var(--good); border-color:rgba(31,122,79,.45); background:rgba(31,122,79,.08); }
    .login-badge.logged_out, .login-badge.no_profile { color:var(--bad); border-color:rgba(182,61,47,.45); background:rgba(182,61,47,.07); }
    .login-badge.offline { color:var(--bad); border-color:rgba(182,61,47,.45); background:rgba(182,61,47,.07); }
    .login-badge.partial, .login-badge.unknown { color:#8a671f; border-color:rgba(150,120,40,.45); background:rgba(150,120,40,.08); }
    .mini { padding: 8px 11px; font-size: 12px; box-shadow: none; white-space: nowrap; }
    .actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 22px; }
    button {
      border: 0;
      border-radius: 999px;
      background: var(--accent);
      color: white;
      padding: 14px 22px;
      font-weight: 800;
      font-size: 15px;
      cursor: pointer;
      box-shadow: 0 12px 22px rgba(36,107,82,.24);
    }
    button.secondary { background: #fffdf8; color: var(--ink); border: 1px solid var(--line); box-shadow: none; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    .hint { color: var(--muted); font-size: 14px; line-height: 1.45; }
    .hint strong { color: var(--ink); }
    .compact-actions { margin-top: 8px; }
    .jobs { margin-top: 22px; display: grid; grid-template-columns: .8fr 1.2fr; gap: 18px; }
    .job-list, .log-box {
      background: rgba(255,253,246,.82);
      border: 1px solid rgba(255,255,255,.9);
      border-radius: 24px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .section-head { padding: 16px 18px; border-bottom: 1px solid var(--line); display:flex; justify-content:space-between; align-items:center; }
    .section-head h2 { margin: 0; font-size: 18px; }
    .job-items { max-height: 460px; overflow: auto; }
    .job {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      cursor: pointer;
    }
    .job:hover { background: rgba(36,107,82,.06); }
    .job.active { background: rgba(36,107,82,.12); }
    .status { display: inline-flex; align-items:center; gap:6px; font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing:.04em; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); display:inline-block; }
    .running .dot { background: var(--accent-2); }
    .completed .dot { background: var(--good); }
    .failed .dot { background: var(--bad); }
    .account-states { margin-top:8px; display:flex; gap:6px; flex-wrap:wrap; }
    .account-state { font-size:12px; border-radius:999px; padding:3px 8px; background:#fffdf8; border:1px solid var(--line); color:var(--muted); }
    .account-state.waiting { color:#6b5b24; border-color:rgba(150,120,40,.45); }
    .account-state.running { color:#a94d21; border-color:rgba(230,107,60,.5); }
    .account-state.completed { color:var(--good); border-color:rgba(31,122,79,.45); }
    .account-state.failed { color:var(--bad); border-color:rgba(182,61,47,.45); }
    .log {
      min-height: 460px;
      max-height: 460px;
      overflow: auto;
      margin: 0;
      padding: 18px;
      background: #17201b;
      color: #e8f1e9;
      font-size: 13px;
      line-height: 1.5;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: pre-wrap;
    }
    .empty { color: var(--muted); padding: 18px; }
    @media (max-width: 820px) {
      .hero, .jobs, .row { grid-template-columns: 1fr; }
      .wrap { padding-top: 18px; }
      .title, .panel { border-radius: 22px; }
    }
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="title">
        <div class="kicker">Worker Queue</div>
        <h1>MetaFlow<br/>任务投递台</h1>
        <p class="lede">把帖子链接交给服务器排队，本地 Worker 会用已登录的账号执行评论；支持纯评论，也支持评论 + 图片。</p>
        <div class="flow">
          <span>1. 填链接</span>
          <span>2. 选账号</span>
          <span>3. Worker 执行</span>
        </div>
      </div>
      <form class="panel" id="taskForm">
        <label for="postUrl">Facebook 帖子链接</label>
        <input id="postUrl" type="url" placeholder="https://www.facebook.com/61550584116226/posts/122291256464019470/" required />

        <label for="productUrl">商品落地页链接</label>
        <input id="productUrl" type="url" placeholder="https://www.seedsunrise.com/products/..." />
        <div class="hint">填写后会优先使用这个商品页生成图片，避免不同账号从 Facebook 页面自动抓错落地页。</div>

        <label for="mode">执行内容</label>
        <select id="mode">
          <option value="ai-product-promo" selected>评论 + 图片</option>
          <option value="ai-comment">只评论</option>
        </select>

        <label>账号</label>
        <div class="actions compact-actions">
          <button type="button" class="secondary" id="selectAll">全选</button>
          <button type="button" class="secondary" id="selectNone">清空</button>
          <span class="hint"><strong>提示：</strong>选择要在服务器上执行的账号。</span>
        </div>
        <div class="accounts" id="accounts"></div>

        <div class="actions">
          <button id="submitBtn" type="submit">提交任务</button>
          <span class="hint">提交后可在下方查看每个账号的执行进度。</span>
        </div>
      </form>
    </section>

    <section class="jobs">
      <div class="job-list">
        <div class="section-head">
          <h2>任务</h2>
          <button type="button" class="secondary" id="refreshJobs">刷新</button>
        </div>
        <div class="job-items" id="jobItems"><div class="empty">还没有任务</div></div>
      </div>
      <div class="log-box">
        <div class="section-head"><h2>日志</h2><span class="hint" id="selectedJob">未选择任务</span></div>
        <pre class="log" id="log">等待任务开始...</pre>
      </div>
    </section>
  </main>

  <script>
    let accounts = [];
    let workers = [];
    let jobs = [];
    let selectedJobId = null;
    let pollTimer = null;
    let accountSelectionInitialized = false;
    let selectedAccountNames = new Set();

    async function api(path, options) {
      const res = await fetch(path, options);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || '请求失败');
      return data;
    }

    function renderAccounts() {
      const box = document.getElementById('accounts');
      if (!accountSelectionInitialized) {
        selectedAccountNames = new Set(accounts.map(acc => acc.name));
        accountSelectionInitialized = true;
      }
      box.innerHTML = accounts.map(acc => {
        const login = acc.login_status || {};
        const checked = selectedAccountNames.has(acc.name) ? 'checked' : '';
        const loginButton = acc.can_login ? `<button type="button" class="secondary mini login-account" data-account="${acc.name}">登录</button>` : '';
        return `
        <div class="account">
          <span class="account-main">
            <input type="checkbox" name="account" value="${acc.name}" ${checked} />
            <span>
              <strong>${acc.name}</strong>
              <small>${acc.username || ''}</small>
              <small><span class="login-badge ${login.state || 'unknown'}">${login.label || login.state || '未知'}</span></small>
            </span>
          </span>
          ${loginButton}
        </div>
      `}).join('') || '<div class="empty">没有读取到账号，请检查 accounts.json</div>';
      box.querySelectorAll('input[name="account"]').forEach(el => {
        el.addEventListener('change', () => {
          if (el.checked) selectedAccountNames.add(el.value);
          else selectedAccountNames.delete(el.value);
        });
      });
      box.querySelectorAll('.login-account').forEach(el => {
        el.addEventListener('click', async (event) => {
          event.preventDefault();
          event.stopPropagation();
          await startAccountLogin(el.dataset.account);
        });
      });
    }

    function selectedAccounts() {
      return Array.from(selectedAccountNames);
    }

    function workerLabel(worker) {
      const status = worker.status || 'offline';
      const accountsText = (worker.accounts || []).join(', ') || '无账号';
      return `${worker.id} · ${status} · ${accountsText}`;
    }

    function renderWorkers() {
      return;
    }

    function renderJobs() {
      const box = document.getElementById('jobItems');
      if (!jobs.length) {
        box.innerHTML = '<div class="empty">还没有任务</div>';
        return;
      }
      box.innerHTML = jobs.map(job => `
        <div class="job ${job.id === selectedJobId ? 'active' : ''}" data-id="${job.id}">
          <div class="status ${job.status}"><span class="dot"></span>${job.status}</div>
          <div style="margin-top:7px;font-weight:800">${job.mode} · ${job.accounts.join(', ')}</div>
          <div class="hint" style="margin-top:4px">${job.post_url}</div>
          ${job.product_url ? `<div class="hint" style="margin-top:4px">商品页：${job.product_url}</div>` : ''}
          <div class="account-states">${Object.entries(job.account_status || {}).map(([name, state]) => `<span class="account-state ${state}">${name}: ${state}</span>`).join('')}</div>
        </div>
      `).join('');
      box.querySelectorAll('.job').forEach(el => {
        el.addEventListener('click', () => selectJob(el.dataset.id));
      });
    }

    async function loadAccounts() {
      const data = await api('/api/accounts');
      accounts = data.accounts || [];
      renderAccounts();
    }

    async function loadWorkers() {
      const data = await api('/api/workers');
      workers = data.workers || [];
      renderWorkers();
    }

    async function loadJobs() {
      const data = await api('/api/jobs');
      jobs = data.jobs || [];
      renderJobs();
    }

    async function startAccountLogin(account) {
      if (!account) return;
      try {
        const job = await api('/api/accounts/' + encodeURIComponent(account) + '/login', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: '{}'
        });
        await loadJobs();
        await selectJob(job.id);
        alert('已在服务器 noVNC 里打开登录浏览器。完成 Facebook 登录后，请关闭那个 Chrome 窗口。');
      } catch (err) {
        alert(err.message);
      }
    }

    async function selectJob(id) {
      selectedJobId = id;
      renderJobs();
      await loadJobLog();
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(loadJobLog, 1500);
    }

    async function loadJobLog() {
      if (!selectedJobId) return;
      const job = await api('/api/jobs/' + selectedJobId);
      document.getElementById('selectedJob').textContent = job.status + ' · ' + job.id.slice(0, 8);
      const log = document.getElementById('log');
      log.textContent = (job.log || []).join('');
      log.scrollTop = log.scrollHeight;
      await loadJobs();
      if (['completed', 'failed'].includes(job.status) && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    document.getElementById('selectAll').addEventListener('click', () => {
      selectedAccountNames = new Set(accounts.map(acc => acc.name));
      renderAccounts();
    });
    document.getElementById('selectNone').addEventListener('click', () => {
      selectedAccountNames = new Set();
      renderAccounts();
    });
    document.getElementById('refreshJobs').addEventListener('click', loadJobs);

    document.getElementById('taskForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const btn = document.getElementById('submitBtn');
      btn.disabled = true;
      try {
        const payload = {
          post_url: document.getElementById('postUrl').value.trim(),
          mode: document.getElementById('mode').value,
          target_worker: 'server',
          product_url: document.getElementById('productUrl').value.trim(),
          accounts: selectedAccounts()
        };
        const job = await api('/api/jobs', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        await loadJobs();
        await selectJob(job.id);
      } catch (err) {
        alert(err.message);
      } finally {
        btn.disabled = false;
      }
    });

    Promise.all([loadAccounts(), loadWorkers()]).then(loadJobs).catch(err => alert(err.message));
    setInterval(loadAccounts, 5000);
    setInterval(loadWorkers, 5000);
  </script>
</body>
</html>
"""


def read_accounts(accounts_file: Path) -> dict:
    if not accounts_file.exists():
        return {}
    data = json.loads(accounts_file.read_text(encoding="utf-8"))
    accounts = data.get("accounts", data) if isinstance(data, dict) else {}
    return accounts if isinstance(accounts, dict) else {}


def parse_env_items(value: str) -> list[str]:
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
    for separator in ("||", "\n", ";", "；"):
        if separator in raw:
            return [item.strip() for item in raw.split(separator) if item.strip()]
    return [raw]


def resolve_profile_dir(profile_dir: str) -> Path | None:
    value = (profile_dir or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path


def chrome_cookie_is_current(expires_utc: int | None) -> bool:
    if not expires_utc:
        return True
    # Chrome stores cookie expiry as microseconds since 1601-01-01 UTC.
    expires_unix = (int(expires_utc) / 1_000_000) - 11644473600
    return expires_unix > time.time()


def facebook_profile_session_status(profile_dir: str) -> dict:
    profile_path = resolve_profile_dir(profile_dir)
    if not profile_path:
        return {"state": "unknown", "label": "未配置", "detail": "没有 profile_dir"}
    if not profile_path.exists():
        return {"state": "no_profile", "label": "未登录", "detail": "未创建浏览器资料"}

    cookie_paths = [
        profile_path / "Default" / "Cookies",
        profile_path / "Cookies",
        *(path for path in profile_path.glob("*/Cookies") if path.name == "Cookies"),
    ]
    cookie_db = next((path for path in cookie_paths if path.exists()), None)
    if not cookie_db:
        return {"state": "logged_out", "label": "未登录", "detail": "未找到 Cookie"}

    try:
        conn = sqlite3.connect(f"file:{cookie_db}?mode=ro", uri=True, timeout=1)
        try:
            rows = conn.execute(
                """
                select name, expires_utc
                from cookies
                where host_key like '%facebook.com'
                  and name in ('c_user', 'xs')
                """
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {"state": "unknown", "label": "检测中", "detail": "Cookie 正在被 Chrome 占用"}

    valid_names = {str(name) for name, expires_utc in rows if chrome_cookie_is_current(expires_utc)}
    if {"c_user", "xs"}.issubset(valid_names):
        return {"state": "logged_in", "label": "已登录", "detail": "已保存 Facebook 会话"}
    if valid_names:
        return {"state": "partial", "label": "可能过期", "detail": "会话 Cookie 不完整"}
    return {"state": "logged_out", "label": "未登录", "detail": "未保存 Facebook 会话"}


def manual_login_chrome_command(account: str, accounts_file: Path) -> tuple[list[str], Path]:
    accounts = read_accounts(accounts_file)
    config = accounts.get(account)
    if not isinstance(config, dict):
        raise ValueError(f"账号不存在: {account}")

    profile_dir = str(config.get("profile_dir") or config.get("CHROME_PROFILE_DIR") or "").strip()
    profile_path = resolve_profile_dir(profile_dir)
    if not profile_path:
        raise ValueError(f"账号 {account} 没有配置 profile_dir。")
    profile_path.mkdir(parents=True, exist_ok=True)
    cleanup_stale_chrome_profile_locks(profile_path)

    chrome_binary = os.getenv("CHROME_BINARY", "").strip() or "chromium"
    cmd = [
        chrome_binary,
        "--start-maximized",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile_path}",
    ]
    cmd.extend(parse_env_items(os.getenv("CHROME_EXTRA_ARGS", "")))
    cmd.extend(chrome_proxy_command_args())
    cmd.append("https://www.facebook.com/login")
    return cmd, profile_path


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def store_and_start_job(job: dict) -> dict:
    with JOBS_LOCK:
        JOBS[job["id"]] = job
    thread = threading.Thread(target=run_job, args=(job["id"],), daemon=True)
    thread.start()
    return job


def store_job(job: dict) -> dict:
    with JOBS_LOCK:
        JOBS[job["id"]] = job
    return job


def public_job(job: dict, include_log: bool = False) -> dict:
    hidden = set() if include_log else {"log"}
    return {key: value for key, value in job.items() if key not in hidden}


def new_job(payload: dict, accounts_file: Path, env_file: str, skip_login: bool) -> dict:
    all_accounts = read_accounts(accounts_file)
    selected = payload.get("accounts") or list(all_accounts.keys())
    if not selected:
        raise ValueError("请选择至少一个账号。")
    unknown = [name for name in selected if name not in all_accounts]
    if unknown:
        raise ValueError(f"账号不存在: {', '.join(unknown)}")

    post_url = (payload.get("post_url") or "").strip()
    if not post_url.startswith(("http://", "https://")):
        raise ValueError("请输入有效的帖子链接。")

    mode = payload.get("mode") or "ai-product-promo"
    if mode not in {"ai-comment", "ai-product-promo"}:
        raise ValueError("执行模式不正确。")
    target_worker = "server"
    product_url = (payload.get("product_url") or "").strip()
    if product_url and not product_url.startswith(("http://", "https://")):
        raise ValueError("商品落地页链接必须以 http:// 或 https:// 开头。")

    job_id = uuid.uuid4().hex
    tasks = []
    for name in selected:
        tasks.append(
            {
                "id": uuid.uuid4().hex,
                "job_id": job_id,
                "account": name,
                "status": "queued",
                "worker_id": "",
                "created_at": time.time(),
            }
        )

    job = {
        "id": job_id,
        "status": "queued",
        "created_at": time.time(),
        "post_url": post_url,
        "mode": mode,
        "accounts": selected,
        "target_worker": target_worker,
        "account_status": {name: "queued" for name in selected},
        "product_url": product_url,
        "env_file": env_file,
        "accounts_file": str(accounts_file),
        "skip_login": skip_login,
        "tasks": tasks,
        "log": [],
    }
    append_log(job, "Job queued. Waiting for a matching worker to pick up selected account tasks.\n")
    if target_worker:
        append_log(job, f"Target worker: {target_worker}\n")
    append_log(job, "Start a worker on a trusted local machine with: python worker.py --server <server-url>\n")
    return store_job(job)


def new_login_job(account: str, accounts_file: Path, env_file: str) -> dict:
    all_accounts = read_accounts(accounts_file)
    if account not in all_accounts:
        raise ValueError(f"账号不存在: {account}")

    job = {
        "id": uuid.uuid4().hex,
        "status": "queued",
        "created_at": time.time(),
        "post_url": "",
        "mode": "account-login",
        "accounts": [account],
        "target_worker": "",
        "account_status": {account: "queued"},
        "product_url": "",
        "env_file": env_file,
        "accounts_file": str(accounts_file),
        "skip_login": True,
        "log": [],
    }
    return store_and_start_job(job)


def append_log(job: dict, line: str) -> None:
    with JOBS_LOCK:
        job["log"].append(line)
        if len(job["log"]) > 3000:
            job["log"] = job["log"][-3000:]


def update_job_status(job: dict) -> None:
    tasks = job.get("tasks") or []
    if not tasks:
        return
    statuses = [task.get("status") for task in tasks]
    job["account_status"] = {task["account"]: task.get("status", "queued") for task in tasks}
    if any(status == "running" for status in statuses):
        job["status"] = "running"
    elif any(status == "queued" for status in statuses):
        job["status"] = "queued"
    elif any(status == "failed" for status in statuses):
        job["status"] = "failed"
        job["finished_at"] = time.time()
    else:
        job["status"] = "completed"
        job["finished_at"] = time.time()


def find_task_locked(task_id: str) -> tuple[dict | None, dict | None]:
    for job in JOBS.values():
        for task in job.get("tasks") or []:
            if task.get("id") == task_id:
                return job, task
    return None, None


def worker_token_ok(handler: BaseHTTPRequestHandler) -> bool:
    expected = os.getenv("WORKER_TOKEN", "").strip()
    if not expected:
        return True
    return handler.headers.get("X-Worker-Token", "") == expected


def read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8") or "{}")


def claim_worker_task(worker_id: str, payload: dict) -> dict:
    accounts = [str(account) for account in payload.get("accounts") or [] if str(account).strip()]
    now = time.time()
    with JOBS_LOCK:
        WORKERS[worker_id] = {
            "id": worker_id,
            "last_seen": now,
            "accounts": accounts,
            "hostname": str(payload.get("hostname") or ""),
            "version": str(payload.get("version") or ""),
        }
        for job in sorted(JOBS.values(), key=lambda item: item.get("created_at", 0)):
            if job.get("mode") == "account-login":
                continue
            target_worker = str(job.get("target_worker") or "").strip()
            if target_worker and target_worker != worker_id:
                continue
            for task in job.get("tasks") or []:
                if task.get("status") != "queued" or task.get("account") not in accounts:
                    continue
                task["status"] = "running"
                task["worker_id"] = worker_id
                task["claimed_at"] = now
                update_job_status(job)
                job["log"].append(f"[{task['account']}] Claimed by worker {worker_id}.\n")
                return {
                    "task": {
                        "id": task["id"],
                        "job_id": job["id"],
                        "account": task["account"],
                        "post_url": job["post_url"],
                        "mode": job["mode"],
                        "product_url": job.get("product_url", ""),
                    }
                }
    return {"task": None}


def append_task_log(task_id: str, line: str) -> bool:
    with JOBS_LOCK:
        job, task = find_task_locked(task_id)
        if not job or not task:
            return False
        prefix = f"[{task.get('account', '?')}] "
        job["log"].append(prefix + line)
        if len(job["log"]) > 3000:
            job["log"] = job["log"][-3000:]
        return True


def finish_task(task_id: str, payload: dict) -> bool:
    with JOBS_LOCK:
        job, task = find_task_locked(task_id)
        if not job or not task:
            return False
        status = str(payload.get("status") or "failed")
        task["status"] = "completed" if status == "completed" else "failed"
        task["finished_at"] = time.time()
        task["exit_code"] = payload.get("exit_code")
        message = str(payload.get("message") or "").strip()
        if message:
            task["message"] = message
            job["log"].append(f"[{task['account']}] {message}\n")
        job["log"].append(f"[{task['account']}] Task finished: {task['status']}\n")
        update_job_status(job)
        return True


def account_lock(account: str) -> threading.Lock:
    with ACCOUNT_LOCKS_LOCK:
        if account not in ACCOUNT_LOCKS:
            ACCOUNT_LOCKS[account] = threading.Lock()
        return ACCOUNT_LOCKS[account]


def run_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["status"] = "running"
        job["started_at"] = time.time()

    append_log(job, f"Job {job_id} started\n")
    append_log(job, f"Mode: {job['mode']}\n")
    append_log(job, f"Accounts: {', '.join(job['accounts'])}\n")
    if job["post_url"]:
        append_log(job, f"Post URL: {job['post_url']}\n\n")
    else:
        append_log(job, "\n")
    if job.get("product_url"):
        append_log(job, f"Product URL override: {job['product_url']}\n\n")
    if job["skip_login"]:
        append_log(
            job,
            "Note: The task uses the saved Chrome profile. If Facebook asks for login/verification, finish it in the opened Chrome/noVNC window.\n\n",
        )

    failed = False
    for index, account in enumerate(job["accounts"], start=1):
        image_output = f"generated/web_{job_id[:8]}_{account}.png"
        if job["mode"] == "account-login":
            accounts_file = Path(job.get("accounts_file") or ROOT / "accounts.json").expanduser()
            if not accounts_file.is_absolute():
                accounts_file = ROOT / accounts_file
            cmd, profile_path = manual_login_chrome_command(account, accounts_file)
        else:
            cmd = [
                sys.executable,
                str(ROOT / "auto_login.py"),
                "--env",
                job["env_file"],
                "--account",
                account,
            ]
            cmd.extend(["--post-url", job["post_url"]])
            if job["skip_login"]:
                cmd.append("--skip-login")
                cmd.append("--wait-login-if-needed")

        if job["mode"] == "ai-product-promo":
            cmd.extend(["--ai-product-promo", "--image-output", image_output])
            if job["product_url"]:
                cmd.extend(["--product-url", job["product_url"]])
        elif job["mode"] == "ai-comment":
            cmd.append("--ai-comment")

        append_log(job, f"===== [{index}/{len(job['accounts'])}] {account} =====\n")
        append_log(job, "Command: " + " ".join(cmd) + "\n")
        with JOBS_LOCK:
            job["account_status"][account] = "waiting"
        append_log(job, f"Waiting for account profile lock: {account}\n")

        lock = account_lock(account)
        with lock:
            append_log(job, f"Account profile lock acquired: {account}\n")
            with JOBS_LOCK:
                job["account_status"][account] = "running"
            account_failed = False
            try:
                if job["mode"] == "account-login":
                    subprocess.Popen(
                        cmd,
                        cwd=str(ROOT),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        env={**os.environ, "KEEP_BROWSER_OPEN": "true"},
                        start_new_session=True,
                    )
                    append_log(
                        job,
                        "\nOpened a normal Chromium window for manual Facebook login.\n"
                        f"Chrome profile directory: {profile_path}\n"
                        "Finish login in noVNC until Facebook shows the home/feed page.\n"
                        "Do not stop on a two-step/checkpoint page. After the home/feed page loads, close that Chrome window.\n"
                        "The account card refreshes every few seconds; the status should become 已登录 once c_user/xs cookies are saved.\n\n",
                    )
                    with JOBS_LOCK:
                        job["account_status"][account] = "completed"
                    continue

                process = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    env={**os.environ, "KEEP_BROWSER_OPEN": "false"},
                    text=True,
                    bufsize=1,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    append_log(job, line)
                code = process.wait()
                append_log(job, f"\nAccount {account} exited with code {code}\n\n")
                if code != 0:
                    account_failed = True
                    failed = True
                    with JOBS_LOCK:
                        job["account_status"][account] = "failed"
                else:
                    with JOBS_LOCK:
                        job["account_status"][account] = "completed"
            except Exception as exc:
                account_failed = True
                failed = True
                with JOBS_LOCK:
                    job["account_status"][account] = "failed"
                append_log(job, f"Failed to run account {account}: {exc}\n\n")
            finally:
                append_log(job, f"Account profile lock released: {account}\n")

        if account_failed:
            append_log(job, f"Account {account} failed; continuing with remaining accounts if any.\n\n")
        if index < len(job["accounts"]):
            append_log(job, "Waiting 5 seconds before next account...\n\n")
            time.sleep(5)

    with JOBS_LOCK:
        job["status"] = "failed" if failed else "completed"
        job["finished_at"] = time.time()
    append_log(job, f"Job finished: {job['status']}\n")


class MetaFlowHandler(BaseHTTPRequestHandler):
    accounts_file: Path = ROOT / "accounts.json"
    env_file: str = ".env"
    skip_login: bool = True

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return text_response(self, 200, INDEX_HTML)
        if parsed.path == "/api/accounts":
            accounts = read_accounts(self.accounts_file)
            items = []
            now = time.time()
            with JOBS_LOCK:
                workers = list(WORKERS.values())
            for name, config in accounts.items():
                if not isinstance(config, dict):
                    continue
                profile_dir = str(config.get("profile_dir") or config.get("CHROME_PROFILE_DIR") or "")
                matching_workers = [
                    worker
                    for worker in workers
                    if name in (worker.get("accounts") or []) and now - float(worker.get("last_seen") or 0) < 45
                ]
                items.append(
                    {
                        "name": name,
                        "username": mask_account_value(str(config.get("username") or config.get("LOGIN_USERNAME") or "")),
                        "profile_dir": profile_dir,
                        "login_status": facebook_profile_session_status(profile_dir),
                        "worker_status": {
                            "state": "online" if matching_workers else "offline",
                            "label": ", ".join(worker["id"] for worker in matching_workers) if matching_workers else "离线",
                        },
                        "can_login": env_bool("SERVER_ENABLE_BROWSER_LOGIN", False),
                    }
                )
            return json_response(self, 200, {"accounts": items})
        if parsed.path == "/api/workers":
            now = time.time()
            with JOBS_LOCK:
                workers = [
                    {
                        **worker,
                        "status": "online" if now - float(worker.get("last_seen") or 0) < 45 else "offline",
                    }
                    for worker in WORKERS.values()
                ]
            return json_response(self, 200, {"workers": workers})
        if parsed.path == "/api/jobs":
            with JOBS_LOCK:
                jobs = [
                    public_job(job)
                    for job in sorted(JOBS.values(), key=lambda item: item["created_at"], reverse=True)
                ]
            return json_response(self, 200, {"jobs": jobs})
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    return json_response(self, 404, {"error": "任务不存在。"})
                payload = public_job(job, include_log=True)
            return json_response(self, 200, payload)
        return json_response(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]

        if len(parts) == 4 and parts[:2] == ["api", "workers"] and parts[3] == "claim":
            if not worker_token_ok(self):
                return json_response(self, 403, {"error": "Invalid worker token."})
            try:
                worker_id = unquote(parts[2])
                payload = read_json_body(self)
                return json_response(self, 200, claim_worker_task(worker_id, payload))
            except Exception as exc:
                return json_response(self, 400, {"error": str(exc)})

        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] in {"log", "result"}:
            if not worker_token_ok(self):
                return json_response(self, 403, {"error": "Invalid worker token."})
            try:
                task_id = unquote(parts[2])
                payload = read_json_body(self)
                if parts[3] == "log":
                    ok = append_task_log(task_id, str(payload.get("line") or ""))
                else:
                    ok = finish_task(task_id, payload)
                if not ok:
                    return json_response(self, 404, {"error": "Task not found."})
                return json_response(self, 200, {"ok": True})
            except Exception as exc:
                return json_response(self, 400, {"error": str(exc)})

        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/login"):
            try:
                account = unquote(parts[2]) if len(parts) >= 3 else ""
                job = new_login_job(account, self.accounts_file, self.env_file)
                return json_response(self, 201, public_job(job))
            except Exception as exc:
                return json_response(self, 400, {"error": str(exc)})

        if parsed.path != "/api/jobs":
            return json_response(self, 404, {"error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            job = new_job(payload, self.accounts_file, self.env_file, self.skip_login)
            return json_response(self, 201, public_job(job))
        except Exception as exc:
            return json_response(self, 400, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MetaFlow local web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind. Defaults to 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind. Defaults to 8765")
    parser.add_argument("--accounts-file", default="accounts.json", help="Accounts JSON path.")
    parser.add_argument("--env", default=".env", help="Env file passed to auto_login.py.")
    parser.add_argument("--no-skip-login", action="store_true", help="Do not force --skip-login when running tasks.")
    args = parser.parse_args()

    load_dotenv(args.env)
    handler = MetaFlowHandler
    handler.accounts_file = Path(args.accounts_file).expanduser()
    if not handler.accounts_file.is_absolute():
        handler.accounts_file = ROOT / handler.accounts_file
    handler.env_file = args.env
    handler.skip_login = not args.no_skip_login

    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"MetaFlow web UI running at http://{args.host}:{args.port}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web UI...", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
