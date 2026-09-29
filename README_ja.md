# Amazon Location Service Plugin

他の言語で読む: [英語](./README.md)

![logo](img/logo.png)

QGISでAmazon Location Service v2の機能を利用するプラグインです。  

- [QGIS](https://qgis.org)  
- [Amazon Location Service](https://aws.amazon.com/location)  

## QGIS Python Plugins Repository

[Amazon Location Service Plugin](https://plugins.qgis.org/plugins/location_service)  

## 利用方法

### Amazon Location ServiceのAPIキー作成

![location-service](img/location-service.png)

[Amazon Location Service v2のAPIキー作成](https://memo.dayjournal.dev/memo/amazon-location-service-007)  

### QGIS Pluginのインストール

![plugin](img/plugin.png)

1. `プラグイン` → `プラグインを管理およびインストール`を選択
2. `Amazon Location Service`で検索

プラグインは[zipファイル](https://github.com/MIERUNE/qgis-amazonlocationservice-plugin/releases)を読み込みでもインストール可能

### メニュー

![menu](img/menu.png)

- `Config`: リージョン名とAPIキーを設定
- `Maps`: 地図表示機能
- `Places`: 検索・ジオコーディング機能（Processingアルゴリズム）
- `Routes`: ルーティング機能（Processingアルゴリズム）
- `Terms`: 利用規約ページを表示

### 設定

![config](img/config.png)

1. `Config`メニューをクリック
2. リージョン名とAPIキーを設定
    - `Region`: `ap-northeast-1`のようなAWSリージョンコード
    - `API Key`: `v1.public.xxxxx`
3. `Save`をクリック

### Maps機能

![maps](img/maps.gif)

1. `Maps`メニューをクリック
2. `Style`を選択（Standard / Monochrome / Hybrid / Satellite）
3. `Color Scheme`を選択（Light / Dark）
4. （任意）スタイル詳細オプションを設定
    - `Language`: 地名ラベルの言語
    - `Political View`: 国境表示の地政学的視点
    - `Terrain`: 陰影起伏の重ね合わせ
    - `Contour Density`: 等高線の密度（Low / Medium / High）
    - `Traffic`: 交通情報（All / Congestion）
    - `Travel Modes`: 交通手段（Transit / Truck）
5. `Add`をクリック
6. 背景地図がレイヤで表示

#### APIキーの取り扱い

- 地図タイルのリクエストはAWSへ直接送信されず、このプラグインのプロキシを経由します。各リクエストに設定したAPIキーが含まれます。
- MapsレイヤのソースURIにはAPIキーが含まれます。QGISプロジェクトを保存すると、プロジェクトファイルにAPIキーが平文で保存されます。

※ 2026.08現在、`Buildings3D`と`Terrain3D`は未対応

### Processingアルゴリズム

Places / Routes の各機能は、Processingツールボックスの `Amazon Location Service` プロバイダに登録されたProcessingアルゴリズムです。そのため、バッチ処理・グラフィカルモデラー・`qgis_process` からも実行できます。

1. `Places` / `Routes` メニュー（またはツールバーのボタン）から機能を選択するか、Processingツールボックスで `Amazon Location Service` を開く
2. パラメータを入力する。位置パラメータは地図上のクリックや任意のCRSの座標で指定可能（WGS 84に変換して送信）
3. `実行`をクリック
4. 結果は出力レイヤとして追加される（一時レイヤまたはファイルへの保存を選択可能）

リクエストの課金区分の見積もり、料金バケット、Notices、データ帰属表示（Attributions）はProcessingのログに出力されます。事前の確認ダイアログは表示されないため、`CalculateIsolines`（しきい値ごとに課金）や`CalculateRouteMatrix`（出発地 × 目的地の組ごとに課金）は入力件数に注意してください。

#### Places

- `SearchText`: フリーテキストで場所を検索。バイアス位置は必須。`Countries`フィルタと`Travel Mode`（Car / Scooter / Truck）に対応。詳細設定で最大5ページまで取得可能（1ページごとに1リクエスト）。
- `Geocode`: 住所を座標に変換。バイアス位置（任意）、`Countries`フィルタ、`Address Names`モード、`Postal Code Mode`に対応。
- `ReverseGeocode`: 位置を最寄りの住所に変換。位置は必須。`QueryRadius`（メートル、0 = 未指定）は任意。
- `SearchNearby`: 位置の周辺スポットを検索。位置と`QueryRadius`（メートル）が必須。詳細設定で最大5ページまで取得可能。
- `GetPlace (add place details)`: `PlaceId`フィールドを持つポイントレイヤをコピーし、`Phone` / `Website` / `OpeningHours` / `TimeZone`を追加。一意なPlaceIdごとに1リクエスト（最大25件）。対象を絞るには「選択地物のみ」を使用。

Placesのリクエストはすべて`IntendedUse=Storage`で送信されます。

※ 2026.08現在、`Suggest`と`Autocomplete`は未対応

#### Routes

- `CalculateRoutes`: 始点と終点の間のルートを計算。経由地（ポイントレイヤ、順序フィールドで並び替え）、`Travel Mode`、`Optimize For`、`Avoid`、出発・到着時刻、Transitのモード絞り込みに対応。ルート概要テーブルは任意出力。
- `CalculateIsolines`: 指定地点を基準に、時間（分）または距離（km）で到達可能な範囲を計算。方向、`Travel Mode`、最大5つのしきい値に対応。
- `SnapToRoads`: ポイント / マルチポイントレイヤのGPSトレースを道路へスナップ。`ID` / `Order` / `Timestamp` / `Heading` / `Speed`フィールドと`Snap Radius`の指定に対応。信頼度ポイントは任意出力。
- `CalculateRouteMatrix`: 2つのポイントレイヤ間の距離と所要時間を一括計算。結果はテーブルで出力され、任意でOD直線を出力可能（最大100組）。

※ 2026.08現在、`OptimizeWaypoints`は未対応

### Terms機能

1. `Terms`メニューをクリック
2. 利用規約ページがブラウザで表示

### 利用規約

[AWS Service Terms](https://aws.amazon.com/jp/service-terms)

Amazon Location Serviceにはデータ利用について利用規約があります。「82. Amazon Location Service」の項目を確認し、自己責任でご利用ください。開発者は、本サービスの利用に関して発生するいかなる損害についても一切の責任を負いません。

HEREをプロバイダとして使用する場合、基本的な利用規約に加えて、次のことを行うことはできません。  

a. ジオコード化および逆ジオコード化の結果を含む日本のロケーションデータを保管またはキャッシュすること。  
b. 別の第三者プロバイダからのマップの上にHEREのルートを重ねること、またはHEREのマップ上に他の第三者プロバイダからのルートを重ねること。  

## 開発

### 必要なツール

- [uv](https://docs.astral.sh/uv/)
- QGIS 3.34以降（QGIS 4.xを含む）

### セットアップ

```bash
# 依存関係のインストール
uv sync

# リント
uv run ruff check .

# フォーマット
uv run ruff format .
```

### ローカル開発

QGISのプラグインディレクトリにシンボリックリンクを作成します：

**macOS:**
```bash
ln -s /path/to/location_service ~/Library/Application\ Support/QGIS/QGIS3/profiles/default/python/plugins/location_service
```

**Windows:**
```powershell
mklink /D "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\location_service" "C:\path\to\location_service"
```

**Linux:**
```bash
ln -s /path/to/location_service ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/location_service
```

QGIS 4を使用する場合は、プロファイルパスの`QGIS3`を`QGIS4`に置き換えてください。

コードを編集した後、QGISでプラグインをリロードすると変更が反映されます。

## ライセンス

Python modules are released under the GNU General Public License v2.0

Copyright (c) 2024-2026 MIERUNE Inc.
