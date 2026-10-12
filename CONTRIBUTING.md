# 参与槐花MV

[项目首页](README.md) · [使用手册](docs/GUIDE.md) · [更新记录](CHANGELOG.md)

欢迎帮助槐花MV成为更好用的商品宣传片与漫剧工作台。你可以反馈问题、完善文档、分享可复用制作流程，或改进代码。

## 从哪里开始

| 参与方式 | 适合贡献的内容 |
| --- | --- |
| 使用反馈 | 难以理解的操作、保存与恢复问题、素材兼容性、导出失败 |
| 创作模板 | 商品行业流程、电影感国漫分镜、可编辑的创作需求和镜头结构 |
| 供应商适配 | 请求与响应格式、模型参数、参考图、声音目录、轮询与下载 |
| 文档与界面 | 更准确的说明、真实示例、可访问性和小屏幕体验 |
| 稳定性改进 | 自动保存、任务幂等、错误边界、取消、重试和本地媒体处理 |

较大的功能先在 Issues 描述目标、使用场景和实现范围，便于确认它与现有流程如何配合。小型修复可以直接提交 Pull Request。

## 反馈 Bug

请提供：

1. **操作与现象**：从哪个页面开始，做了什么，看到什么。
2. **预期结果**：你希望它如何工作。
3. **复现步骤**：尽量用小项目和最少素材说明。
4. **环境**：系统、Python、浏览器版本；媒体问题补充 FFmpeg / FFprobe 版本。
5. **错误证据**：任务错误、启动窗口日志或浏览器截图，先删除敏感内容。

涉及供应商时，注明协议、模型 ID 和相关参数，不要提供 API Key、完整 `.env` 或含认证信息的请求头。涉及媒体时，优先提供你有权分享的最小测试文件。

## 提出需求或贡献模板

说明创作场景、当前做法、遇到的困难，以及怎样判断新功能有用。模板应包含创意输入、角色或商品约束、镜头顺序、参考素材用途和输出目标。

模板只是可编辑起点，要明确其来源；不要把固定草稿描述为 AI 自动完成，也不要放入未获授权的角色、商品素材或声音。

## 本地开发

推荐 Python 3.10+。前端使用 React 19、TypeScript、Vite、React Flow 与 Zustand；当前前端逻辑测试直接加载 TypeScript，建议使用已验证的 **Node.js 24**。FFmpeg 和 FFprobe 需在 PATH 中。

### 安装与运行后端

在项目根目录执行 Windows 命令：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -X utf8 backend/app.py --port 5009
```

端口 `5009` 用来配合当前 Vite 代理。普通用户启动仍为 `5088`。完整本地使用与模型配置见 [使用手册](docs/GUIDE.md)。

### 运行前端开发服务

在另一个终端进入 `frontend/`：

```powershell
cd frontend
npm ci
npm run dev
```

Vite 默认端口是 `5173`，资源前缀是 `/static/canvas/`。访问 <http://localhost:5173/static/canvas/>；API 和本地素材请求代理到 `http://localhost:5009`。

Vite 开发页不注入 Flask 的真实项目 ID。需要验收作品创建、项目切换、刷新恢复等完整流程时，请构建后访问 Flask 页面。

### 构建可交付前端

在 `frontend/` 中执行：

```powershell
npm run typecheck
npm run build
```

构建产物固定写入 `backend/static/canvas/`，由 Flask 页面引用。修改源码后同时更新对应产物，避免页面继续使用旧包。浏览器 URL 仍为 `/static/canvas/`，不需要加入 `backend`。不要提交 `node_modules/`。

## 必要检查

后端逻辑改动在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s backend/tests -t backend -v
```

前端逻辑改动在 `frontend/` 运行：

```powershell
node --test tests/engine.test.mjs src/ui/studio/productionLogic.test.mjs
npm run build
```

FFmpeg / FFprobe 缺失时，依赖它们的集成测试可能跳过；报告通过结果时同时注明跳过项。供应商适配测试应使用隔离配置与模拟响应，普通自动化测试不请求付费服务。

UI 改动还应检查商品宣传片和漫剧各一个项目：打开工作室、编辑、保存、刷新恢复，再检查桌面与小屏幕布局。导出改动应验证镜头顺序、裁切范围、音轨和字幕。

## 代码改动约定

- 保留旧作品的数据；存储结构变更提供迁移与回归检查。
- 让分镜引用真正进入节点输入；修改端口规则时同步前后端定义。
- 任务必须区分排队、运行、完成、失败、取消和过期；不把未配置服务显示为成功。
- 重试与恢复不得悄悄重复提交供应商请求；旧任务结果不得覆盖新编辑。
- 使用环境变量管理密钥；配置状态接口只返回非敏感信息。
- 上传、下载和媒体路径需要边界检查；调用 FFmpeg 使用参数列表，不拼接 shell 命令。
- 文档描述应对应当前代码，外部模型兼容性需要证据，不把接口模拟测试写成真实生成验证。

核心文件：

| 路径 | 职责 |
| --- | --- |
| `frontend/src/` | 节点、工作室、状态、持久化、执行器 |
| `backend/modules/canvas_project_service.py` | SQLite 画布与生产资料 |
| `backend/modules/studio_service.py`、`backend/studio_routes.py` | 作品、素材、模板与分镜 |
| `backend/modules/generation_service.py`、`backend/generation_routes.py` | 供应商、任务、AI 规划与提示词辅助 |
| `backend/modules/media_pipeline.py` | 本地 FFmpeg 裁切、拼接、音轨和字幕 |
| `scripts/start.ps1` | Windows 启动；根目录入口保持兼容 |

## 提交 Pull Request

描述以下内容：

- 解决了什么问题，哪种操作会触发它。
- 修改后用户看到的行为。
- 如何验证，实际执行了哪些检查，是否有跳过项。
- 数据、配置或供应商协议是否变化。

避免提交 `.env`、`data/`、`output/`、`.local/`、虚拟环境、依赖目录或个人素材。`.local/` 中的原代码快照和迁移备份用于本机追溯，不属于公开源代码。截图统一放在 `docs/assets/`，使用可公开的示例内容；大改动配截图或短演示，方便评审。

文档更新补充对应导航；产品行为变化记录到 [CHANGELOG.md](CHANGELOG.md)。

## 许可与敏感问题

项目许可证待确认；后续核对源码来源和依赖许可后补充 `LICENSE`。贡献内容应能公开分享，许可证确认后再明确统一的贡献许可。

发现密钥泄漏或可利用的安全问题时，不要在公开 Issue 中粘贴密钥、个人数据或攻击细节。先提供不含敏感信息的描述，联系维护者协商私下补充材料。
