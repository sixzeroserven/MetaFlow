# MetaFlow Selenium + OpenAI 自动化示例

这个示例会打开登录页、填写账号和密码，并提交表单；也可以读取 Facebook 帖子内容，调用 OpenAI 生成评论草稿或图片。遇到验证码、双重验证、设备确认等安全校验时，需要手动完成，脚本不会绕过这些验证。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

```bash
cp .env.example .env
```

然后编辑 `.env`：

```env
LOGIN_USERNAME=你的邮箱或手机号
LOGIN_PASSWORD=你的密码
```

你提供的页面元素对应的选择器已经写好：

```env
USERNAME_SELECTOR=input[name="email"]
PASSWORD_SELECTOR=input[name="pass"]
SUBMIT_SELECTOR=div[role="button"][aria-label="登录"]
```

默认使用密码框回车提交，通常比点击复杂的 `div[role="button"]` 更稳定。

## 运行

```bash
python auto_login.py
```

如果页面要求验证码、短信验证或设备确认，请在打开的浏览器里手动完成，然后回到终端按 Enter。

如果你已经通过 `CHROME_PROFILE_DIR` 保存了登录状态，可以跳过登录页：

```bash
python auto_login.py --skip-login
```

## 可视化前端

可以启动本地 Web 控制台，通过页面输入 Facebook 帖子链接并选择账号执行。默认会选择全部账号，默认模式是“评论 + 图片”。

```bash
source .venv/bin/activate
python web_app.py
```

然后打开：

```text
http://127.0.0.1:8765
```

页面支持：

- 输入帖子链接，例如 `https://www.facebook.com/61550584116226/posts/122291256464019470/`
- 默认勾选所有 `accounts.json` 里的账号
- 也可以只勾选指定账号
- 选择“评论 + 图片”或“只生成评论”
- 可选填写产品链接，适合帖子里识别不到落地页链接时使用
- 在页面里查看每个账号的执行日志

前端后台实际会顺序调用现有脚本，例如：

```bash
python auto_login.py --account account2 --post-url "帖子链接" --ai-product-promo --skip-login
```

为了避免在 Web 任务里卡住终端输入，`web_app.py` 默认会给任务加 `--skip-login`，也就是优先使用已保存的 Chrome 登录状态。同时也会加 `--wait-login-if-needed`：如果某个账号已经退出登录，浏览器会停住等你手动登录，检测到登录成功后会自动回到帖子继续执行。

如果你确实要让 Web 任务走账号密码登录流程，可以启动时加：

```bash
python web_app.py --no-skip-login
```

也可以指定端口：

```bash
python web_app.py --port 8888
```

## Docker 部署

Docker 版本会同时启动 Web 控制台、Chrome 图形环境和 noVNC。用户第一次登录账号时，可以通过浏览器访问 noVNC 来操作容器里的 Chrome。

1. 准备配置文件：

```bash
cp .env.example .env
cp accounts.docker.example.json accounts.json
mkdir -p chrome-profiles generated output
```

2. 编辑 `accounts.json`，每个账号使用独立的 `profile_dir`：

```json
{
  "accounts": {
    "main": {
      "username": "your_email_or_phone",
      "password": "your_password",
      "profile_dir": "./chrome-profiles/main",
      "skip_login": true
    }
  }
}
```

3. 启动：

```bash
docker compose up -d --build
```

4. 打开控制台：

```text
http://127.0.0.1:8765
```

5. 第一次登录账号：

- 先打开 noVNC：`http://127.0.0.1:7900/vnc.html`
- 回到控制台，点击某个账号旁边的“登录”
- 在 noVNC 里的 Chrome 完成 Facebook 登录/验证
- 登录状态会保存到 `./chrome-profiles/<account>`，后续任务会复用

6. 平时执行：

- 输入 Facebook 帖子链接
- 选择账号
- 选择“评论 + 图片”或“只生成评论”
- 点击“开始执行”

注意：

- 不建议定时自动重新登录；更稳的是掉线后点“登录”重新验证一次。
- 多账号会顺序执行，不会同时打开多个账号浏览器，避免抢 profile 和触发异常。
- Docker 里建议账号的 `profile_dir` 都放在 `./chrome-profiles/...`，这样重启容器后登录状态还在。
- 如果你本机 `.env` 里有 `HTTP_PROXY=http://127.0.0.1:xxxx`，容器里不能直接用这个地址；Docker 代理请改用 `DOCKER_HTTP_PROXY=http://host.docker.internal:xxxx` 和 `DOCKER_HTTPS_PROXY=http://host.docker.internal:xxxx`。
- 如果服务器外网访问必须走代理，需要同时配置 `DOCKER_HTTP_PROXY` / `DOCKER_HTTPS_PROXY` 和 `CHROME_PROXY_*`。前者让 Python 请求走代理，后者让容器里的 Chrome/Facebook 访问走代理；带用户名密码的代理请用 `CHROME_PROXY_USERNAME` / `CHROME_PROXY_PASSWORD`。
- 如果部署到服务器，把 `8765` 作为平台端口，把 `7900` 作为远程登录浏览器端口，并做好访问权限控制。

