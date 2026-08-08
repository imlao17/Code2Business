# Code2Business 网页端部署（形态①）

一键启动：PG + API + 前端（nginx），浏览器打开 http://localhost:8080。
默认只监听 `127.0.0.1`，不会直接暴露到局域网或公网。

这是 C2B 的 **Web 工作台产品**，可单独部署；不要求同时安装 C2B CLI 或 Agent Skills。它适合在浏览器中导入代码、查看业务图谱、人工确认规则和下载交付文档。三种产品的定位和选择方式见根目录 [README](../../README.md)。

## 快速开始

```bash
./up.sh      # 构建前端 + core wheel + 后端镜像 + 启动全套
./down.sh    # 停止（数据保留在 volume）
```

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
- `docker compose down` 不删卷；彻底清理：`docker compose down -v`

## 常用运维

```bash
docker compose logs -f api        # 查看 API 日志
docker compose ps                 # 服务状态
docker compose restart api        # 重启 API
```

## 自定义

- LLM 模型/地址：网页「LLM 设置」随时改（存容器 ~/.c2b/config.json）
- 数据库地址：修改 compose 中 `C2B_DB_URL` 环境变量

## 远程访问与安全

- 不要直接把 compose 的 `8080` 端口改成公网监听；远程使用请在前面部署带 HTTPS 和身份认证的反向代理。
- 后端支持可选的 `C2B_API_TOKEN`。配置后，代理必须为 `/api` 请求注入 `X-C2B-Token`（或客户端发送 Bearer Token），否则 API 返回 401。
- LLM/Git 地址默认仅允许公网 HTTPS，并拒绝本机、私网和云元数据地址。确需连接内网 Git 或本地 Ollama 时，分别显式设置 `C2B_ALLOW_PRIVATE_GIT=1` / `C2B_ALLOW_PRIVATE_LLM=1`，且只应在可信内网使用。
