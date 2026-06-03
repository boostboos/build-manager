# Build Manager

> 多项目 Docker 构建管理工具 — Web 界面管理 Maven 编译、Docker 构建、镜像上传与容器部署。

将传统手动执行脚本的流程抽象为带数据库存储的 Web 应用，支持项目配置、构建历史追溯、实时日志推送、Portainer 集成一键上传部署。

---

## 功能特性

### 项目管理

- 增删改查多个项目，每个项目独立配置构建参数
- 支持 Maven 编译（含 profile、settings、离线模式）、Docker build（含缓存策略）、镜像导出压缩

### 构建系统

- **一键构建**：Maven→Docker build→镜像保存（tar.gz）全自动串联
- **实时日志**：后台 subprocess 流式输出，浏览器轮询推送，支持复制
- **构建取消**：支持停止正在运行的构建（`taskkill /T /F` 杀进程树）
- **超时保护**：600 秒超时自动终止
- **状态追溯**：pending → running → success/failed/stopped/timeout，含耗时统计

### Portainer 集成

- **配置存储**：每项目独立配置 Portainer URL、用户名、密码、环境 ID、容器名
- **镜像上传**：将构建产物的 tar.gz 流式上传到 Portainer Docker API
- **容器部署**：自动保留原容器配置（ENV、端口映射、卷挂载、网络、重启策略等），重命名旧容器→创建新容器→启动→清理旧容器
- **一键链式**：构建→上传→部署全自动串联，每一步等待完成再进入下一步

### 安全设计

- 删除镜像仅操作本地 Docker `docker rmi`，不触及 Portainer 远程环境
- SECRET_KEY 通过环境变量 `FLASK_SECRET_KEY` 配置
- Debug 模式通过环境变量 `FLASK_DEBUG=1` 开启
- 所有 subprocess 使用列表传参，无 shell 注入风险
- Jinja2 模板默认 auto-escape，防 XSS
- 监听 `127.0.0.1` 仅本地访问

### UI/UX

- Bootstrap 5 响应式设计，深色导航栏
- 构建日志暗色终端风格，支持复制/滚动到底部
- 状态徽章区分一键部署（紫色）与手动部署（绿色）
- 分页浏览构建历史

---

## 快速开始

### 环境要求

- Python 3.9+
- Docker CLI（构建与本地镜像删除需要）
- Maven（可选，跳过 Maven 模式不需要）

### 启动

**方式一（Windows 推荐）：** 双击 `start.bat`

- 自动安装依赖
- 启动服务并打开浏览器

**方式二（命令行）：**

```bash
pip install -r requirements.txt
python app.py
```

浏览器打开 **http://127.0.0.1:5000**

### 停止

- 双击 `stop.bat`（Windows）
- 或命令行 `Ctrl+C`
- 或 `taskkill /f /im python.exe`

---

## 配置指南

### 创建项目

在「新建项目」页面填写以下配置：

| 字段              | 说明                        | 示例                                    |
| ----------------- | --------------------------- | --------------------------------------- |
| 项目名称          | 唯一标识                    | `biz-service`                         |
| 项目根目录        | 代码绝对路径                | `E:/project/my-app`                   |
| Maven 命令        | mvn.cmd 完整路径            | `E:/tools/maven/bin/mvn.cmd`          |
| Maven Settings    | settings.xml 路径           | `E:/tools/maven/conf/settings.xml`    |
| JAVA_HOME         | JDK 路径                    | `E:/tools/jdk/corretto-21`            |
| Maven Profile     | `-P` 参数                 | `dev`                                 |
| 离线模式          | Maven `-o` 参数           | 勾选                                    |
| Dockerfile 目录   | 相对项目根                  | `docker`                              |
| Dockerfile 文件名 | 默认 `DockerfileH`        | `DockerfileH`                         |
| 镜像标签前缀      | Registry 前缀               | `registry.example.com/my-project/` |
| 镜像标签后缀      | 可选，加在时间戳前          | `my-service:`                      |
| 输出目录          | 相对项目根                  | `docker/output`                       |
| 压缩格式          | Gzip 或 tar                 | `Gzip`                                |
| 无缓存构建        | `docker build --no-cache` | 勾选                                    |
| 跳过 Maven        | 直接执行 Docker             | 勾选                                    |
| 跳过保存          | 不导出 tar.gz               | 勾选                                    |

#### 镜像标签规则

最终标签 = `前缀 + 后缀 + MMdd-HHmm`

例如：

- 前缀：`registry.example.com/my-project/`
- 后缀：`my-service/`
- 时间：`0602-0846`
- 结果：`registry.example.com/my-project/my-service/0602-0846`

### Portainer 配置

在项目编辑页面的 **Portainer 配置** 区域填写：

