# 明暗交界线：原生 Curve 会话

状态：待实施（仅记录研究结论，不授权实施）

## 结论

用 Blender 自带 Bézier Curve 作为交界线作者工具，前提是 Studio 增加 `Paint ↔ Restricted Curve Edit` 会话，并防止模式逃逸。现有 Boundary 折线编辑不是可用工具，不复活为编辑器。闭合曲线经采样、投射到目标 Mesh、转 UV 后填充当前角度的 Light/Shadow。记录可行性，具体交互（含拖动手柄时实时填还是松手再填）待确认。

## 简要细节

- 预览黑白／Toon 挂在脸 Mesh 的预览材质上，不依赖 Texture Paint；Curve Edit 时 Mesh 保持 Object Mode，二者可同屏。
- Paint：维持关闭原生 overlay。Curve Edit：打开 overlay 以显示手柄，helper 曲线 `show_in_front`，Tab／确认／Esc 直接回 Paint，不停留在 Curve Object Mode。
- 防逃逸：仅 helper 可选；active／mode 偏离则拉回 Curve Edit 或干净退回 Paint。不使用吃掉原生 Pen／手柄快捷键的阻塞 modal。
- 吸附用 Face Project 或求值点重投射到目标 Mesh，不用 Cast 修改器。贴面为近似。
- 可复用的只有 UV 闭合填充、岛边界、3D hit→UV；`boundary.py` 的绘制 UI、未接线的 `BezierKnot` 不作为作者层。
- 曲线保持为可再编辑的源，写入生成层、不覆盖手绘 Coverage。
