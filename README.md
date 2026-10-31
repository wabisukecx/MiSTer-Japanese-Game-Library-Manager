# MiSTer Japanese Game Library Manager

MiSTer Console Modeの日本語ゲーム名とパッケージ画像をPCで整備し、まとめて反映するツールです。PyQt6のGUIと、登録・照合・確認用のCLIを備えています。

標準の使い方では、**MiSTerにConsole Modeを導入し、PCにSkraperを用意して、SkraperからScreenScraperのユーザーアカウントでログインしておく**ことを前提にします。本ツールをインストールする前に、下の「使い始める前の準備」を進めてください。

## 主な機能

- PCのROMフォルダーを登録し、日本語名の候補を取得・確認・確定
- Skraperで取得したPNGを取り込み、画像の選択と90度単位の向き補正
- 不足するROM・選択画像・確定済みの日本語名をFTPで一括転送
- 転送前バックアップ、読み戻し照合、失敗・中断時の復旧
- 実機のROM一覧取得と、名前の更新候補の比較
- 出典・確定名・判断履歴を含む日本語名DBのJSON書き出しとマージ（[使い方](docs/title-db.md)）

ファミコン、スーパーファミコン、メガドライブ、ゲームボーイ、ゲームボーイカラー、ゲームボーイアドバンス、PCエンジン、PCエンジン CD-ROM²（CHD）、PlayStation（CHD）、セガサターン（CHD）、NINTENDO 64、ネオジオ（.neo）、ワンダースワン、ワンダースワンカラーに対応します。[機種ごとの形式・保存先](docs/systems.md)を確認してください。

ディスクシステム、SG-1000、セガ・マークIII／マスターシステム、ゲームギア、スーパー32X、メガCD（CHD）、ネオジオポケット／カラー、バーチャルボーイにも対応します。国内発売済みで日本語名データを確認できた機種を対象にしており、全ソフトの日本語名が揃っているわけではありません。

## 使い始める前の準備

