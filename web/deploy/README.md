# Code2Business 网页端部署（形态①）

> 当前部署包版本：v0.2.2。版本变化见[仓库变更日志](../../CHANGELOG.md)。

一键启动：PG + API + 前端（nginx），浏览器打开 http://localhost:8080。
默认只监听 `127.0.0.1`，不会直接暴露到局域网或公网。

这是 C2B 的 **Web 工作台产品**，可单独部署；不要求同时安装 C2B CLI 或 Agent Skills。它适合在浏览器中导入代码、查看业务图谱、人工确认规则和下载交付文档。三种产品的定位和选择方式见根目录 [README](../../README.md)。

**本部署包的当前定位是本机单团队试点**：默认监听 `127.0.0.1`，系统本身不包含登录、角色权限或客户级数据隔离。只在本机或可信内网使用时可直接启动；若交给多人或通过公网访问，必须先在外部反向代理提供 HTTPS、身份认证和访问控制，且不要将不同客户项目混放在同一个未隔离实例。

## 快速开始

```bash
./up.sh      # 构建前端 + core wheel + 后端镜像 + 启动全套
./down.sh    # 停止（数据保留在 volume）
```

首次执行 `./up.sh` 会从 `.env.example` 创建权限为 `600` 的 `.env`，并生成一个
URL-safe 的 PostgreSQL 密码。部署者应备份该 `.env`；它不是可提交的项目文件。

首次启动后：
1. 打开 http://localhost:8080
2. 右上角「LLM 设置」选择 OpenAI Compatible（含 DeepSeek/OpenAI/自建兼容端）或 Anthropic Native（Claude），填入对应地址、模型和 Key（保存后即生效）

## 独立分发（离线部署到目标机器）

```bash
./build-dist.sh     # 产出离线包 c2b-web-deploy.tar.gz（镜像+compose+前端+wheel+校验清单）
```

目标机器（无需源码、无需构建环境）：
```bash
tar xzf c2b-web-deploy.tar.gz
cd c2b-web-deploy
cp .env.example .env
# 编辑 .env：至少替换 C2B_POSTGRES_PASSWORD；远程部署还应设置 C2B_API_TOKEN
chmod 600 .env
shasum -a 256 -c MANIFEST.sha256    # Linux 可改用 sha256sum -c
docker load < c2b-web-images.tar.gz
docker compose up -d
# 访问 http://localhost:8080
```

离线包目录包含：
- `frontend-dist/`：前端静态文件
- `dist/c2b-*.whl`：API wheel，供版本核对或具备依赖缓存时重建；离线启动使用已打包 API 镜像
- `c2b-web-images.tar.gz`：预构建运行时镜像包（API + PostgreSQL + nginx）
- `MANIFEST.sha256`：完整文件校验清单

## 目录

| 文件 | 说明 |
|---|---|
| `up.sh` / `down.sh` | 一键启停 |
| `docker-compose.yml` | PG + API + frontend 三服务编排（API 固定镜像名 `c2b-api:latest`） |
| `Dockerfile` | API 镜像（安装 deploy/dist/ 内 wheel） |
| `nginx/nginx.conf` | 静态页 + /api 代理（上传上限 1GB） |
| `frontend-dist/` | 运行时与离线包共用的前端产物目录（脚本生成） |
| `dist/` | 运行时与离线包共用的 wheel 目录（脚本生成） |

## 前置条件

- Docker（含 compose 插件）
- 本机构建需要 `python3`、Node.js 18+、`pnpm` 与 Docker；`up.sh` / `build-dist.sh` 会按锁文件安装前端依赖，并自动生成 `frontend-dist/` 与 `dist/`

## 数据持久化

- `c2b_pgdata`：PostgreSQL 数据卷（图谱/业务知识）
- `c2b_data`：上传的代码仓库（挂载到容器 /data/projects）
- `c2b_settings`：Web 保存的 Provider、Base URL、模型名和 Key（挂载到 `/root/.c2b`，API 容器重建后仍保留）
- `docker compose down` 不删卷；彻底清理：`docker compose down -v`

