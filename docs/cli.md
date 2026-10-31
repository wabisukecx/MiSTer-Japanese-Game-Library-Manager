# CLIリファレンス

以下ではインストール済みの `mister-jp-library` を使います。仮想環境を有効化していない場合は `.\.venv\Scripts\mister-jp-library.exe` と読み替えてください。

```powershell
mister-jp-library --help
mister-jp-library <command> --help
```

DBの既定値は `work/library.sqlite3` です。`--db` はサブコマンドより前に指定します。
相対パスは実行時の作業ディレクトリを基準に解決します。
`--system` は機種IDで指定します。例ではファミコンのID `NES` を使います。日本語の機種名との対応は[対応機種](systems.md)を参照してください。
ライブラリ分割と `--device` オプションは廃止しました。旧形式のinventory JSONに含まれる `device` は無視します。既存DBは[旧バージョンからの移行](gui.md#旧バージョンからの移行)に従って自動統合します。

## ROMの登録と一覧

```powershell
mister-jp-library register-roms 'D:\ROMs\NES' --system NES
mister-jp-library list
```

register-romsはサブフォルダーも探索します。`--root` で機種の対応ルートを選べます。
CLIでは登録のみが既定です。対応する公開DBによる日本語名照合も行う場合は `--lookup` を付けます。

実機からファイル名だけを登録する場合はscanを使います。

```powershell
mister-jp-library connection-test --host 192.0.2.10 --user root
mister-jp-library scan --host 192.0.2.10 --user root --system NES --out work/nes-inventory.json
```

`192.0.2.10` は例示用です。所有するMiSTerのIPに置き換えてください。パスワードは非表示の対話入力です。
scanとconnection-testは `--port`（既定21）、`--timeout`（既定15秒）、`--root` を指定できます。
scanに `--recursive` を付けるとmediaを除く子フォルダーも探索します。子フォルダーへの転送は対象外です。
scanではROMの内容をダウンロードしません。消えたROMのDB登録は自動削除しません。

ローカルの一覧JSONは `import-inventory <path>` で取り込めます。形式は [サンプル](../tests/fixtures/inventory.json) を参照してください。

## 名前候補と確定

```powershell
mister-jp-library import-aliases my-aliases.json
mister-jp-library match
mister-jp-library list --pending
mister-jp-library confirm 1 '確認した日本語名'
mister-jp-library unconfirm 1
```

`1` はlistに表示されたゲームIDへ置き換えます。別名JSONは [aliases.json](../tests/fixtures/aliases.json) と同じ配列形式です。
systemには対応機種ID、alias・japanese_title・sourceには空でない文字列を指定します。

matchはインポートした別名を使い、未確定ゲームの候補を置き換えます。Retronianの取得処理ではありません。
既存のRetronian候補も置換対象になるため、併用する場合は候補の出所を確認してください。
確定名は再照合で上書きしません。`delete-aliases <source>` は出典単位でインポート済み別名を削除します。

## 日本語名DBの書き出しとマージ

日本語名の候補・確定名を再利用用JSONとして保存する場合は、次のコマンドを使います。[形式とマージ規則](title-db.md)も参照してください。

```powershell
mister-jp-library export-title-db --out work/title-db/titles.json
mister-jp-library export-title-db --system SATURN --confirmed-only --hash-roms --out work/title-db/saturn.json
mister-jp-library merge-title-db work/title-db/titles.json work/title-db/saturn.json --out work/title-db/merged.json
```

`export-title-db` は `--system`・`--confirmed-only` で絞れます。`--hash-roms` を付けるとローカルROMを読み、SHA-256を追加します。ZIPでは内部ROMのハッシュです。マージはネット接続やライブラリDBを必要とせず、既存ファイルは上書きしません。

## 画像取得


```powershell
mister-jp-library artwork-url 'Exact Catalog Title (Japan)' --system NES
mister-jp-library artwork-download 1 'Exact Catalog Title (Japan)'
```

artwork-urlはLibretroのURL候補を表示するだけです。artwork-downloadは取得・選択し、既に選択済みなら `--replace` が必要です。
`--cache` の既定は `work/artwork`。出力されたlocal_previewで画像内容・地域・向きを確認します。

ScreenScraperの検索と画像取得も利用できます。[認証と使い方](screenscraper.md)を参照してください。

```powershell
mister-jp-library screenscraper-search 1
mister-jp-library screenscraper-download 1 --provider-game-id 1234
```

`1234` は検索結果のプロバイダーIDに置き換えます。既定では画像候補の登録だけで、採用する場合は `--select` を付けます。
既存選択の変更には `--replace` も必要です。これらの画像取得コマンドはMiSTerへ転送しません。

## 確認用出力と復旧

```powershell
mister-jp-library preview --out work/review.json
mister-jp-library recover-transfer work/transfer-backups/<実行ID>/transfer.json --host 192.0.2.10 --user root
```

previewはレビュー用JSONです。転送計画ではなく、MiSTerには書き込みません。既存の出力ファイルは上書きしません。
exportは汎用出力が未対応である理由を表示し、終了コード2で停止します。

recover-transferは実機のファイルを転送前へ戻す操作です。v2形式の一括転送記録を使います。[復旧手順](recovery.md)
通常のROM・画像・名前の一括転送はGUIで行います。

## コマンド一覧

| コマンド | 用途 |
|---|---|
| gui | GUIを起動 |
| register-roms | PCのROMフォルダーを登録。`--lookup` で日本語名も照合 |
| connection-test | FTPログインと一覧取得を確認 |
| scan | 実機ROM一覧を取得・登録 |
| import-inventory | 一覧JSONを取り込み |
| import-aliases | 出典付きの別名JSONを取り込み |
| match / list | 別名照合、登録ゲームと候補の表示 |
| confirm / unconfirm | 日本語名の確定・解除 |
| delete-aliases | 出典別に別名を削除 |
| artwork-url / artwork-download | LibretroのURL確認・画像取得 |
| screenscraper-search / screenscraper-download | API検索・画像取得 |
| preview / export | 確認用JSON、未対応の汎用出力の案内 |
| recover-transfer | 保存した一括転送記録から復旧 |

通常成功は0、入力・通信・未対応処理のエラーは2です。通常のCLI操作のCtrl+Cは130で終了します。

## 実機なしで試す

運用DBと分けたデモDBを使います。fixturesは架空のROM一覧と名前だけで、ROM本体や画像は含みません。

```powershell
mister-jp-library --db work/demo.sqlite3 import-inventory tests/fixtures/inventory.json
mister-jp-library --db work/demo.sqlite3 import-aliases tests/fixtures/aliases.json
mister-jp-library --db work/demo.sqlite3 match
mister-jp-library --db work/demo.sqlite3 list --pending
mister-jp-library --db work/demo.sqlite3 preview --out work/demo-review.json
```

再実行時にpreviewの出力が存在する場合は、別の出力名を指定します。