| 字段           | 说明                               | 示例                          |
| -------------- | ---------------------------------- | ----------------------------- |
| Portainer 地址 | Portainer 服务 URL                 | `http://192.168.1.100:9000` |
| 用户名         | Portainer 登录用户                 | `admin`                     |
| 密码           | Portainer 登录密码                 | `********`                  |
| 环境 ID        | Portainer Environment(Endpoint) ID | `1`                         |
| 容器名称       | 目标容器名                         | `my-app-container`          |

> 密码以明文存储在 SQLite 数据库中。建议使用独立的 Portainer 账号并限制权限。

---

## 使用指南

### 基本构建流程

1. **创建项目** → 填写路径和构建参数
2. **触发构建** → 点击「构建」按钮，实时查看日志
3. **查看产物** → 构建成功后，页面显示输出文件路径和加载命令

### 上传到 Portainer

仅在构建成功后可操作。单击「上传」将 tar.gz 流式上传到 Portainer 环境。

### 部署容器

上传成功后或已有成功部署记录可操作。单击「部署」执行：

1. 检查现有容器，获取完整配置
2. 将旧容器重命名为 `{name}-old-{build_id}`
3. 用新镜像创建新容器（保留 ENV、端口、卷、网络等配置）
4. 启动新容器
5. 删除重命名的旧容器

### 一键构建部署

当项目配置了 Portainer 信息后，可使用「一键构建部署」按钮：

1. 自动触发构建
2. 等待构建完成
3. 自动上传镜像到 Portainer
4. 等待上传完成
5. 自动部署容器

构建记录会标记为「一键构建部署」紫色徽章，与手动操作区分。

### 删除构建记录

删除时支持两步确认：

1. 是否删除构建记录
2. 是否同时删除 **本地 Docker 镜像**（`docker rmi`，不影响 Portainer 远程镜像）

---

## 状态说明

### 构建状态

| 状态        | 徽章 | 说明         |
| ----------- | ---- | ------------ |
| `pending` | 灰色 | 等待执行     |
| `running` | 黄色 | 正在构建     |
| `success` | 绿色 | 构建成功     |
| `failed`  | 红色 | 构建失败     |
| `stopped` | 灰色 | 用户手动停止 |
| `timeout` | 红色 | 超时终止     |

### 上传/部署状态

| 状态               | 徽章                                    | 说明                 |
| ------------------ | --------------------------------------- | -------------------- |
| `none`           | 灰色「未上传」                          | 未执行上传操作       |
| `uploading`      | 蓝色「上传中」                          | 正在上传到 Portainer |
| `upload_success` | 绿色「上传成功」/「一键上传成功」       | 镜像已上传           |
| `upload_failed`  | 红色「上传失败」                        | 上传出错             |
| `deploying`      | 蓝色「部署中」                          | 正在部署容器         |
| `deploy_success` | 紫色「一键 部署成功」/ 绿色「部署成功」 | 容器已部署           |
| `deploy_failed`  | 红色「部署失败」                        | 部署出错             |

> `is_chain=True` 的记录会显示「一键」前缀或紫色徽章。

---

## 安全说明

| 项目       | 状态                                                               |
| ---------- | ------------------------------------------------------------------ |
| SECRET_KEY | 通过环境变量 `FLASK_SECRET_KEY` 配置，未设置时回退到不安全默认值 |
| 调试模式   | 默认关闭，通过 `FLASK_DEBUG=1` 开启                              |
| 密码存储   | Portainer 密码以明文存储在 SQLite，建议使用受限账号                |
| Shell 注入 | 所有 subprocess 调用使用列表形式，无 shell 注入风险                |
| XSS 防护   | Jinja2 模板默认 auto-escape                                        |
| 网络暴露   | 默认监听 `127.0.0.1`，仅本地可访问                               |
| 远程镜像   | 删除操作仅操作本地 Docker，不影响 Portainer 远程环境               |

---

## API 接口

### 上传/部署

| 方法     | 路径                               | 说明                     |
| -------- | ---------------------------------- | ------------------------ |
| `POST` | `/api/builds/<id>/upload`        | 上传镜像到 Portainer     |
| `POST` | `/api/builds/<id>/deploy`        | 部署容器                 |
| `GET`  | `/api/builds/<id>/upload-status` | 上传/部署状态 + 实时日志 |
| `POST` | `/api/builds/<id>/delete-image`  | 删除本地 Docker 镜像     |

### 构建控制

| 方法     | 路径                           | 说明                                    |
| -------- | ------------------------------ | --------------------------------------- |
| `GET`  | `/api/builds/<id>/status`    | 构建状态 + 镜像标签 + 输出文件          |
| `GET`  | `/api/builds/<id>/logs`      | 构建日志（支持 `since` 参数增量拉取） |
| `POST` | `/api/builds/<id>/stop`      | 停止构建                                |
| `GET`  | `/api/projects/<id>/running` | 检查项目是否有正在运行的构建            |

### 页面路由

