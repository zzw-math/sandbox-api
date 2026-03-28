# Sandbox API MVP

一个偏本地优先的多租户 Sandbox API 最小实现，用来给 Agent 执行 `read`、`write`、`bash` 这类工具调用。

这一版的目标：

- 每个工具调用都必须带 `sandboxId`
- 相同 `sandboxId` 复用同一份持久化工作目录
- 不同 `sandboxId` 使用不同目录，数据隔离
- Sandbox 元数据持久化到本地 SQLite
- 文件持久化到本地目录
- 为后续切换到 Docker runtime 预留抽象层

这不是最终生产版，但已经把核心边界搭好了。

## 项目结构

```text
SandboxAPI/
  README.md
  pyproject.toml
  sandbox_api/
    main.py              # FastAPI 入口
    config.py            # 配置
    db.py                # SQLite 初始化
    schemas.py           # API 请求/响应模型
    services/
      path_guard.py      # 路径安全检查
      sandbox_manager.py # Sandbox 生命周期和元数据管理
      tool_executor.py   # read/write/bash 调度
    runtime/
      base.py            # Runtime 抽象
      docker.py          # Docker runtime
  data/
    sandboxes/            # 运行时自动创建
    sandbox.db           # 运行时自动创建
```

## 当前实现方式

### 隔离模型

- 每个 `sandboxId` 对应一个独立目录
- 默认目录为 `./data/sandboxes/<sandboxId>/workspace`
- 所有文件读写都限制在该目录内
- 每个 sandbox 拥有一把异步锁，同一 `sandboxId` 的工具调用串行执行

### 持久化

- 文件内容保存在本地 `data/sandboxes/<sandboxId>/workspace`
- 元数据保存在本地 SQLite `data/sandbox.db`

### Runtime

当前版本使用 `DockerRuntime`：

- 每个 `sandboxId` 对应一个独立 Docker 容器
- 宿主机目录 `data/sandboxes/<sandboxId>/workspace` 挂载到容器内 `/workspace`
- `bash` 工具通过 `docker exec` 在该容器内执行
- 同一个 `sandboxId` 停止后，目录仍保留，后续可 `resume`

## 快速开始

### 1. 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. 确保 Docker 可用

```bash
docker --version
docker info
```

默认容器镜像：

- `ubuntu:24.04`

如果本地还没有这个镜像，第一次创建 sandbox 前建议先拉取：

```bash
docker pull ubuntu:24.04
```

可通过环境变量覆盖：

- `SANDBOX_DOCKER_IMAGE`
- `SANDBOX_DOCKER_SHELL`
- `SANDBOX_DOCKER_NETWORK`
- `SANDBOX_DOCKER_MEMORY`
- `SANDBOX_DOCKER_CPUS`
- `SANDBOX_DOCKER_PIDS_LIMIT`

### 3. 启动服务

```bash
uvicorn sandbox_api.main:app --reload
```

默认监听：

- `http://127.0.0.1:8000`
- Swagger 文档：`http://127.0.0.1:8000/docs`

## API 概览

### 创建 sandbox

`POST /v1/sandboxes`

请求：

```json
{
  "tenantId": "tenant-demo",
  "metadata": {
    "agent": "test-agent"
  }
}
```

响应：

```json
{
  "sandboxId": "sbx_3f2f7a2d7f344a0b",
  "tenantId": "tenant-demo",
  "status": "ready",
  "workspacePath": "/absolute/path/to/data/sandboxes/sbx_xxx/workspace",
  "createdAt": "2026-03-28T00:00:00Z",
  "lastActiveAt": "2026-03-28T00:00:00Z"
}
```

### 查看 sandbox

`GET /v1/sandboxes/{sandboxId}`

### 恢复或确保 sandbox 目录存在

`POST /v1/sandboxes/{sandboxId}/resume`

### 删除 sandbox

`DELETE /v1/sandboxes/{sandboxId}?purge=false`

- `purge=false`：停止容器，保留目录和元数据
- `purge=true`：删除容器、目录和元数据

### 调用工具

`POST /v1/tool-call`

支持工具：

- `read`
- `write`
- `bash`

请求示例：

```json
{
  "sandboxId": "sbx_3f2f7a2d7f344a0b",
  "requestId": "req_001",
  "tool": "write",
  "args": {
    "path": "notes/hello.txt",
    "content": "hello sandbox"
  }
}
```

```json
{
  "sandboxId": "sbx_3f2f7a2d7f344a0b",
  "requestId": "req_002",
  "tool": "read",
  "args": {
    "path": "notes/hello.txt"
  }
}
```

```json
{
  "sandboxId": "sbx_3f2f7a2d7f344a0b",
  "requestId": "req_003",
  "tool": "bash",
  "args": {
    "command": "ls -la notes && cat notes/hello.txt",
    "timeoutMs": 10000
  }
}
```

## 设计说明

### 为什么同时保留 `sandboxId` 和 `requestId`

- `sandboxId`：标识要使用哪个隔离环境
- `requestId`：标识这一次工具调用，便于日志、审计、幂等控制

### 为什么同一个 sandbox 串行执行

这是 MVP 阶段最稳妥的做法，可以避免：

- 两个 bash 同时修改同一文件
- write 和 read 交错导致状态不可预测
- 多个 Agent 并发操作同一环境时出现竞态

## Docker 行为说明

容器命名规则：

- `sandbox-<sandboxId>`

容器创建时的默认行为：

- 工作目录：`/workspace`
- 挂载目录：`<host>/data/sandboxes/<sandboxId>/workspace:/workspace`
- 网络：`none`
- 内存：`512m`
- CPU：`1.0`
- PIDs 限制：`256`

这一版仍然是 MVP，下面几点你要心里有数：

- `read` 和 `write` 仍然直接操作宿主机挂载目录，不经过容器
- `bash` 超时后会终止本地 `docker exec` 进程，但不保证容器内子进程一定完全清理干净
- 默认镜像是通用 Ubuntu，不是最小安全镜像，生产环境建议自建镜像并收紧权限

### 后续建议演进

下一阶段建议加：

1. 自定义安全镜像和非 root 用户
2. TTL 与自动回收
3. 工具调用幂等
4. 网络控制
5. 资源限制
6. 审计日志
7. 真正的 tenant 鉴权