## 登录后评论

可以用命令行参数打开指定帖子，并输入评论：

```bash
python auto_login.py --post-url "https://www.facebook.com/61550584116226/posts/122290195820019470/" --comment "good"
```

脚本会先登录；如果出现验证码、双重验证或设备确认，请手动完成，然后回到终端按 Enter。之后脚本会打开帖子、输入 `good`，默认直接提交。

如果想在命令里显式声明直接提交，也可以加上 `--submit-comment`：

```bash
python auto_login.py --skip-login --post-url "https://www.facebook.com/61550584116226/posts/122290195820019470/" --comment "good" --submit-comment
```

如果只想填好但不发布，可以在 `.env` 里改成 `SUBMIT_COMMENT=false`，并按需把 `CONFIRM_BEFORE_COMMENT=true` 打开。

也可以写到 `.env`：

```env
POST_URL=https://www.facebook.com/61550584116226/posts/122290195820019470/
COMMENT_TEXT=good
COMMENT_IMAGE=
COMMENT_TEXT_INPUT_MODE=paste
CONFIRM_BEFORE_COMMENT=false
SUBMIT_COMMENT=true
```

如果要在评论里附加本地图片：

```bash
python auto_login.py --skip-login --post-url "https://www.facebook.com/61550584116226/posts/122290195820019470/" --comment "good" --comment-image generated/product_scene.png
```

默认使用 Facebook 评论框的图片/相机按钮上传，并等待图片预览/上传状态稳定：

```env
COMMENT_IMAGE_ATTACH_MODE=file
```

如果你想改回先尝试剪贴板粘贴、失败再回退上传，可以改成：

```env
COMMENT_IMAGE_ATTACH_MODE=auto
```

## 获取帖子内容并用 AI 生成评论草稿

脚本参考 `shoplazza-blacklist-service/jobs/sync_paypal_disputes.py` 的 AI 争议归因模式，使用独立 OpenAI-compatible client：

- 通过环境变量控制是否启用、base URL、模型、wire API 和超时。
- 支持 `responses` 与 `chat_completions` 两种文本接口形态。
- 文本输出要求 JSON，再做本地解析和规范化。
- 请求失败会短暂重试，避免一次网络抖动直接中断。

脚本支持先读取帖子页面里可见的文本内容，再调用 OpenAI 生成一条评论草稿。默认会把 AI 评论填入评论框后直接提交，不再二次询问。

先在 `.env` 里配置评论/文本接口。你现在的推荐方式是评论走中转站：

```env
AI_ATTRIBUTION_API_KEY=你的中转站Key
AI_ATTRIBUTION_BASE_URL=http://47.253.224.99:3000/openai
AI_ATTRIBUTION_WIRE_API=responses
AI_ATTRIBUTION_MODEL=gpt-5.5
AI_COMMENT_LANGUAGE=the same language as the post
AI_COMMENT_STYLE=更随意一点，像平时聊天；可以自然用 it / this / that，不用刻意说产品名；不要用 The/the 开头；不要直接使用链接或标题里的关键词；偏好“好看 & 实用 / pretty & useful / cute & practical”这种短而有力的评价；表情由程序本地追加；别写成 slogan
AI_COMMENT_EMOJI_ENABLED=true
AI_COMMENT_EMOJI_MODE=heart
AI_COMMENT_EXPERIENCE_NOTES=
AI_COMMENT_ANGLES=好看 & 实用||pretty & useful||cute & practical||nice & easy||looks handy||I'd put it by the door||my mom would like this||家里人应该会喜欢||我会放门口||放阳台应该挺顺眼
```

`AI_COMMENT_EMOJI_MODE=heart` 会随机使用红心/爱心类表情，例如 `❤ ♥ ♡`。如果你确认现代 emoji 在 Facebook 粘贴正常，可以改成 `AI_COMMENT_EMOJI_MODE=modern`，会使用 `😊 😍 🥰 😂 🙌 ❤️` 这类更情绪化的表情。

