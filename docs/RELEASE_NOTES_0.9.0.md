# Quick SDF Paint 0.9.0

0.9.0は、Material名や面積から顔を推測する方式を廃止し、アーティストが指定したPolygonを唯一のFace Areaとして扱います。

## 明示的なFace Area

- 顔専用のSurfaceなら、従来どおり `Create & Edit` の1操作で開始できます。
- 顔と身体が同じMaterialまたは連続したトポロジーに含まれるモデルでは、`Select Face Area…`を押し、Edit Modeで顔のPolygonを選択してから `Create & Edit Selected Faces` を押します。
- Object名、Material名、Polygon面積、骨名、位置から顔を推測しません。
- Face Areaは複数のMaterial Slotをまたげます。
- 選択した範囲を、法線ガイド、3D／2D Paint、Preview、Boundary、Symmetry、Exportで一貫して使用します。

## ペイントとプレビューの分離

- 3D Projection PaintはFace Areaの面だけを対象にします。
- 2D CanvasでFace Area外へ入った変更はstroke確定時に破棄します。
- 同じMaterialを身体と共有していても、Preview OverlayはFace Areaだけへ表示し、身体の元Material表示を維持します。
- Face Areaが複数Materialにまたがる場合は、使用する全Slotを一時Previewへ切り替え、終了／保存／例外時にすべて復元します。

## UVの安全確認

- Face Areaと対象外の同一Material面が同じUV座標を共有している場合は、1枚のテクスチャで分離できないためProject作成を停止します。
- Face AreaのUV占有範囲をbinary dataとしてProjectに保持し、SDF Areaの初期値と2D Paint制限に使用します。

## 対応環境

- Blender 5.1／5.2 LTS
- Windows x64
