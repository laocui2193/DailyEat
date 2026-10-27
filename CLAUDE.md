# 附近餐厅推荐 — 产品设计文档（香港大学版）

## 一、产品概述

**产品名称**：随心吃吃

**产品定位**：一款 Windows 桌面小工具，每天自动推荐 3 家香港大学（HKU）周边的餐厅，帮助港大学生解决"今天吃什么"的选择困难。

**核心机制**：每个自然日自动刷新推荐，用户不满意可一键手动刷新。

---

## 二、目标用户

- 香港大学学生与教职员
- 港大周边（西环/坚尼地城/西营盘/上环）的居民
- 有"今天吃什么"选择困难症的用户

---

## 三、功能需求

### 3.1 核心功能

| 功能 | 说明 |
|------|------|
| 每日推荐 | 每天展示 3 家附近餐厅，自然日 00:00 自动刷新 |
| 一键刷新 | 不满意当前推荐时点击按钮重新随机 3 家（不包含当前 3 家） |
| 餐厅卡片 | 展示名称、菜系、人均价格、距离、简介、推荐菜 |
| 历史记录 | 保留近 7 天的推荐历史，可回顾 |

### 3.2 筛选功能

- 口味偏好：两级分类筛选（14 大菜系，覆盖煲仔饭/粥粉面/糖水甜品/西班牙菜/意大利菜/法国菜/墨西哥菜/印度菜/江浙菜/日本料理（寿喜烧/怀石/omakase/日式自助/拉面/日式烧肉）/韩国料理/创意融合等）
- 用餐要求：「⚡ 无需预约」与「🎁 有优惠」独立开关（位于独立区块），可与菜系/时段/预算任意叠加
- 用餐时段：早餐/午餐/晚餐/宵夜，启动时按系统时间自动检测
- 预算区间：上下限双滑块，HK$30–4000（对数刻度，低端密集高端拉开，交叉自动钳制）
- 收藏夹：爱心标记 + 👤 账号菜单内的收藏列表（弹窗查看，点击收藏项弹出餐厅完整卡片，不动主页推荐）
- 预约提示：`book: true` 的餐厅显示"建议提前预约"标签；「无需预约」芯片可一键筛选"马上能吃"的餐厅（`!book` 的所有餐厅）
- 米其林徽章：星数徽章（红星 SVG）、绿星（三叶草 SVG）随卡片展示

### 3.3 云端功能（自建后端，离线自动降级）

- 用户系统：注册/登录（pbkdf2 加盐哈希 + 随机 token），收藏云端同步（登录时自动 ∪ 合并）
- 评分评论：给餐厅打 1-5 星 + 短评（一用户一店一条，可改分），卡片显示平均分与评价数
- 排行榜：食客评分 TOP10（SQL 聚合：平均分 + 评价数双排序，≥2 条防刷分）
- 打卡签到：每日打卡记录连续天数（同日同店防重复），👤 菜单显示统计
- 口味画像：基于收藏/历史/高分评价分析大类偏好，生成人格标签 + 偏好条形图
- UGC 餐厅：用户提交新餐厅（审核制，X-Admin-Key 审核通过后并入推荐池，id 10000+）
- AI Agent 推荐：自然语言对话 + Tool Calling（7 个工具：筛选/详情/排行榜/口味画像/语义相似/收藏/打卡），ReAct 多步推理循环（最多 8 轮工具调用 + 工具并行执行），用户画像记忆注入；多模型分工——主推理 deepseek-v4-pro（thinking 禁用提速）、评审/蒸馏用 deepseek-flash；幻觉防治——防幻觉提示词铁律 + 评审 Agent 事实核查（异步化：卡片先出、后台核查后自动修正）+ 幻觉专项评测；查询缓存（单条重复提问 10 分钟秒回）；SSE 流式输出（思考过程完全隐藏，仅「正在思考」圆点动画 + 推荐卡片）
- 多模态识图：上传食物照片（前端压缩至 512px）→ 视觉模型（硅基流动 Qwen3-VL-8B）识别菜名 → 注入 Agent 上下文推荐餐厅；识别失败优雅降级为文字模式
- RAG 语义搜索：餐厅文本向量化（embedding 服务，默认硅基流动 bge-m3）存 SQLite，余弦相似度检索「像XX那样的店」；未配置时优雅降级
- 偏好蒸馏（长期记忆）：对话结束后后台 LLM 抽取长期饮食偏好（忌口/预算/菜系）存 SQLite，下次对话自动注入系统提示词——跨对话记忆；口味画像可查看
- 离线降级：后端不可用时自动隐藏云端功能并显示提示条，本地推荐/筛选/收藏不受影响

### 3.4 辅助功能

