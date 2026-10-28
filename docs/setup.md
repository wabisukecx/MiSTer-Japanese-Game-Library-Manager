# 初回セットアップ

このガイドでは、Console ModeとSkraperを使って、PCで整備した名前・画像をMiSTerへ反映する標準の構成を準備します。

## 準備するもの

| 場所 | 用意するもの | 準備ができた状態 |
|---|---|---|
| MiSTer | Console Mode | 起動し、ゲーム一覧を表示・選択できる |
| PCとMiSTer | 同じネットワーク、FTP接続 | MiSTerのIP・FTPユーザー名・パスワードが分かり、接続できる |
| PC | Pythonと本ツール | GUIを起動できる |
| PC | Skraper | 展開したSkraperUI.exeを起動できる |
| Skraper | ScreenScraperのユーザーアカウント | Skraper側でログイン・認証が成功する |
| PC | 利用するROM | 本ツールの対応機種ごとにフォルダーを指定できる |

Console Mode・Skraper・ROMは本ツールに同梱していません。

## 1. MiSTerにConsole Modeを導入する

1. [Console Modeの公式配布ページ](https://github.com/Retro-Remake/ConsoleMode_Distribution)を開き、導入手順を確認します。
2. 配布元の案内に従って `Install_Console_Mode.sh` を用意し、MiSTerのScriptsメニューから実行します。導入する配布版が補助ファイルを指定している場合は、その案内も確認します。
3. Console Modeを起動し、使用する機種とゲームの一覧が表示されることを確認します。

画面の使い方は、配布元が案内する[Console Mode操作ガイド](https://github.com/Takiiiiiiii/SuperStation-Documentation/wiki/Console-Mode)を参照してください。
本ツールはConsole Modeの導入や設定の自動修正を行いません。

日本語名の反映には、実機の `/media/fat/ConsoleMode/config.ini` に別名ファイルの保存先 `GLOBAL.aliasPath` が設定され、
`/media/fat/ConsoleMode/fonts/NotoSansCJK-Regular.otf` が存在する必要があります。
本ツールの「詳細機能 → 日本語タイトル設定」で確認できます。未設定・未配置なら、Console Mode側の準備を済ませてから転送します。

## 2. FTPで接続できることを確認する

PCとMiSTerを同じネットワークに接続し、MiSTerのIPアドレスとFTPのログイン情報を確認します。
本ツールはFTPを使います。GUIの転送はポート21に接続し、サーバーはMLSDによる一覧取得に対応する必要があります。

本ツールをインストールしたら、次のコマンドでログインと一覧取得を確認できます。

```powershell
.\.venv\Scripts\mister-jp-library.exe connection-test --host 192.0.2.10 --user root
```

MiSTerのFTPは標準で動作しており、[MiSTer公式ドキュメント（Advanced Networking）](https://mister-devel.github.io/MkDocs_MiSTer/advanced/network/)ではユーザー名 `root`、パスワード `1` とされています。パスワードを変更している場合は設定した値を使ってください。

IPとユーザー名は自分のMiSTerの値へ置き換えます。FTPパスワードは実行時に入力し、本ツールは保存しません。

## 3. PCにSkraperを用意する

1. [Skraper公式サイト](https://www.skraper.net/)からWindows版をダウンロードします。
2. 任意のフォルダーへ展開し、`SkraperUI.exe` を起動します。ZIP内から直接起動せず、展開先を使います。
3. 初回設定を進めます。起動に必要な.NETランタイムの案内が出た場合は、公式サイトの案内に従って用意します。

展開先は後で本ツールの「Skraperフォルダー」に指定します。SkraperはScreenScraperのデータを利用して画像を取得します。[Skraper公式案内](https://www.skraper.net/)

## 4. ScreenScraperに登録し、Skraperでログインする

1. [ScreenScraper](https://www.screenscraper.fr/)でユーザーアカウントを作成します。
2. Skraperの初回設定またはアカウント設定に、ScreenScraperのユーザー名とパスワードを入力します。
3. Skraper側で認証・接続が成功することを確認します。アカウントがある場合は、既存の情報を使います。

認証情報を入力する場所はSkraperです。本ツールへScreenScraperのパスワードを登録する操作はありません。

| 接続先・用途 | 使うアカウント | 入力する場所 |
|---|---|---|
| MiSTerへ転送・一覧取得 | MiSTerのFTPアカウント | 本ツールの接続画面 |
| Skraperで画像取得 | ScreenScraperのユーザーアカウント | Skraperのアカウント設定 |
| CLIからScreenScraper APIへ直接接続 | 開発者認証とユーザー認証 | CLIの対話入力または環境変数 |

通常のGUI手順では上の2つを使います。CLIのAPI直接連携は任意の別機能です。[API直接連携](screenscraper.md)
日本語名の自動照合はRetronian GameDBを利用し、Skraperのログインとは別に動作します。

## 5. ROMと画像取得先を設定する

1. PCのROMフォルダーを機種ごとに用意します。[対応機種とROM形式](systems.md)を確認してください。
2. Skraperで対象機種を追加し、Games/Roms folderにそのROMフォルダーを指定します。
3. 画像取得対象をパッケージ正面（BOX 2D）にし、PNG形式の出力先を確認します。日本版を使う場合は地域を日本に設定します。
4. 少数のROMで取得を試し、ROMと同じ基本名のPNGが出力されることを確認します。

ゲームボーイとゲームボーイカラーは、本ツールには同じPCフォルダーから登録できますが、Skraperの設定・画像取り込み・転送はそれぞれの機種で行います。
本ツールは画像のファイル名でROMと対応付けます。同名の画像が複数ある場合は、取り込む出力先を整理します。

## 6. 本ツールで作業を始める

[READMEのインストール手順](../README.md#本ツールのインストール)で本ツールを導入します。
GUIでは「PCのROMを登録 → Skraperで画像取得・取り込み → 名前・画像の承諾 → MiSTerへまとめて転送」の順に進めます。[GUIの操作](gui.md)

既に取得済みのPNGを取り込む操作ではSkraperの再取得は不要です。名前だけを整備する場合やCLIの別の画像取得手段を使う場合は、利用する機能に合わせて準備してください。