1. **MiSTerにConsole Modeを導入する。** [Console Modeの公式配布ページ](https://github.com/Retro-Remake/ConsoleMode_Distribution)の案内に従ってインストールし、Console Modeが起動してゲームを選べることを確認します。
2. **PCとMiSTerを同じネットワークへ接続する。** MiSTerのIPアドレス（自分の実機の値。手順は[初回セットアップ](docs/setup.md#2-ftpで接続できることを確認する)）とFTPのユーザー名・パスワードを確認し、PCからFTPで接続できる状態にします。
3. **PCにSkraperを用意する。** [Skraper公式サイト](https://www.skraper.net/)からWindows版をダウンロード・展開し、`SkraperUI.exe` を起動します。
4. **ScreenScraperに登録し、Skraperでログインする。** [ScreenScraper](https://www.screenscraper.fr/)のユーザーアカウントを作成し、そのユーザー名とパスワードをSkraperに設定します。これはMiSTerのFTPアカウントとは別です。
5. **PCのROMと画像取得設定を用意する。** 機種別のROMフォルダーを指定し、Skraperでパッケージ画像をPNGとして取得できることを確認します。

初期設定の詳しい手順、接続確認、日本語表示の条件は[初回セットアップ](docs/setup.md)を参照してください。通常のSkraper経由の手順では、本ツール用のScreenScraper開発者認証を利用者が用意する必要はありません。

## 必要環境

| 項目 | 要件 |
|---|---|
| PC | Windowsを主な対象として開発。Python 3.10以上 |
| GUI | PyQt6（GUI用インストールで導入） |
| MiSTer | Console Modeを導入・起動済み。PCからMLSD対応のFTPで接続できること |
| 日本語表示 | Console Modeの別名設定と `NotoSansCJK-Regular.otf` |
| 画像取得 | Skraperをダウンロード・展開し、ScreenScraperのユーザーアカウントを設定済み |

CLIの実行時依存はPython標準ライブラリのみです。日本語名の初回照合や画像取得にはインターネット接続を使います。ROM本体を取得する機能はありません。

## 本ツールのインストール

リポジトリを取得し、プロジェクトのルートで実行します。以下はWindows PowerShellの例です。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
.\.venv\Scripts\mister-jp-library.exe gui
```

CLIのみの場合は `pip install -e .` とします。既存の環境でソース構成を更新した場合も、同じインストールコマンドを再実行してください。

```powershell
.\.venv\Scripts\mister-jp-library.exe --help
```

## 基本的な使い方

1. **PCのROMを登録**で機種とフォルダーを指定します。日本語名の候補も照合できます。
2. **Skraperで画像取得・取り込み**から外部のSkraperを起動し、取得したPNGを取り込みます。
3. ゲームをチェックし、名前・画像・向きを確認します。必要なら名前を手入力したり、画像を回転補正したりします。
4. **3 · MiSTerへ転送**で画像と表示名・接続先を確認して送ります。通常は転送種別を選ぶ必要はなく、画像や日本語名がないゲームもそのまま進めます。オプションは「詳細設定」にまとめています。

Skraperでのログイン・取得設定・取得開始はSkraper側で行います。取り込んだ画像はPC内の管理ライブラリにコピーされます。
画像の内容が更新された場合は選択を解除し、再確認が必要になります。

[GUI操作ガイド](docs/gui.md) · [CLIリファレンス](docs/cli.md)

## 転送の動作

転送は1実機・1機種・同一のROMルートごとに行います。既存ROMは上書きせず、不足分だけを追加します。任意で既存ROMとPCの内容を照合できます。

画像と日本語名は更新前にバックアップし、一時転送・読み戻し照合後に公開します。日本語名は実機の `GLOBAL.aliasPath` へ統合します。別名キーは全機種共通のため、実機のgames配下も衝突検査します。

失敗・中断時は反映済みファイルの復旧を試みます。通信断や外部変更で戻せない場合は、画面上部に表示される **転送を復旧** から再実行できます。[転送と復旧](docs/recovery.md)

## データと対応範囲

登録情報・画像・キャッシュ・バックアップは既定で `work/` に保存します。ROM・取得画像・取得した日本語名DB・認証情報・Skraper本体はリポジトリに含めません。
日本語名の候補には [Retronian GameDB](https://github.com/retronian/retronian-gamedb) を使用し、PCエンジン CD-ROM²・PlayStation・セガサターンでは [Redump Local Names](https://github.com/yingw/redump-local-names) の対応表も併用します。候補の出典と取得データの利用条件はローカルに保存します。[データ管理と取得元](docs/data.md)
メガCDではRedump Local Namesを使用します。[GameDataBase by PigSaint](https://github.com/PigSaint/GameDataBase)（CC BY 4.0）はディスクシステム等の追加機種に加え、既存のFC・SFC・GB系・N64・メガドライブ・メガCD・サターン・PCエンジン系でも利用します。機種ごとに対応データのあるDBをすべて照合し、全角・半角と空白だけの違いは出典を残してまとめます。表記が競合した場合は個別に確認し、確定済みの名前は保持します。

対象ROMが1本入ったZIPに対応し、ZIPのまま転送します。実機の子フォルダーへの転送、7z・RAR、ディスクシステムのFDSと対応CD機種のCHD以外のディスクイメージ、複数ROM入りのMAME・FBNeo ZIP、背景生成、Console Modeの画像キャッシュ更新は対象外です。汎用 `export` コマンドも停止状態です。

ファミコン・スーパーファミコン・メガドライブ・ゲームボーイ・ゲームボーイカラー・ゲームボーイアドバンス・PCエンジン・ネオジオは一部タイトルで実機確認済みです。

## 開発

```powershell
.\.venv\Scripts\python.exe scripts/verify.py
```

結果は `work/verification.txt` に出力します。[開発ガイド](docs/development.md)と[アーキテクチャ](docs/architecture.md)を参照してください。

[ドキュメント一覧](docs/README.md)

## ライセンス

本ツールのソースコードは [MIT License](LICENSE) で公開します。Retronian GameDBのデータ（CC BY-SA 4.0）、GameDataBase by PigSaint（CC BY 4.0）、Redump由来のデータ、Skraper、ScreenScraper、画像、ROMは本リポジトリに含まれず、それぞれの提供元の条件に従います。