- 倒计时显示距下次刷新时间（精确到秒）
- 卡片错峰入场/淡出动画
- 系统托盘常驻
- 浅色/深色主题
- 👤 账号下拉菜单（登录状态/收藏入口/退出）
- 餐厅详情弹窗（收藏列表/AI 推荐卡片点击弹出完整卡片，栈式叠加——关闭后回到上一层）

---

## 四、数据策略

### 4.1 数据文件

餐厅数据存放于独立文件 `restaurants.json`，与代码分离：

| 字段 | 说明 |
|------|------|
| `_metadata.version` | 数据版本号，每次更新 +1 |
| `_metadata.lastUpdated` | 最后更新日期 |
| `_metadata.dailyPicks` | 人工指定今日精选 3 家（null = 随机） |
| `_metadata.notice` | 运营公告，显示在 UI 顶部 |
| `restaurants[].closed` | true = 歇业，自动从推荐池排除 |
| `restaurants[].book` | true = 高档餐厅，卡片显示"建议提前预约" |
| `restaurants[].michelin` | 1/2/3 = 米其林星数，卡片显示内联 SVG 星形徽章（6 花瓣手绘近似，官方 logo 有商标权） |
| `restaurants[].green` | true = 米其林绿星（可持续餐饮），卡片显示三叶草 SVG 徽章 |
| `restaurants[].subs` | 可选，子类数组：融合餐厅可同时属于多个子类（如 Ta Vie 旅 = 法国菜 + 日西融合） |
| `restaurants[].features` | 场景特征标签数组（海景/夜景/约会氛围/排队热门等 16 种），由 LLM 批量增强生成（server/enrich_features.py），Agent 按语义筛选用 |
| `restaurants[].promo` | 优惠活动信息（如餐厅周：title/meal/price/validUntil/source），由 LLM 抽取流水线生成（server/fetch_restaurant_week.py），过期自动隐藏 |

### 4.2 加载策略

三级降级：远程拉取 → 本地文件 → localStorage 缓存。拉取成功后写入缓存，离线可用。数据版本变化时自动清空旧推荐记录与收藏（ID 失效保护）。

### 4.3 餐厅数据库

**623 家**，覆盖全港（港大周边步行圈 + 港岛 + 九龙），含米其林星级（47 家）、亚洲50最佳与 458 家餐厅周优惠：

| 区域（距离） | 数量 |
|------|:--:|
| 港大步行圈（≤1.3km） | 42 |
| 上环（2.5km） | 24 |
| 中环（3km） | 179 |
| 金钟/湾仔（4km） | 150 |
| 铜锣湾（5.5km） | 76 |
| 尖沙咀（7km） | 125 |
| 旺角/佐敦/油麻地（7.5-8km） | 16 |
| 九龙城及更远 | 10 |

数据来源三通道：人工调研 + 榜单（米其林/亚洲50）+ **官方 API 自动导入**（餐厅周流水线：api.diningcity.asia 裸接口 → 分页拉取 → LLM 匹配/补全 → 入库）

榜单覆盖：亚洲50最佳（大班楼、永、Caprice、Neighborhood、Mono、Estro、Ando、VEA、Testina、Ta Vie、Feuille、Bombana）、米其林三星（Bombana、Amber、Caprice、富临饭店、Ta Vie、唐阁）及大量一星/必比登餐厅

### 4.4 推荐算法

1. 从餐厅池中剔除歇业餐厅 + 历史 3 天内推荐过的
2. 应用筛选链：口味偏好 → 时段 → 预算上下限 → 无需预约（AND 逻辑，可任意组合）
3. 从剩余中随机选 3 家，保证菜系不重复
4. 每日凌晨自动刷新；手动刷新排除当前展示的 3 家
5. 远程数据指定 `dailyPicks` 时跳过随机，直接采用人工精选

---

## 五、技术方案

### 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 桌面框架 | Electron | 最小化使用，仅做窗口壳 |
| 前端 | 原生 HTML/CSS/JS | 零依赖，单文件内嵌 |
| 数据存储 | localStorage | 推荐记录 + 收藏 + 数据缓存 |
| 数据源 | restaurants.json | 独立 JSON 文件，支持远程化 |
| 后端 | Python + Flask | 自建 REST API（用户/评分/排行榜），SQLite 数据库（Python 内置 sqlite3） |

### 项目结构

