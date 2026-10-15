# blender-mcp-connect 重启-重连

状态：已实施（下游仓 `F:/Git/blender-mcp-enhanced`，版本 0.2.0；未提交、未发 tag，pi 侧仍用 PyPI 0.1.0，需等发布或在 mcp.json 指向本地路径后生效）

## 背景：断联逻辑链（2026-10-15 viewportui 会话事故）

- pi 以 stdio 启动 `uvx blender-mcp-connect==0.1.0`，作为常驻 MCP server 进程；每次工具调用经 `resolve_endpoint()` 解析端点。
- Blender 扩展 `blender_mcp_connect` 每个进程在 `%LOCALAPPDATA%\BlenderMCPConnect\instances\<uuid>.json` 发布 descriptor（instanceId = 每进程新生成的 UUIDv4），正常 quit/卸载时撤文件。
- MCP server 发现到"唯一健康实例"后把它 pin 进进程级全局 `_selected_instance`（session affinity，"Never silently switch to another"）。
- 事故时序：旧 Blender 61608 被 pin（旧 id 5c5293ff…）→ 会话内经 MCP 完成保存并 spawn 新实例 20028（新 id f0895cff…，descriptor 21:42 发布）→ 定时器 quit 旧实例（旧 descriptor 随之撤除）→ 下一次 MCP 调用：pin 仍指向已死 id，`probe_instance()` 失败 → 抛 "The selected Blender instance (…) is no longer reachable. Restart the MCP server or set BLENDER_MCP_INSTANCE…"；此后所有调用走同一死分支，直到 MCP server 进程重启。
- 根因：instanceId 以"进程"为身份，重启必然换 id；而客户端亲和性按 id 硬绑定、无重选通道，且 `BLENDER_MCP_INSTANCE` 只能在启动时注入，运行中的 stdio server 无法改。

## 最终决策

- 只改 uvx 包（`blmcp`），不改桥协议与 descriptor schema（executable/blendFile/startedAt 字段已存在）。
- 显式工具重选：新增 MCP 工具 `reconnect_blender_instance(instance_id=None)`：无参时扫描健康实例，唯一健康即改 pin 并返回其 id/pid/blend/executable；多健康返回列表不猜测；显式传 id 则直接 pin。
- 亲和性自动重连：pin 失效时不立即 fail-closed，而是扫描健康实例；若恰好一个健康、且 `blenderExecutable` 与 `blendFile`（双方皆空亦算匹配）与 pin 时刻快照一致 → 改 pin，并在该次调用的结果前缀一次性提示 `reconnected: {from,to,pid,blendFile}`；不满足唯一性/匹配性则维持 fail-closed，报错文本改为附健康实例表（id/版本/blend/pid），供 agent 调 reconnect 工具。
- 一键重启工具：新增 `restart_blender_instance(save: bool)`：先留存 pin 快照；经现有 execute 通道执行 save_mainfile（可选）+ spawn 新实例（`blender <blend>` 分离进程，扩展 autostart 自会起桥）+ 定时 quit 旧实例；随后按 1s 间隔轮询 descriptor（executable 匹配、blendFile 匹配、startedAt 晚于动作时刻、pid 不同于旧 pid），deadline 约 60s，找到即 pin，超时返回健康实例列表。
- 环境回退：设置 `BLENDER_MCP_INSTANCE` 时禁用自动重连（严格模式，仅手改 pin 生效）。
- 顺手项：客户端扫描中对 probe 失败且 pid 已死的 descriptor 做尽力删除（校验 instanceId 属主），避免运行目录积累死文件（当前已有 9 个陈旧文件）。
- 不做：静默无限期切任意实例；对发送后失败请求的重放（沿用 no-replay 语义）。

## 实现落点

- `blmcp/tools_helpers/connection.py`：`resolve_endpoint()` 分支 3；`_selected_instance` 增加匹配快照（blender_executable、blend_file）与 pin 时刻。
- `blmcp/tools/reconnect_blender_instance.py`、`blmcp/tools/restart_blender_instance.py`：按现有 tools 的 register(mcp) 模式注册；restart 用 `blender_cli`/descriptor 元数据解析 blender 可执行路径。
- pi 侧无需改动；实施后事故场景（保存→spawn→quit→继续驱动）无需重连 pi MCP。

## 参考（事故与现状证据）

- 会话：viewportui pane（wV:p5），报错原文见 2026-10-15 13:59 条目。
- 现状代码：uvx 缓存 `blmcp/tools_helpers/connection.py`（pin 与错误文案）、`instance_discovery.py`（discover/probe/select）；Blender 扩展 `instance_registry.py`、`mcp_to_blender_server.py`（start/stop/refresh_descriptor）。
