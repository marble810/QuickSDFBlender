# Quick SDF Paint 0.9.0

Quick SDF Paintは、Blender 5.1／5.2 LTS上でトゥーンレンダリング用の顔影スレッショルドマップを作成するWindows向けExtensionです。法線から作られた影ガイドを下描きにして、気になる部分だけを直せます。

角度別の白黒マスクをペイントし、SDF距離補間を使って、光の向きに応じた影の切替値を1枚の16-bit RGBAテクスチャにまとめます。SDFはマスク境界を補間するための内部処理であり、最終出力はSDFそのものではなくスレッショルドマップです。

角度ごとの画像管理や整合性チェックを意識する必要はありません。アーティストは顔を見ながら `Light` / `Shadow` を塗り、スレッショルドマップを書き出すだけです。

## 5つの操作

1. 顔のMeshを選択する
2. `N` → `Quick SDF` → `Create & Edit`
3. 下のタイムラインで角度を選ぶ
4. `Light` または `Shadow` を選んで、3Dか2Dへ塗る
5. `Export Threshold Map`

顔専用の面が独立したMaterial Surfaceなら、`Create & Edit`だけで開始できます。顔と身体が同じMaterial／連続したトポロジーに含まれるモデルでは、先に`Select Face Area…`を押し、Edit Modeで顔として扱うPolygonを選択して`Create & Edit Selected Faces`を押します。Quick SDF PaintはObject名、Material名、面積から「顔らしい場所」を推測しません。

`Create & Edit` は、現在のポーズと評価済み法線から9段階の影ガイドを作り、中央の `Side Light 90°` キーを選択した状態で専用のQuick SDF Paintワークスペースを開きます。タイムラインの0～100%は物理的なライト角度ではなく、影がLightへ切り替わる制作上の進行度です。0%では斜め後ろから光が入り始め、50%でライトが厳密に真横を向き、100%で顔全体が明部になります。真横光でも白黒の面積が半分になるとは限りません。左右対称は既定でONです。片側だけ直せば反対側の出力も自動生成されます。

キーの間へさらに形を描き込みたい場合は、タイムラインを目的の位置へ動かしてそのまま塗ります。一時Canvas上の画素が0.5境界を実際に越え、書き出し結果が変わる時だけCustomキーが自動追加されます。低Strengthの筆跡は一時Canvasへ蓄積され、スクラブ、同色の一筆、境界を越えない修正だけではProjectのキーや永続Imageは増えません。既存キーから内部角度2°以内は吸着し、上限は片側16キーです。

一筆はBlender標準Texture Paintの操作感を保ったまま、選択中の角度へ即時反映されます。書き出し時に角度のつながりを非破壊で自動調整するため、Propagate、Validate、反対側生成の追加操作は不要です。

書き出すRGBAの割り当てはプロジェクトごとに変更できます。通常は設定を触らず、そのまま1ボタンで書き出せます。

Texture Paintに必要なDisplayだけをRGBA8 Imageとして保持し、Baseと手描き範囲は検証付き1-bit bitplaneとして保存します。キーを追加しても補助レイヤーがRGBA Imageとして増えないため、メモリ消費を抑えながら細かな角度制御ができます。

