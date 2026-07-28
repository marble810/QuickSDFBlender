# Quick SDF Paint 0.8.1

0.8.1は、透明な顔オーバーレイや目が顔面の前に重なるモデルで、3Dペイントが別の面へ吸われる問題を修正した更新です。

## 複数Materialの顔モデル

- 保存時のアクティブMaterialが透明ディテールでも、使用中の明確な顔面Materialを安全に提案します。
- Studio中は対象Materialの面だけをProjection Paintの対象にします。
- 透明オーバーレイ、目、まつげなど、手前にある別Materialの面が顔面へのストロークを遮らなくなりました。
- 非表示にしていた面は表示せず、その状態を維持します。

## Blender状態の復元

- Studio開始前のFace Selection Mask、面選択、非表示、アクティブMaterial、アクティブUVを記録します。
- Studio終了、保存、読込、Undo、Project切替、Add-on無効化で元の状態へ戻します。
- 保存後は対象面の分離を自動的に再開します。
- Object／Mesh名をStudio中に変更した場合や、保存に失敗した場合も安全に復旧します。

## 対応環境

- Blender 5.1／5.2 LTS
- Windows x64