| 方法         | 路径                                   | 说明                    |
| ------------ | -------------------------------------- | ----------------------- |
| `GET`      | `/projects`                          | 项目列表                |
| `GET/POST` | `/projects/new`                      | 新建项目                |
| `GET/POST` | `/projects/<id>/edit`                | 编辑项目                |
| `POST`     | `/projects/<id>/delete`              | 删除项目及所有构建记录  |
| `POST`     | `/projects/<id>/build`               | 触发构建                |
| `POST`     | `/projects/<id>/build-upload-deploy` | 一键构建+上传+部署      |
| `GET`      | `/projects/<id>`                     | 项目详情 + 构建记录列表 |
| `GET`      | `/builds`                            | 全局构建记录（分页）    |
| `GET`      | `/builds/<id>`                       | 构建详情 + 实时日志     |
| `POST`     | `/builds/<id>/delete`                | 删除构建记录            |

---

## 目录结构

```
build-manager-pro/
├── app.py                # Flask 主应用（路由 + 模型 + 后台线程）
├── requirements.txt      # Python 依赖
├── README.md             # 本文档
├── start.bat             # Windows 一键启动
├── stop.bat              # Windows 停止脚本
├── build_manager.db      # SQLite 数据库（自动生成）
├── Dockerfile            # Docker 独立部署
├── docker-compose.yml    # Docker Compose 编排
├── templates/            # Jinja2 模板
│   ├── base.html         # 基础布局 + 导航栏
│   ├── projects.html     # 项目列表 + 一键构建部署按钮
│   ├── project_form.html # 项目编辑表单（含 Portainer 配置）
│   ├── project_detail.html # 项目详情 + 构建记录
│   ├── builds.html       # 全局构建记录列表
│   ├── build_detail.html # 单条构建详情 + 日志
│   ├── _build_badge.html # 构建状态徽章
│   └── _upload_badge.html # 上传/部署状态徽章（一键/手动区分）
└── static/
    └── style.css         # 自定义样式
```

---

## 技术栈

| 组件        | 技术                          |
| ----------- | ----------------------------- |
| 后端框架    | Flask 3.1（Python）           |
| 数据库      | SQLite + Flask-SQLAlchemy 3.1 |
| 模板引擎    | Jinja2                        |
| 前端        | Bootstrap 5.3（CDN）          |
| 日志推送    | 轮询（setInterval + API）     |
| 后台任务    | threading.Thread（daemon）    |
| HTTP 客户端 | requests                      |
| 构建执行    | subprocess.Popen 流式输出     |
| 容器管理    | Portainer Docker API（REST）  |
| 认证缓存    | 内存 JWT 缓存（8 小时 TTL）   |

---

## 架构说明

```
┌─────────────┐     ┌──────────────┐     ┌────────────────┐
│  浏览器      │────▶│  Flask 应用   │────▶│  subprocess    │
│ (Bootstrap)  │     │  (app.py)     │     │  (Maven/Docker)│
└─────────────┘     └──────┬───────┘     └────────────────┘
                           │
                    ┌──────┴───────┐     ┌────────────────┐
                    │  SQLite DB    │     │  Portainer API  │
                    │ (builds/proj) │     │  (Docker 远程)  │
                    └──────────────┘     └────────────────┘
```

- **构建流程**：浏览器触发 → 后台 daemon 线程执行 subprocess → 流式写入 `_live_logs` → 浏览器轮询 `/api/logs`
- **上传流程**：后台线程通过 requests 流式 POST 到 Portainer Docker API
- **部署流程**：后台线程通过 Portainer API 执行 inspect → rename → create → start → delete
- **一键链式**：第二个后台线程轮询 build/upload 状态，依次触发上传和部署

---

## 常见问题

### 路径中的反斜线被转义

**原因**：Windows 路径 `\a`、`\b`、`\n` 等在 Python 字符串中被转义。

**解决**：使用正斜线 `E:/project/my-app` 或双反斜线 `E:\\project\\my-app`。

### 构建卡在"运行中"

**原因**：子进程异常退出但未触发正常回调。

**解决**：刷新页面或重启服务。可在项目详情页再次触发构建（会自动停止之前的）。

### Portainer 认证失败

检查 Portainer 地址、用户名、密码是否正确。首次认证后 JWT 会缓存 8 小时，若密码在 Portainer 侧变更需重启应用。

### 部署后容器配置丢失

部署流程会自动从现有容器获取 ENV、端口映射、卷挂载、网络等配置。若旧容器已被删除，会以空配置创建容器。首次部署前请确保容器已手动创建并配置好。

### 一键链式卡住

一键构建部署依赖于轮询构建和上传状态。如果某一步异常（如构建失败、上传超时），链会自动中止。请在构建详情页查看日志了解原因。

---

## Docker 部署

```bash
docker compose build
docker compose up -d
```

### 注意

- 项目目录需要通过 volume 挂载到容器内
- Windows 路径映射到 Docker 需要调整卷挂载方式
- SQLite 数据库需挂载到持久化卷

---

## License

Apache 2.0
