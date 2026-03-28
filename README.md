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
sandbox-api/
  README.md
  config/
    sandbox.toml         # 运行时和调度上限配置
  pyproject.toml
  sandbox_api/
    main.py              # FastAPI 入口
    config.py            # 配置加载
    db.py                # SQLite 初始化
    errors.py            # 领域错误
    schemas.py           # API 请求/响应模型
    services/
      path_guard.py      # 路径安全检查
      sandbox_manager.py # Sandbox 生命周期和元数据管理
      tool_executor.py   # read/write/bash 调度
      workspace_limits.py# 工作区大小计算
    runtime/
      base.py            # Runtime 抽象
      docker.py          # Docker runtime
  data/
    sandboxes/           # 运行时自动创建
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
- 多个不同 `sandboxId` 的 `bash` 调用通过全局并发信号量限流
- sandbox 创建、恢复、删除通过独立生命周期信号量限流

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

- `SANDBOX_CONFIG_PATH`
- `SANDBOX_DOCKER_IMAGE`
- `SANDBOX_DOCKER_SHELL`
- `SANDBOX_DOCKER_NETWORK`
- `SANDBOX_DOCKER_MEMORY`
- `SANDBOX_DOCKER_CPUS`
- `SANDBOX_DOCKER_PIDS_LIMIT`
- `SANDBOX_WORKSPACE_SOFT_LIMIT_BYTES`
- `SANDBOX_DOCKER_STOP_TIMEOUT_SECONDS`
- `SANDBOX_MAX_SANDBOXES`
- `SANDBOX_MAX_CONCURRENT_BASH`
- `SANDBOX_MAX_CONCURRENT_LIFECYCLE`

### 3. 启动服务

```bash
uvicorn sandbox_api.main:app --reload
```

默认监听：

- `http://127.0.0.1:8000`
- Swagger 文档：`http://127.0.0.1:8000/docs`

### 4. 使用 Remote MCP Server

当前项目已经把 `FastMCP` 的 remote server 挂到现有 FastAPI 应用上。
启动 `uvicorn` 后，可直接通过 Streamable HTTP 访问：

```bash
http://127.0.0.1:8000/mcp/
```

这个 MCP Server 不会改动现有 HTTP API shape，而是复用同一套 `SandboxManager` 和 `ToolExecutor`。
它会在第一次 `tools/call` 时自动创建一个 sandbox，并自动生成每次调用的 `requestId`。

如果你还想用本地 `stdio` 模式，也可以：

```bash
python -m sandbox_api.mcp.stdio
```

可选环境变量：

- `SANDBOX_MCP_TENANT_ID`：创建 sandbox 时使用的 tenant，默认 `default`
- `SANDBOX_MCP_SANDBOX_ID`：固定复用某个已有 sandbox，适合多次重连调试
- `SANDBOX_MCP_STOP_ON_EXIT`：进程退出时是否执行 `purge=false` 停止 sandbox，默认 `true`

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

### MCP Adapter 怎么补这两个字段

- `sandboxId`：由 MCP 会话持有，不要求模型显式传递
- `requestId`：由 MCP Server 每次调用自动生成，避免全局主键冲突
- 对外暴露的 MCP tools 仍然是 `read`、`write`、`bash`
- MCP `tools/call` 最终会被转成当前的 `/v1/tool-call` 等价调用

### 为什么同一个 sandbox 串行执行

这是 MVP 阶段最稳妥的做法，可以避免：

- 两个 bash 同时修改同一文件
- write 和 read 交错导致状态不可预测
- 多个 Agent 并发操作同一环境时出现竞态

### 多个不同 sandboxId 同时到来时怎么处理

当前实现采用的是“分层限流”：

- 同一个 `sandboxId`：串行执行，避免同一工作区竞态
- 不同 `sandboxId` 的 `bash`：允许并行，但受 `max_concurrent_bash` 全局上限控制
- 创建、恢复、删除：允许并行，但受 `max_concurrent_lifecycle` 上限控制
- 新建 sandbox：如果已达到 `max_sandboxes`，直接返回 `429`

这种策略适合当前阶段，因为它兼顾了三件事：

- 避免单个 sandbox 内部状态混乱
- 避免大量容器同时执行命令把宿主机打满

## Cherry Studio 测试

如果你想把这个项目作为 Cherry Studio 的 MCP Server 使用，推荐直接连 remote URL：

- Transport: `Streamable HTTP`
- URL: `http://127.0.0.1:8000/mcp/`

如果想固定复用一个 sandbox，可以额外设置环境变量：

- `SANDBOX_MCP_SANDBOX_ID=<已有 sandboxId>`

如果你更想用本地进程方式，也可以继续配置 stdio：

- Command: `/Users/zhongziwen/Documents/Code/sandbox-api/.venv/bin/python`
- Args: `-m sandbox_api.mcp.stdio`
- Cwd: `/Users/zhongziwen/Documents/Code/sandbox-api`

第一次工具调用时，MCP Server 会自动创建 sandbox，所以只做 `initialize` / `tools/list` 时不会触发 Docker。
真正执行 `bash` 时，需要本机 Docker 可用，并且当前运行服务的进程对 Docker socket 有访问权限。
- 保持实现简单，便于后续切换成更复杂的调度器

如果后面请求量更大，可以继续演进成这些方案：

1. 全局等待队列
   所有不同 sandbox 的请求先进统一队列，按先来先服务取执行权。
2. 按 tenant 公平调度
   每个 tenant 分配独立配额，避免一个大客户占满全部 bash 并发。
3. 分级优先级队列
   `read/write` 高优先级，长时间 `bash` 低优先级，减少交互卡顿。
4. 拒绝而不是等待
   当全局并发已满时直接返回 `429`，让上游 Agent 自己退避重试。

MVP 阶段我推荐继续使用当前这套：

- 同 sandbox 串行
- 不同 sandbox 受全局并发限制并排队等待
- 创建数量超过上限时直接拒绝

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
- 工作区软限制：`256 MiB`

这一版仍然是 MVP，下面几点你要心里有数：

- `read` 和 `write` 仍然直接操作宿主机挂载目录，不经过容器
- `bash` 超时后会重建该 sandbox 容器，以更彻底地清理容器内残留进程
- 默认镜像是通用 Ubuntu，不是最小安全镜像，生产环境建议自建镜像并收紧权限
- 工作区磁盘限制目前是“软限制”：`write` 会在写入前拦截，`bash` 会在执行后回报是否超过限制，但不会做真正的内核级磁盘配额

## 配置文件

默认配置文件在 [sandbox.toml](/Users/zhongziwen/Documents/Code/sandbox-api/config/sandbox.toml)。

```toml
[docker]
image = "ubuntu:24.04"
shell = "/bin/bash"
network = "none"
memory = "512m"
cpus = "1.0"
pids_limit = 256
workspace_soft_limit_bytes = 268435456
stop_timeout_seconds = 1

[scheduler]
max_sandboxes = 20
max_concurrent_bash = 4
max_concurrent_lifecycle = 2
```

这些配置分别控制：

- `memory`：单个 sandbox 容器的内存上限
- `cpus`：单个 sandbox 容器可用 CPU 上限
- `pids_limit`：单个 sandbox 容器最大进程数
- `workspace_soft_limit_bytes`：工作区软限制
- `max_sandboxes`：系统允许保留的最大 sandbox 数量
- `max_concurrent_bash`：不同 sandbox 并行执行 bash 的最大数量
- `max_concurrent_lifecycle`：创建、恢复、删除等生命周期操作的最大并发数
