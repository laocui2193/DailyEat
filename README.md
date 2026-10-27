# 随心吃吃 🍜

一款 Windows 桌面餐厅推荐工具——面向香港大学学生，每天自动推荐 3 家周边餐厅，内置 623 家全港餐厅数据（米其林、亚洲 50 最佳、餐厅周优惠），并有从零手写的 **AI Agent** 推荐引擎。

## 功能亮点

- 📅 每日自动推荐 3 家 + 一键刷新，近 7 天历史
- 🎛️ 四维筛选：菜系大类/小类 × 用餐时段 × 预算上下限（对数刻度）× 无需预约/有优惠
- ⭐ 米其林星级徽章（红星 SVG）、绿星（三叶草）、预约提示、餐厅周优惠徽章
- 👤 用户系统（pbkdf2 哈希 + token 会话）、评分评论、排行榜（SQL 聚合防刷分）、打卡、收藏云同步
- ✨ **AI Agent**：Tool Calling（7 个工具）+ ReAct 循环 + 多模型分工（v4-pro 推理 / flash 评审）+ 幻觉防治 + 三层记忆（会话/画像/偏好蒸馏）+ RAG 语义搜索 + 多模态识图（拍食物照片推荐餐厅）
- 🧪 Agent 评测集（21 用例回归）+ 推荐覆盖蒙特卡洛验证
- 🚀 离线可用：后端不可用时本地推荐照常

## 技术栈

| 层 | 技术 |
|------|------|
| 桌面 | Electron（零依赖前端：单文件 HTML/CSS/JS） |
| 后端 | Python + Flask（单依赖）+ SQLite |
| AI | DeepSeek v4-pro/flash（OpenAI 兼容）+ 硅基流动（embedding bge-m3 / 视觉 Qwen3-VL） |

## 快速开始

```bash
# 后端
cd server
pip install flask
cp config.example.json config.json   # 填入你的 API Key
python app.py                          # http://localhost:3000

# 前端
npm install
npm start        # 或双击 启动.bat
```

后端未启动时 App 自动降级为离线模式。

## 项目结构

见 [CLAUDE.md](CLAUDE.md)（产品设计文档，含完整接口表与数据策略）。

## 声明

餐厅数据来自公开渠道（人工调研、米其林/亚洲 50 榜单、DiningCity 公开 API），仅供学习交流。
