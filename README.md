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
CONFIRM_BEFORE_COMMENT=false
SUBMIT_COMMENT=true
```

如果要在评论里附加本地图片：

```bash
python auto_login.py --skip-login --post-url "https://www.facebook.com/61550584116226/posts/122290195820019470/" --comment "good" --comment-image generated/product_scene.png
```

默认会用 macOS 剪贴板方式粘贴图片，类似你手动复制图片后在评论框里按 `Command+V`：

```env
COMMENT_IMAGE_ATTACH_MODE=paste
```

如果你想回到文件上传 input 方式，可以改成：

```env
COMMENT_IMAGE_ATTACH_MODE=file
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
AI_COMMENT_STYLE=简短自然，像看到产品后的真实心情；少描述产品，多表达喜欢、舒服、惊喜、治愈等感受；不官方、不机械、不营销
```

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
OPENAI_IMAGE_API=images
OPENAI_IMAGE_ENDPOINT_URL=
OPENAI_IMAGE_ENDPOINT_PATH=/images/generations
OPENAI_IMAGE_SIZE=1024x1024
OPENAI_IMAGE_QUALITY=medium
AI_IMAGE_STYLE=product-focused realistic photography, no people, warm natural lighting
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
4. 使用 `gpt-image-2` 生成无人物、以产品为主体的展示图。
5. 生成一条简短、自然、偏心情表达的评论草稿。
6. 把评论和生成的图片填入 Facebook 评论框，等待图片预览渲染后直接提交。

推荐 `.env` 配置：

```env
OPENAI_IMAGE_API_KEY=你的官方OpenAI图片Key
OPENAI_IMAGE_BASE_URL=https://api.openai.com/v1
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_API=images
AI_PRODUCT_PROMO=true
PRODUCT_USE_CASES=home office, daily life, gifting, travel
AI_COMMENT_LANGUAGE=the same language as the post
AI_IMAGE_STYLE=product-focused realistic photography, no people, warm natural lighting
IMAGE_OUTPUT=generated/product_scene.png
```

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

## 可选：保留登录状态

第一次人工验证后，可以在 `.env` 里启用：

```env
CHROME_PROFILE_DIR=./chrome-profile
```

这个目录会保存 Cookie 和浏览器会话，后续运行可能不需要重复登录。
