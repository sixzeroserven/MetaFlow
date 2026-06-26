# MetaFlow 交接文档

## 1. 项目用途

MetaFlow 用于批量执行 Facebook 帖子评论任务，支持两种模式：

- `ai-comment`：只生成并发布评论
- `ai-product-promo`：生成评论 + 配图后发布

当前项目重点是“根据帖子和落地页信息生成更贴合产品的评论与图片”。


## 2. 核心结构

- `auto_login.py`
  - Selenium 主执行入口
  - 负责登录、打开帖子、提取帖子/商品信息、生成评论/图片、提交评论
- `openai_content_client.py`
  - OpenAI 文本与图片调用封装
  - 当前已加入“产品画像（product brief）”逻辑
- `web_app.py`
  - 本地/容器内任务投递台，端口 `8765`
- `worker.py`
  - 轮询 `web_app.py` 的任务队列并执行
- `docker-compose.yml`
  - 当前主运行方式
- `accounts.json`
  - 多账号配置


## 3. 当前生成逻辑

### 评论与图片不再只靠简单关键词

现在会先结合以下信息提炼产品画像：

- Facebook 帖子内容
- 商品落地页 `Title`
- 商品落地页 `H1`
- 落地页 `Description`
- 落地页正文可见文本
- 商品 URL path slug

画像中会提炼：

- `category`
- `subtype`
- `product_name`
- `product_summary`
- `scene_hint`
- `comment_focus`
- `image_strategy`
- `detection_reason`

### 当前两类产品逻辑

- 假花 / 仿真花
  - 走 `reference_product_scene`
  - 保持“成品商品本体 + 新生活化背景”
  - 评论强调：顺眼、低维护、门口/阳台/玄关摆放感
  - 明确避免种子语境

- 种子
  - 走 `seed_growth`
  - 图片主体是“刚种下后的发芽 / 小苗 / 早期生长阶段”
  - 背景强调：花盆、土壤、苗盘、菜园、花园边、后院土地
  - 评论强调：刚种下、等发芽、生命力、种植期待、自家花园/土地场景


## 4. 最近关键改动

- 已把“假花”和“种子”从同一套提示词里拆开
- 已修正 `seedsunrise` 这类域名导致的误判问题
  - 现在只看 URL path slug，不把域名里的 `seed` 当成商品类型证据
- 已给日志增加 `Product brief` 输出，便于排查为什么会这样生成
- 评论有近期历史去重逻辑，避免多条评论过于相似


## 5. 当前账号状态

当前 `accounts.json` 只保留：

- `account2`
- `account3`

## 6. 当前部署方式

主服务跑在 Azure 服务器：

- 主机：`azureuser@20.6.132.23`
- 项目目录：`/home/azureuser/MetaFlow`

容器服务：

- 服务名：`metaflow`
- Web 端口：`8765`
- noVNC 端口：`7900`

常用命令：

```bash
ssh azureuser@20.6.132.23
cd /home/azureuser/MetaFlow
docker compose ps
docker compose restart metaflow
docker compose up -d --build
```


## 7. 运行链路

1. 在 `web_app.py` 提交任务
2. `worker.py` 认领任务
3. `auto_login.py` 打开帖子并抓取帖子/落地页内容
4. `openai_content_client.py` 生成评论和图片
5. Selenium 将评论和图片提交到 Facebook


## 8. 当前最该关注的排查点

如果评论或图片不对，优先看运行日志里的两段：

- `Detected product profile: ...`
- `Product brief: ...`

重点确认：

- `category` 是否判断正确
- `image_strategy` 是否正确
  - 假花应为 `reference_product_scene`
  - 种子应为 `seed_growth`
- `scene_hint` 是否贴合帖子和落地页


## 9. 已知风险 / 后续建议

- 落地页文案如果很乱，产品画像仍可能偏差
- 某些种子页如果几乎全是成熟效果图文，仍可能把“生长阶段”引导得不够稳
- 建议下一步继续做：
  - 为常见商品类型补更细的场景模板
  - 把 `Product brief` 存档到 `generated/` 里，方便复盘
  - 做一组真实假花 / 种子样例回归测试

