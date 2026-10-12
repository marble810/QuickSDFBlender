# Blender 开发实时同步

状态：已实施（Blender 5.1.2 已验证一次同步／重载／Studio 恢复，以及 watch 模式下新增、删除文件自动同步；异常回退和忙碌等待未实测）

## 最终决策

- 标准库 watcher 监听扩展源码，以暂存快照请求安装目录同步和安全全量重载；不运行发布构建／测试／ZIP 打包。
- Blender 主线程 timer 桥独立于扩展包，首次在 Console 启动；不依赖 MCP 常驻连接。每个仓库只支持一个 bridge／watcher。
- 等待模态交互、笔画、Studio 切换及生成／导出任务结束；临时 Canvas 和辅助 mask 编辑要求先回到正式 key。
- 重载前刷新 dirty 图像；恢复当前项目 UUID，默认保留工作区布局和视图；显式参数或布局版本变化才重建。
- 文件同步、注册与 Studio 恢复分别报告状态；注册／同步失败回退安装文件。异步恢复失败不回退文件，进程崩溃不保证恢复。
- Undo 不保证保留；model.py／manifest／native 二进制变化保守要求关闭 Blender 后更新并重启，不强制卸载 DLL。
- 实现：`scripts/dev_sync.py`、`scripts/blender_dev_bridge.py`；使用说明：`scripts/DEV_SYNC.md`。