`AI_COMMENT_ANGLES` 可以补充随机评论方向，用 `||` 分隔，例如：

```env
AI_COMMENT_ANGLES=好看 & 实用||pretty & useful||cute & practical||nice & easy||looks handy||I'd put it by the door||my mom would like this||家里人应该会喜欢
```

如果要写“收到实物”“用起来方便”“朋友/亲人夸了”这类亲历内容，请只把真实发生过的素材写进 `AI_COMMENT_EXPERIENCE_NOTES`；为空时脚本会避免编造已购买、已收到或亲友已夸的内容。

如果你想让评论也走官方 OpenAI，也可以用：

```env
OPENAI_API_KEY=你的OpenAI_API_Key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_WIRE_API=responses
OPENAI_TEXT_MODEL=gpt-5.5
```

如果你使用的是 `shoplazza-blacklist-service` 同款中转站配置，也可以直接写：

```env
AI_ATTRIBUTION_ENABLED=true
AI_ATTRIBUTION_API_KEY=你的中转站Key
AI_ATTRIBUTION_BASE_URL=http://47.253.224.99:3000/openai
AI_ATTRIBUTION_MODEL=gpt-5.5
AI_ATTRIBUTION_WIRE_API=responses
AI_ATTRIBUTION_TIMEOUT_MS=30000
OPENAI_IMAGE_MODEL=gpt-image-2
```

当前项目会自动把 `AI_ATTRIBUTION_*` 作为 OpenAI-compatible 配置读取。

然后运行：

```bash
python auto_login.py --skip-login --post-url "https://www.facebook.com/61550584116226/posts/122290195820019470/" --ai-comment
```

脚本会：

1. 使用当前 `CHROME_PROFILE_DIR` 登录状态打开帖子。
2. 提取页面中可见的帖子文本。
3. 调用 OpenAI 生成一条简短、贴合上下文的评论草稿。
4. 把草稿填入评论框。
5. 询问你是否提交。

看到提示后，输入 `y` 才会提交；直接按 Enter 会保留在输入框里但不发送。

## 生成图片

可以直接用提示词生成图片，不需要打开浏览器：

```bash
python auto_login.py --image-prompt "A clean editorial illustration of a small business preparing a thoughtful social media update" --image-output generated/example.png
```

也可以先读取帖子内容，再根据帖子生成配图：

```bash
python auto_login.py --skip-login --post-url "https://www.facebook.com/61550584116226/posts/122290195820019470/" --ai-image-from-post --image-output generated/post_image.png
```

图片相关配置。图片生成走官方 OpenAI Images API，不走中转站：

```env
OPENAI_IMAGE_API_KEY=你的官方OpenAI图片Key
OPENAI_IMAGE_BASE_URL=https://api.openai.com/v1
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_EDIT_MODEL=gpt-image-2
OPENAI_IMAGE_API=images
OPENAI_IMAGE_ENDPOINT_URL=
OPENAI_IMAGE_ENDPOINT_PATH=/images/generations
OPENAI_IMAGE_EDIT_ENDPOINT_URL=
OPENAI_IMAGE_EDIT_ENDPOINT_PATH=/images/edits
OPENAI_IMAGE_REFERENCE_ENABLED=true
OPENAI_IMAGE_REFERENCE_FIELD=image[]
OPENAI_IMAGE_REFERENCE_LIMIT=2
OPENAI_IMAGE_SIZE=1024x1024
OPENAI_IMAGE_QUALITY=medium
AI_IMAGE_STYLE=exact landing-page product match, new background, realistic customer phone photo, no people, no background blur, casual lived-in setting, natural light
IMAGE_OUTPUT=generated/post_image.png
```

如果中转站图片接口不是 OpenAI 标准 `/images/generations`，可以改成自定义端点：

```env
OPENAI_IMAGE_API=custom
OPENAI_IMAGE_MODEL=你的图片模型名
OPENAI_IMAGE_ENDPOINT_URL=http://你的中转站/实际图片接口
OPENAI_IMAGE_PAYLOAD_JSON={"model":"{model}","prompt":"{prompt}","size":"{size}"}
```

产品推广流程只会调用配置的图片接口。图片接口失败时，脚本会保存提示词并报错停止，不会再走 Responses、chat completions、imagegen CLI 或本地绘图 fallback。


### 测试官方 gpt-image-2 图片接口

先把官方图片配置写到 `.env`：

```env
OPENAI_IMAGE_API_KEY=你的官方OpenAI图片Key
OPENAI_IMAGE_BASE_URL=https://api.openai.com/v1
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_API=images
OPENAI_IMAGE_SIZE=1024x1024
OPENAI_IMAGE_QUALITY=medium
IMAGE_OUTPUT=generated/test_gpt_image_2.png
```

