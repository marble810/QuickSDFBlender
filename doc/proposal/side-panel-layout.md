# Side panel 布局：Advanced 平铺与 Front 折叠

状态：已实施（结构变更，未在 Blender 中逐项目视确认）

## 结论

1. 删除 `Advanced` 收纳面板，其内容全部平铺进 `Quick SDF Paint` 主面板，不再需要点开。
2. Front 控件只保留一处：原先主面板的 Front 框与 `Advanced → Character Axes` 合并为一个。
3. 轴按钮改为三列：第 1 列 `+X`／`-X`，第 2 列 `+Y`／`-Y`，第 3 列 `+Z`／`-Z`。
4. Front 增加小箭头折叠；折叠状态存 `AddonPreferences.front_axes_open`（默认展开），
   `UI Settings` 面板里也有同名开关可恢复。
5. `Output Packing` 与 `Additional Masks` 不再嵌套在 `Advanced` 下，升为顶层面板并改为默认展开。

## 简要细节

- 面板顺序：`Quick SDF Paint` 10 → `Output Packing` 20 → `Additional Masks` 30
  → `Light Indicator Center` 50 → `UI Settings` 90。
- 主面板顺序：日常编辑控件（Studio 中）／`Open Quick SDF Paint`（未开）
  → Base 过期警告 → 方向警告 → Object／Face Area／UV Map／解像度
  → Front（可折叠，展开时含 `Use This View as Front`）→ Mesh Normals
  → Adjust Shadow Guide → Mirror → Angle Keys → Rebake Base → Review / Recovery
  → 导出调整提示 → 诊断信息 → 删除项目 →（Studio 中）导出与退出。
- 创建状态：选择面提示／创建按钮 → 解像度、初期化、Guide Padding、Source Mask → Front。
  未选中网格时也仍会显示创建设置（与旧 Advanced 行为一致）。
- Mesh Normals 开关原先在 Studio 状态下由主面板绘制、在 `Advanced` 中又一次绘制（置灰规则不同），
  现在只保留一处，沿用 `Advanced` 的置灰规则（`preview_enabled` 为假时禁用）。
- Front 折叠的箭头不带当前轴名；其余段落仍是带标题的 box，但不可折叠。
