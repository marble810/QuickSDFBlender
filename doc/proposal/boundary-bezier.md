# 明暗交界线：原生 Curve 会话

状态：会话已实施；Terminator Pen 贴面待实施

## 结论

Studio 增加 `Paint ↔ Restricted Curve Edit` 会话。用原生 Bézier Curve 作为交界线作者工具，并防止模式逃逸。现有 Boundary 折线编辑不是可用工具，不复活为编辑器。闭合曲线经采样、投射到目标 Mesh、转 UV 后填充当前角度的 Light/Shadow。

点击落点贴面：自建 EDIT_CURVE 的 WorkSpaceTool（`quicksdf.terminator_pen`），只包 LMB。空处点击用视口 `scene.ray_cast` 打到目标 Mesh 再加点／挤出；点在已有控制点或手柄上则交给 `curve.pen`。进会话时切到该工具。`curve.pen` 不能子类化，无点创建钩子。

## 简要细节

- 预览黑白／Toon 挂在脸 Mesh 的预览材质上，不依赖 Texture Paint；Curve Edit 时 Mesh 保持 Object Mode，二者可同屏。
- Paint：维持关闭原生 overlay。Curve Edit：打开 overlay 以显示手柄，helper 曲线 `show_in_front`，Tab／确认／Esc 直接回 Paint，不停留在 Curve Object Mode。
- 防逃逸：仅 helper 可选；active／mode 偏离则拉回 Curve Edit 或干净退回 Paint。不使用吃掉原生 Pen／手柄快捷键的阻塞会话 modal。工具 keymap 只在该 tool 激活时占用 LMB；G／R／S、框选、Cyclic、手柄类型走原生 Curve keymap。
- 贴面用视口射线 `scene.ray_cast`，未命中不落点。不用 Cast／Shrinkwrap 修改器。第一版点击即落点；拖动手柄仍用原生 Pen。operator `poll` 仅对 terminator helper 有效。
- 可复用的只有 UV 闭合填充、岛边界、3D hit→UV；`boundary.py` 的绘制 UI、未接线的 `BezierKnot` 不作为作者层。
- 曲线按角度保存在原生 Curve 物体上，可再编辑；写入当前角度生成层、不覆盖手绘 Coverage。
- 填充时机：Enter／Tab 确认时填充；Esc 不填充直接回 Paint。拖动手柄时不实时填。