## 常用运维

```bash
docker compose logs -f api        # 查看 API 日志
docker compose ps                 # 服务状态
docker compose restart api        # 重启 API
```

Compose 为 PostgreSQL、API 和前端都配置了健康检查，API 的 `/healthz` 会同时核对数据库连接；
即使配置了 `C2B_API_TOKEN`，该探针仍可供容器平台使用且不会返回项目数据。`docker compose up -d --wait`
会等三项均就绪后再返回。仓库 CI 还会上传一个最小 ZIP，经 nginx 调用 API 完成扫描，检查
Spring API、MyBatis 表/SQL，并重启 API 验证 PostgreSQL 持久化。该自动化证明的是发布栈
主链路可运行，不代替客户网络、真实数据规模或外部模型连通性验收。

当前 MVP 的扫描、标注和模块规格任务在 API 进程内执行。API 重启时，正在执行的
线程无法继续运行；启动后系统会把这些任务标记为失败，并给出“请重新运行”的原因，
不会让项目永久停留在“运行中”。运行中的任务会按 `.env` 的
`C2B_JOB_LEASE_SECONDS` 定期刷新租约；超过该时长没有心跳的旧任务也会在下一次
项目任务启动时被释放。

## 配置与内网模型

- 复制 `.env.example` 为 `.env` 后配置数据库密码、Web 监听地址、API Token 与安全开关。`.env` 已被 Git 忽略，不要提交。
- LLM 模型/地址：网页「LLM 设置」随时改；配置保存到 `c2b_settings` 卷，重建 API 容器后仍会保留。
- 公网模型：默认仅允许公网 HTTPS 地址。
- 内网 / Docker / 宿主机模型：在可信网络内将 `.env` 的 `C2B_ALLOW_PRIVATE_LLM=1`，再运行 `docker compose up -d --force-recreate api`。这会放开 API 访问私网地址，不能与无认证的公网 Web 一起使用。
- Docker Compose 内的模型服务可填写 `http://服务名:端口/v1`，例如 `http://ollama:11434/v1`；宿主机模型在 Docker Desktop 上通常使用 `http://host.docker.internal:11434/v1`。Linux 还需要为 `api` 服务配置 `extra_hosts: ["host.docker.internal:host-gateway"]`，并确保模型服务对 Docker 网桥可访问。
- `C2B_ALLOW_PRIVATE_GIT` 与 LLM 开关独立，只有确需克隆内网 Git 时才设为 `1`。

### ZIP 导入安全边界

ZIP 导入始终会拒绝路径穿越、符号链接、超过文件数量/总解压量/单文件大小限制的内容。为避免
老系统仓库中的重复日志、导出文本或模板文件被误判为 zip bomb，单文件压缩率只在文件达到
`C2B_MIN_ARCHIVE_RATIO_MEMBER_BYTES`（默认 8 MiB）后检查；同时仍会检查整个压缩包的压缩率。

默认限制见 `.env.example`：最多 100,000 个成员、解压总量最多 2 GiB、单文件最多 512 MiB、
大文件压缩率最多 200:1、整体压缩率最多 100:1。仅在确认代码包可信且确有需要时才调大这些值；
不要为解决单个小文本文件的误报而关闭全部安全限制。

## 远程访问与安全

- 不要直接把 compose 的 `8080` 端口改成公网监听；远程使用请在前面部署带 HTTPS 和身份认证的反向代理。
- 后端支持可选的 `C2B_API_TOKEN`。配置后，代理必须为 `/api` 请求注入 `X-C2B-Token`（或客户端发送 Bearer Token），否则 API 返回 401。
- LLM/Git 地址默认仅允许公网 HTTPS，并拒绝本机、私网和云元数据地址。私网模型或 Git 只能通过 `.env` 中相应的显式开关启用，并且只应在可信内网使用。
