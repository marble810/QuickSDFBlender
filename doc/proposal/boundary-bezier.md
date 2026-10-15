# 明暗交界线：原生 Curve 会话

状态：会话、SDF Curve 工具、Bézier handle 贴面拟合、会话内 Undo／Redo 已实施（Blender 5.1.2 已实测注册、拾取、贴面落点、原生 Pen 转交、会话内 Undo／Redo；未做真实鼠标下的拖拽与填充回归）

## 结论

Studio 增加 `Paint ↔ Restricted Curve Edit` 会话。用原生 Bézier Curve 作为交界线作者工具，并防止模式逃逸。现有 Boundary 折线编辑不是可用工具，不复活为编辑器。闭合曲线经采样、投射到目标 Mesh、转 UV 后填充当前角度的 Light/Shadow。

Curve 模式注册独立工具 `SDF Curve`，挂在原生 Curve Pen 体系上，负责把落点贴到目标 Mesh（见下节）。

## SDF Curve 工具（已实施）

- 注册与 `quicksdf.paint_view` 并列的 WorkSpaceTool：`bl_idname = "quicksdf.sdf_curve"`、`bl_label = "SDF Curve"`、`bl_context_mode = "EDIT_CURVE"`，在工具栏排在原生 Curve Pen 之后；进会话时由 `_try_activate_curve_tool` 切到它（失败则回退 `builtin.pen`／`builtin.draw`）。
- keymap 复制原生 Curve Pen 的 LMB 项：普通 LMB 交给 `quicksdf.sdf_curve_pen`，Ctrl+LMB 仍走 `curve.pen`（insert_point／delete_point），双击仍走 `curve.pen`（toggle_vector／cycle_handle_type）。
- 贴面（Cast）：普通 LMB 先用与 Blender 相同的像素阈值（`ED_view3d_select_dist_px × 0.2`，含手柄可见性规则）判断是否点击了已有控制点／手柄；是则用原生 pen 参数 `INVOKE_DEFAULT` 转交（选中／拖拽／闭合等交互留在原生 modal），否则用 `target.ray_cast`（求值 depsgraph）打目标 Mesh。
- 命中目标 Mesh 才落点：把世界坐标写入 helper 的 Bézier spline（创建时种子点直接改用，其后 `bezier_points.add(1)` 追加，保持 Cyclic），用 `curve.handle_type_set` 按工具设置里的 Handle Type（Auto／Vector，默认 Vector）计算手柄；只选中新点。未命中不落点（`PASS_THROUGH`）。
- operator `poll` 仅对 terminator helper 有效（helper 标记 + `EDIT_CURVE` + 会话 active）。
- 已知取舍：普通 LMB 不再触发原生 pen 的 segment 拖动／点击线段插入；Ctrl+LMB 的插入与删除仍可用。

## Handle 贴面拟合（已实施）

- 固定结点。手柄方向由「上一点 → 当前 → 下一点」引导（两段单位切向平均），再投影到该点 mesh 切平面，长度取邻接弦长的 `1/3`，写成 `ALIGNED`。不用最近点采样反求，避免手柄被吸飞。
- 触发：SDF Curve 落点后（≥3 点）；Enter／Tab 填充前再算一次。少于 3 点仍走 VECTOR／AUTO。不在 watchdog 或拖拽中拟合。

## Curve 会话内 Undo／Redo（已实施）

- Curve Edit 期间 `Ctrl+Z` / `Ctrl+Shift+Z` 绑定到会话自身的 `quicksdf.curve_undo` / `quicksdf.curve_redo`（SDF Curve 工具 keymap 与 `Curve` keymap 双绑定），不调用 Blender 全局 Undo：既不把模式退回 Paint，也不触发 `undo_post` 清空画笔 Undo 历史。
- watchdog 采样 helper 曲线数据（spline 类型、Cyclic、resolution、结点／手柄类型与坐标、radius／tilt、选择）。几何变化静置 0.15 秒记一步，纯选择变化不记步；原生 modal（Pen 拖拽／G／R／S／导航）期间不结算，一次拖拽只算一步。Undo／Redo 只写回快照，helper 保持 EDIT。
- 撤销栈是会话局部：进入会话时清空，Esc／Enter／退出时丢弃；到达边界后 `Ctrl+Z` 只提示无可撤销内容，不越界触碰全局历史。

## 简要细节

- 预览黑白／Toon 挂在脸 Mesh 的预览材质上，不依赖 Texture Paint；Curve Edit 时 Mesh 保持 Object Mode，二者可同屏。
- Paint：维持关闭原生 overlay。Curve Edit：打开 overlay 以显示手柄，helper 曲线 `show_in_front`，Tab／确认／Esc 直接回 Paint，不停留在 Curve Object Mode。
- 防逃逸：仅 helper 可选；active／mode 偏离则拉回 Curve Edit 或干净退回 Paint。不使用吃掉原生 Pen／手柄快捷键的阻塞会话 modal。工具 keymap 只在该 tool 激活时占用 LMB；G／R／S、框选、Cyclic、手柄类型走原生 Curve keymap。
- 贴面未命中不落点；补充：普通 LMB 由 SDF Curve 工具接管，拖动手柄／G／R／S／框选／Cyclic 仍走原生。
- 可复用的只有 UV 闭合填充、岛边界、3D hit→UV；`boundary.py` 的绘制 UI、未接线的 `BezierKnot` 不作为作者层。
- 曲线按角度保存在原生 Curve 物体上，可再编辑；写入当前角度生成层、不覆盖手绘 Coverage。
- 填充时机：Enter／Tab 确认时填充；Esc 不填充直接回 Paint。拖动手柄时不实时填。
