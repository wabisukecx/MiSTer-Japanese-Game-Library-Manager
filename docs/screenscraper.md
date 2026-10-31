# ScreenScraper APIへの直接連携（CLI）

ScreenScraper連携はCLIから名前検索とパッケージ画像取得を行います。
GUIのSkraper取り込みとは別経路です。通常のSkraperの準備とユーザーログインは[初回セットアップ](setup.md)を参照してください。
このページのCLIからAPIへ直接接続する機能には、本アプリ用の開発者認証とScreenScraperのユーザー認証が必要です。Skraper経由で画像を取得する利用者が、本ツール用の開発者認証を用意する必要はありません。
サービスの登録・利用条件は [ScreenScraper](https://www.screenscraper.fr/) で確認してください。

## 認証

不足する値はコマンド実行時に対話入力します。パスワードは非表示で、ファイルやDBに保存しません。
自動実行では次の環境変数と `--no-prompt` を使えます。

| 環境変数 | 内容 |
|---|---|
| SCREENSCRAPER_DEVID | 開発者ID |
| SCREENSCRAPER_DEVPASSWORD | 開発者パスワード |
| SCREENSCRAPER_SSID | 利用者名 |
| SCREENSCRAPER_SSPASSWORD | 利用者パスワード |

認証値をコマンド引数・ソースコード・共有設定へ埋め込まないでください。

## 検索と取得

先にROMを登録し、listでローカルのゲームIDを確認します。

```powershell
mister-jp-library list
mister-jp-library screenscraper-search 1
mister-jp-library screenscraper-download 1 --provider-game-id 1234
```

1はローカルのゲームID、1234は検索結果のScreenScraper側IDへ置き換えます。
検索は登録ゲームの機種と正規化名を使い、ROM本体・ROMハッシュ・ROMサイズは送信しません。
成功した検索候補は30日キャッシュします。`--refresh` で再検索できます。

画像取得の既定地域はjpです。自動的に海外版へ切り替えず、必要な場合だけ `--region us`・eu・worを指定します。
取得画像はPNG候補として保存します。`--select` で採用し、既存の選択変更には `--replace` も指定します。
JPを要求しても画像の内容・地域・向きは目視確認が必要です。このコマンドはMiSTerへ転送しません。

## キャッシュと要求制御

`--cache` の既定は `work/artwork` で、その下のscreenscraperへ検索結果・画像を保存します。
画像はURL単位のキャッシュを使い、破損を検出すると再取得します。認証付きURLはキャッシュキーやメタデータへ保存しません。

アダプターは利用枠を確認し、要求間隔・ダウンロード速度・同時実行を制御します。
一時的な通信障害だけを再試行し、認証・利用枠エラーや想定外の応答では停止します。
認証付きリクエストをリダイレクト先へ転送しません。

API実行ロックと次回接続可能時刻は、Windowsでは `%LOCALAPPDATA%/mister-jp-library/api-rate/` に保存します。
強制終了でロックが残った場合は、API処理が動いていないことを確認してから該当ロックだけを除去してください。
別アプリが同じアカウントを使う場合の要求数は、このツールだけでは管理できません。

APIアダプターの自動テストは合成応答と通信モックを使用します。実際の開発者認証を使う接続確認は別途必要です。
