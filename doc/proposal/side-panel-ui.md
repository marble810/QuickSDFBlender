# Side panel 代码结构统一

状态：已实施（Blender 5.1.2 后台注册检查通过；侧栏像素与折叠状态未改，外观优化留给后续布局阶段）

## 结论

3D View 侧栏 `Quick SDF` tab 由 `quick_sdf_blender/ui/` 单一拥有。面板顺序写在一张显式表里，
不再依赖 `__init__._modules()` 的注册顺序。所有跨界面重复的控件行集中到 `ui/widgets.py`。
绘制回调里不再有业务逻辑。

## 结构

```
quick_sdf_blender/
  ui/
    __init__.py        PANELS 面板表 + CLASSES + tag_editors_for_redraw
    base.py            SidebarPanel / ProjectSidebarPanel / ImageEditorPanel
    context.py         项目与编辑状态的唯一来源
    widgets.py         共用控件行
    panels/            launcher advanced packing masks settings image light_center
```

面板顺序（`bl_order`）：`launcher 10` → `advanced 20` → 子面板 `output_packing 30`
→ `additional_masks 40` → `light_center 50` → `ui_settings 90`。与重构前的实际顺序一致。

## 简要细节

- `ui/context.py` 提供三组入口，替代原先散落在六个模块里的判断：
  `sidebar_project()`（侧栏）、`studio_workspace_project()`（Timeline 与底部浮层）、
  `viewport_project()`（视口叠加层，含 gizmo 与 mask 编辑门控）。三者语义本就不同，
  只统一其下的零件：`session()`、`session_project()`、`is_studio_active()`、
  `curve_edit_active()`、`editing_aux_mask_uuid()`、`object_project()`、`front_axis_id()`。
- `ui/widgets.py` 收拢重复控件：`paint_value_row()`（原先侧栏、mask、工具 header 三份）、
  `display_row()`、`uv_padding_row()`、`normals_toggle()`、`export_section()`、
  `header_status_row()`、`front_axis_row()`、`mirror_confirmation()`、`aux_edit_status()`、`section()`。
- `tools.py` 只剩 WorkSpaceTool 定义、键位与 `_draw_tool_settings`；后者改调共用控件，
  显示结果与重构前一致（含 `_operator_exists` 保护与 Image Editor 专属选项）。
- `Light Indicator Center` 面板由 `lighting_indicator.py` 迁入 `ui/panels/light_center.py`；
  该模块只保留指示器绘制与两个 operator。侧栏顺序不变。
- 业务逻辑移出绘制层：`runtime.matching_world_front_axis()`、`runtime.packing_channel_map()`、
  `runtime.packing_channel_counts()`、`runtime.packing_channel_error()`（返回 token，文案留在面板）。
  aux mask 查表统一到 `runtime.aux_mask_for_uuid()`；删除 `model.aux_mask_for_uuid()` 与
  `ui.py` 内的两份副本。
- 删除空壳 `ui.register_draw_handlers()` / `ui.register_keymaps()` 及其调用点
  （`preview.py`、`__init__.py`）。
- i18n：侧栏与工具 header 的全部 `text=` 走 `tr()`，`i18n.py` 补齐 21 条日文条目。
- 保持不变的公开接口：`ui.CLASSES`、`ui.PANELS`、`ui.QSDF_PT_*`、`ui.QSDF_UL_aux_masks`、
  `ui.tag_editors_for_redraw()`、`viewport_lighting._project`（冒烟脚本会 monkeypatch 它）。
- 与清单的两处偏差：导出块保留 `export_section()` 与 `header_status_row()` 两个函数，
  因为侧栏是“进度框 + 消息列”，header 是单行，强行合并只会增加分支；packing 的
  `_packing_meaning()` 文案留在面板，只有校验进入 runtime。

## 本轮不做

- 侧栏外观、间距与面板分组不在本文件范围；后续的布局改动见 `side-panel-layout.md`。
- `i18n.py` 中已无人引用的历史条目未删除，仅补充缺失条目。

## 安装状态

- 冒烟检查：`python -m py_compile` 全部通过；Blender 5.1.2 后台用 `addon_utils.enable()`
  注册通过，`ui.PANELS`、`ui.CLASSES`、面板继承与共享谓词均如预期。
- 开发同步：`ui/` 包已热重载进开发用 Blender（bridge 报 `reloaded`）。
- 尚未生效的文件：`ui/panels/{advanced,launcher,packing}.py`（只少一行多余的 `import bpy`，
  无行为差异）、`i18n.py`（21 条新日文条目）、`model.py`。
- `model.py` 无法热装：dev bridge 把 `model.py`、manifest 与原生库一起列为必须重启的文件，
  且安装副本自上一个 commit 起就缺少 `terminator_object`。需要关闭 Blender 后复制安装副本，
  再重启 Blender 与 bridge。