```
餐厅推荐/
├── main.js           # Electron 主进程（窗口 + 托盘）
├── index.html        # 全部 UI（内嵌 CSS + JS）
├── restaurants.json  # 餐厅数据（与代码分离）
├── package.json      # 依赖配置
├── CLAUDE.md         # 本文件
├── 启动.bat          # 一键启动 App
├── 启动后端.bat      # 一键启动后端（Python）
├── server/           # 自建后端（Python + Flask）
│   ├── app.py        # REST API：用户/评分/排行榜/打卡/UGC/AI Agent
│   ├── db.py         # 数据层（SQLite，接口与旧 JSON 版一致）
│   ├── enrich_features.py  # 一次性脚本：LLM 批量打场景特征标签
│   ├── fetch_restaurant_week.py  # 优惠流水线：网页→LLM抽取→匹配入库（餐厅周）
│   ├── rag.py             # RAG 模块：embedding 向量化 + 余弦相似度检索
│   ├── embed_restaurants.py  # 一次性脚本：全量餐厅向量化建索引
│   ├── eval_agent.py      # Agent 评测集（21 用例回归，含幻觉专项）
│   ├── verify_coverage.py # 推荐覆盖验证（蒙特卡洛模拟，全量可达性检查）
│   ├── config.json   # LLM 密钥 + 管理员密钥（不提交到公开仓库）
│   ├── requirements.txt  # flask
│   └── data/         # nearbyeats.db（SQLite 数据库）
└── resources/
    └── icon.png      # 应用图标
```

### 后端接口

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/health | GET | 健康检查（前端用它检测在线状态） |
| /api/register | POST | 注册（用户名唯一，密码 pbkdf2 加盐哈希） |
| /api/login | POST | 登录（返回随机 token） |
| /api/me | GET | 当前用户信息（需 Bearer token） |
| /api/favorites | GET/PUT | 收藏云同步 |
| /api/reviews | POST | 评分（1-5 星 + 评论，一用户一店一条） |
| /api/reviews?restaurant_id=N | GET | 某餐厅评价列表 + 平均分 |
| /api/leaderboard | GET | 评分排行榜 TOP10（≥2 条评价防刷分） |
| /api/checkins | POST | 打卡（同日同店防重复，返回连续天数） |
| /api/checkins/mine | GET | 我的打卡统计（连续/累计） |
| /api/reviews/mine | GET | 我的全部评分（口味画像用） |
| /api/preferences/mine | GET | 我的长期偏好（AI 蒸馏记忆） |
| /api/submissions | POST | 提交新餐厅（审核制，初始 pending） |
| /api/submissions/mine | GET | 我提交的餐厅及审核状态 |
| /api/submissions/approved | GET | 已审核 UGC 餐厅（前端启动时并入推荐池） |
| /api/submissions/:id/approve | PUT | 审核通过（需 X-Admin-Key 请求头） |
| /api/agent/chat | POST | AI Agent 对话（非流式，评测/调试用） |
| /api/agent/stream | POST | AI Agent 流式对话（SSE：进度/打字机/卡片/后台评审修正；支持 image 参数多模态识图） |
| /api/agent/confirm | POST | 写操作确认（session_id + approved → 恢复 Agent 循环继续流式输出） |

### 依赖策略：前端零依赖，后端单依赖

- 前端（Electron + index.html）：无构建、无框架、零依赖。App 功能简单、UI 轻量，使用 React 等框架反而增加复杂度；原生 HTML/JS 加载更快、体积更小
- 后端（server/）：仅 Flask 一个依赖；数据库用 Python 内置 sqlite3，密码哈希用内置 hashlib，零额外安装
- 打包：electron-builder（devDependency），产物为绿色版文件夹 + zip

---

## 六、运行方式

```bash
# 前端（Electron）
cd d:/练习/餐厅推荐
npm install        # 首次安装依赖
npm start          # 或双击 启动.bat

# 后端（Python + Flask）
pip install flask  # 首次安装依赖（若用 Anaconda 环境则已内置）
npm run server     # 或双击 启动后端.bat（用 Anaconda 的 python）
```

注意：本机 `python` 命令指向 MSYS2 的 Python（无 flask），后端需用
`C:/Users/asus/anaconda3/python.exe` 运行（启动后端.bat 已处理）。

后端未启动时 App 自动降级为离线模式：本地推荐、筛选、收藏照常可用，仅评分/排行榜/云同步不可用。

---

## 七、打包发布

```bash
npx electron-builder --win --dir    # 产物在 dist/win-unpacked/
# 已配置：package.json "build" 字段（appId/productName/icon/files/win.target）
```

注意两点（实测踩坑）：
1. winCodeSign 缓存在 Windows 上解压失败（符号链接权限）：手动用 7za `-snld` 解压全部 `%LOCALAPPDATA%\electron-builder\Cache\winCodeSign\*.7z` 后重跑
2. `ELECTRON_RUN_AS_NODE` 环境变量会让打包版 exe 以 Node 模式启动而闪退：测试前必须真删除该变量（PowerShell `$env:X=$null`），发布给朋友不受影响（对方无此变量）

分发流程：`dist/win-unpacked/` 内容复制到 `dist/随心吃吃/`（含使用说明.txt）→ 压缩为 zip 发给朋友，解压双击 exe 即用（无需装 Node）。

云端功能发布给朋友需先部署 server/（Render/Railway 等免费平台），再把 index.html 的 `API_BASE` 改为部署地址。
