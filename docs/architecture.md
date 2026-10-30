# アーキテクチャ

## モジュール構成

ソースはsrc直下に置き、画像・DB・通信などの責務ごとにサブディレクトリへ分けています。
異なる領域のモジュールは絶対インポート、同じサブパッケージ内は相対インポートを使用します。

```text
src/
├── main.py               CLIとGUI起動
├── __main__.py           python src の入口
├── workflow.py           登録・照合・転送計画の共通処理
├── local_rom.py          ROMとZIPの読み取り
├── systems.py            機種定義と転送先検査
├── gui/                  PyQt6の画面とワーカー
├── database/             SQLiteストア
├── metadata/             名前照合と一括承諾
├── artwork/              画像取得・取り込み・回転補正
├── mister/               FTP一覧・一括転送・復旧
└── consolemode/          別名の統合と設定確認
```

## データの流れ

```text
PCのROM ─── 登録・ZIP検査 ─┐
実機の一覧 ─ FTPで列挙 ───┴─ SQLite
Retronian ─ キャッシュ・照合 ─ 名前候補 ─ 確定
Skraper ─ PNG取り込み・コピー ─ 画像候補 ─ 補正・採用
                                           │
                           現在のDBから転送計画を作成
                                           │
                       退避 → 不足ROM → 画像 → 別名
                       一時転送・読み戻し照合・公開
```

GUIの時間がかかる操作はQThreadで実行し、SQLite接続はワーカー内で作成・終了します。
workflow.register_folderはGUIとCLIで共有します。workflow.batch_planは画面の古い状態ではなく、現在のDBから各計画を作成します。

## DBと状態

ゲームの一意キーは `(device, system, rom_path)` です。deviceは論理ライブラリ名であり、接続先IPとは独立しています。

| テーブル | 責務 |
|---|---|
| systems / games | 機種とROM単位の登録・確定名・選択画像 |
| local_roms | 外部ROMの絶対パス、ZIP内部メンバー名 |
| title_aliases / match_candidates | 出典付き別名、名前候補と照合根拠 |
| artworks / artwork_edits | 画像候補・ハッシュ、補正元・角度・元ハッシュ |
| settings | GUIの表示範囲、機種別作業先、Skraperの場所 |
| sync_manifests | CLIのレビュー用JSON履歴 |

名前はunmatched・review・confirmedの状態を持ちます。候補更新は確定名を変更しません。
画像は選択パスを保持し、同じ候補の内容が更新されたら選択を解除します。補正画像は祖先のハッシュも検査します。
一括承諾はプレビュー後の状態を再検査し、ゲームごとのトランザクションで適用します。

games.sync_statusは実際の転送結果を追跡しません。転送の状態はtransfer.jsonが担い、レビュー用JSONとは分けています。

## 名前照合

Retronianは機種別にインデックスを作り、次の順で照合します。

1. ROM内容のSHA-256（ZIPでは内部ROM）
2. 地域・Revを含むROM基本名
3. NFKC正規化・タグ除去後の名前

上位で一致したら下位へ進みません。1と2はscore 1、3は0.8なので、score 1が必ず内容一致を意味するわけではありません。
候補の出典・理由を保存し、確認してから確定します。ヘッダー除去などのROM内容の変換は行いません。

## 通信と公開

FTPReaderは読み取り専用の一覧取得を担当します。一括転送はartwork_transfer、別名統合はconsolemode.titlesに分離しています。
alias_guardは実機games配下の衝突を検査し、recoveryは転送の逆操作と再実行を担当します。

一括転送は既存画像・別名をPCへ退避した後、ファイルごとに一時転送・検証・rename・検証を行います。
実機にも元データを保持し、失敗時は逆順に復旧します。外部変更を検出した場合は保護して未完了を記録します。
FTP上の一括トランザクションではありません。[転送と復旧](recovery.md)を参照してください。

## 画像とキャッシュ

PNGは16 MiB上限、構造・CRC、SHA-256を検査します。回転時はQtでデコードして別PNGを生成します。
管理コピーと回転コピーはDBから直接参照し、Skraperの元出力に依存しない構成です。
Libretroの取得先は許可HTTPSホストに制限し、ScreenScraperでは認証・検索キャッシュ・要求間隔を独立して扱います。