然后运行：

```bash
python test_gpt_image_2.py
```

脚本只会打印脱敏后的 key、base URL、模型和输出路径，不会输出完整密钥。

## 根据产品链接生成使用场景图和赞美评论

如果帖子里包含外部产品链接，可以用产品链路模式。脚本会：

1. 打开帖子并提取可见文本。
2. 从帖子链接中挑选第一个外部产品链接；如果识别不到，可用 `--product-url` 手动指定。
3. 打开产品页，提取标题、描述、正文和图片线索。
4. 使用 `gpt-image-2` 生成无人物、像用户收到货后随手拍的真实产品图。
5. 生成一条更随意、有生活气，带产品名称/简称、基础特点、场景和心情的评论草稿。
6. 把评论和生成的图片填入 Facebook 评论框，等待图片预览渲染后直接提交。

推荐 `.env` 配置：

```env
OPENAI_IMAGE_API_KEY=你的官方OpenAI图片Key
OPENAI_IMAGE_BASE_URL=https://api.openai.com/v1
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_API=images
OPENAI_IMAGE_REFERENCE_ENABLED=true
OPENAI_IMAGE_EDIT_ENDPOINT_PATH=/images/edits
AI_PRODUCT_PROMO=true
PRODUCT_USE_CASES=home office, daily life, gifting, travel
AI_COMMENT_LANGUAGE=the same language as the post
AI_IMAGE_STYLE=exact landing-page product match, new background, realistic customer phone photo, no people, no background blur, casual lived-in setting, natural light
IMAGE_OUTPUT=generated/product_scene.png
```

产品推广图片会优先下载落地页里的产品图作为参考图，再调用图片编辑/参考图接口生成生活场景图；如果参考图下载或参考图生成失败，脚本会停止，不会退回纯文本生图，避免生成出和产品不一致的假图。

运行：

```bash
python auto_login.py --skip-login --post-url "https://www.facebook.com/61550584116226/posts/122290195820019470/" --ai-product-promo --image-output generated/product_scene.png
```

如果帖子里产品链接识别不到：

```bash
python auto_login.py --skip-login --post-url "https://www.facebook.com/61550584116226/posts/122290195820019470/" --product-url "https://example.com/products/example-product" --ai-product-promo --image-output generated/product_scene.png
```

可以用 `--use-cases` 控制使用场景：

```bash
python auto_login.py --skip-login --post-url "https://www.facebook.com/61550584116226/posts/122290195820019470/" --ai-product-promo --use-cases "cozy home office, weekend travel, thoughtful gift"
```

## 多账号 JSON 配置

如果有多个 Facebook 账号，推荐用 `accounts.json` 统一维护账号和对应的 Chrome 配置目录。先复制示例文件：

```bash
cp accounts.example.json accounts.json
```

然后编辑 `accounts.json`：

```json
{
  "accounts": {
    "main": {
      "username": "主账号邮箱或手机号",
      "password": "主账号密码",
      "profile_dir": "./chrome-profile-main",
      "skip_login": false,
      "attach_existing": false,
      "experience_notes": ""
    },
    "account2": {
      "username": "第二个账号邮箱或手机号",
      "password": "第二个账号密码",
      "profile_dir": "./chrome-profile-account2",
      "skip_login": false,
      "attach_existing": false,
      "experience_notes": "真实素材示例：收到后颜色比想象顺眼，挂起来挺省事，家里人说门口看着更有节日感"
    }
  }
}
```

运行时用 `--account` 指定账号：

```bash
python auto_login.py --account main --post-url "https://www.facebook.com/xxx/posts/xxx/" --ai-comment
python auto_login.py --account account2 --post-url "https://www.facebook.com/xxx/posts/xxx/" --ai-comment
```

每个账号会使用自己的 `profile_dir`，避免串号。第一次运行某个账号时，如果 Facebook 要验证码、双重验证或设备确认，手动完成后回终端按 Enter；后续这个目录会保留登录状态。

也可以在 `.env` 固定默认账号：

```env
ACCOUNTS_FILE=accounts.json
ACCOUNT_NAME=main
```

注意：`accounts.json` 已加入 `.gitignore`，不要把真实账号密码提交到 GitHub。

## 可选：保留登录状态

第一次人工验证后，可以在 `.env` 里启用：

```env
CHROME_PROFILE_DIR=./chrome-profile
```

这个目录会保存 Cookie 和浏览器会话，后续运行可能不需要重复登录。