詳しい使い方は[Web操作ガイド](https://yeczrtu.github.io/QuickSDFBlender/)または[日本語ユーザーガイド](docs/USER_GUIDE_JA.md)を参照してください。

仕組みと制作判断を詳しく知りたい場合は、次の解説記事を参照してください。

- [顔影スレッショルドマップとは — 仕組みと制作方法を体系的に解説](https://yeczrtu.github.io/QuickSDFBlender/articles/face-shadow-threshold-map/)
- [角度別マスクを1枚にまとめる方法 — SDF距離補間の比較検証](https://yeczrtu.github.io/QuickSDFBlender/articles/sdf-threshold-interpolation/)
- [Blenderで顔影スレッショルドマップを作る実践ワークフロー](https://yeczrtu.github.io/QuickSDFBlender/articles/blender-threshold-map-workflow/)

## インストール

対応環境はBlender 5.1／5.2 LTS、Windows x64です。同じExtension ZIPを両バージョンで使用できます。

1. [GitHub Releases](https://github.com/yeczrtu/QuickSDFBlender/releases/latest)から `quick_sdf_blender-0.9.0-windows-x64.zip` を取得する
2. Blenderで `編集` → `プリファレンス` → `エクステンションを入手` を開く
3. 右上メニューから `ディスクからインストール` を選ぶ
4. 取得したZIPを展開せずに選択する
5. 3D Viewで `N` を押し、`Quick SDF` タブを開く

対象Meshにはローカルの0–1 UV Mapが必要です。明示的に選んだFace AreaはMaterial Slotをまたいでも構いません。ただし、Face Areaと対象外の面が同じUV座標を共有している場合は別々にペイントできないため、UVを分けてください。初期解像度は1024です。

## Quick SDF Paintの画面

| 場所 | 用途 |
|---|---|
| 左上 | 2DのUVペイント |
| 右上 | 顔を見ながら3Dペイント／プレビュー |
| 下 | 角度キーを選ぶタイムライン |

通常作業で使うのは `Light`、`Shadow`、`Mirror On`、表示切替、`Export`だけです。解像度、UV、軸、Mirror方式、Boundaryなどは、3D Viewの `Quick SDF` → `Advanced` にまとめています。

タイムラインのプレイヘッドは1本だけです。9個の標準キーは0、12.5、25、37.5、50、62.5、75、87.5、100%へ等間隔に並び、50%は直接ベイクされた `Side Light 90°` キーです。レールをドラッグしている間は2D／3Dでキー間を連続確認できます。既存キー付近で離すとそのキーへ吸着し、それ以外では一時Canvasを準備します。Customキーへ確定するのは、そこで0.5境界を越える変更を加えた時だけです。

## スレッショルドマップの出力

`Export Threshold Map`を押すと、検証・SDF距離補間・パッキング・保存を一度に行い、顔影スレッショルドマップを16-bit RGBA PNGとして1枚出力します。SDF距離補間は角度別の白黒マスクから滑らかな切替値を求めるために使われます。

出力先シェーダーの対応例として、既定の`Packing: lilToon`はlilToon／liltoonUE向けに次のチャンネル構成を使用します。

| Channel | 既定の内容 |
|---|---|
| R | キャラクター右側からの光に対するスレッショルド |
| G | キャラクター左側からの光に対するスレッショルド |
| B | 角度別の顔影制御を使う領域で0、通常法線シェーディングへ戻す領域で65535 |
| A | 通常の影で65535、影を無効化する領域で0 |

角度のつながりに矛盾があっても、元のペイントを変更せず、書き出し用データだけを自動調整します。初回だけ保存先を選び、同じパスへの上書き時は確認が表示されます。エンジンではNon-Color／Dataテクスチャとして扱ってください。

別のシェーダー向けに並べ替える場合は、`Quick SDF` → `Advanced` → `Output Packing`で`Customize`を押します。R/G/B/Aの各行へRight Threshold、Left Threshold、SDF Area、Shadow Strength、Custom Mask、Constantを割り当て、必要な行だけDirect／Invertを切り替えられます。このレシピはProject内に保存され、ほかのProjectには影響しません。ノードグラフや数式は使いません。

`Additional Masks`では、全角度に共通する`SDF Area`と`Shadow Strength`を3D／2Dでペイントできます。任意のCustom Maskも追加でき、既存のBlender ImageからR/G/B/A/Luminanceのいずれかを読み込めます。マスク編集を終えるときは`Back to Face Shadow`で角度別ペイントへ戻ります。

最終生成と明示的な`Rebake Base`はバックグラウンドで実行されます。高解像度ではQuick SDF Paintワークスペース内に進捗と `Cancel` が表示されるため、Blenderを固めずに中止できます。保存先やディスクの問題で失敗した場合は、パスを保持したまま `Retry Export` できます。

## 出力とSDF

Quick SDF Paintが出力するのは「顔影スレッショルドマップ」です。SDFは角度別マスクの境界を補間し、画素ごとの切替値を求める生成手法としてだけ使用します。

統一された名称はありません。公開されているシェーダーや資料では、次の表記が実際に使われています。

- lilToon：[`SDF Face Shadow`、`Shadow SDF mode`](https://github.com/lilxyzw/lilToon/blob/master/Assets/lilToon/CHANGELOG.md)
- PotaToon：[`Face SDF`、`FaceSDF textures`、`Face SDF Tex`](https://potatoon.dev/en/features/material-settings)
- ChiliMilk URP Toon：[`SDF_FaceShadow`、`sdf shadow mask`](https://github.com/ChiliMilk/URP_Toon)
- Anime Shading Plus：[`SDF-based face shadow map`、`Face Shadow Map`](https://erichu33.github.io/ASPDocs/en/articles/face-shadow-map-creation-and-baking-workflow.html)
- entropy622の公開シェーダー実装：[`face SDF shadow`](https://github.com/entropy622/Unity-URP-Shader-For-Starrail-Characters/blob/master/README_EN.md)
- Natane Toon Shader：[`SDF Shadow Map`、`SDF Shadow Texture`](https://github.com/natane010/natane_toon_shader/blob/v1.1.5/Website/en/params/lighting/sdf-shadow.html)
- akasakiの公開ツール：[`Shadow Threshold Map`](https://github.com/akasaki1211/sdf_shadow_threshold_map)
- Hi-Fi RUSHの事例記事：[`Face Threshold Map`](https://cgworld.jp/article/202306-hifirush01.html)

これらは各実装の機能名・モード名・テクスチャ名です。本書ではQuick SDF Paintの出力内容を「顔影スレッショルドマップ」と呼びます。

> [!NOTE]
> **UE5版からの系譜**
>
> Quick SDF Paintは、UE5向けの[QuickSDFTool](https://github.com/yeczrtu/QuickSDFTool)で培った角度別マスクとSDF距離補間の考え方を基に、Blenderのアーティスト向け操作体験として再設計した後継プロジェクトです。UE5版の開発は終了しており、既存のソースコードとリリースは参照用として引き続き公開されています。

## Development

Live development sync (without running the release gate):
[`scripts/DEV_SYNC.md`](scripts/DEV_SYNC.md). A standard-library watcher syncs
Python/UI changes to the installed Blender extension and requests safe reloads.

```powershell
# Blender 5.1（既定）
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_extension.ps1

# Blender 5.2 LTS
.\scripts\build_extension.ps1 `
  -BlenderPath 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
```

正式リリース前は同じゲートを5.1と5.2 LTSの両方で実行します。このリリースゲートはunit test、Blender背景／実ウィンドウsmoke、Texture Paint Autosave、2048pxの性能・メモリ予算、保存再読込、Extension validation、ZIPのbyte検証、隔離ユーザー領域への実インストールを順に実行します。

## License

Quick SDF Paintは[GNU General Public License v3.0 or later](LICENSE)で公開されています。同梱native coreも同じライセンスです。利用しているアルゴリズムとランタイムの帰属情報は[Third-Party Notices](THIRD_PARTY_NOTICES.md)に記載しています。
