# 対応機種とROM形式

本文とGUIでは日本語の機種名を使います。CLIの `--system`、DB、フォルダー名では下表の機種IDを使います。たとえばゲームボーイカラーは、CLIでは `--system GBC` と指定します。

| 機種名 | 機種ID | MiSTerのROMルート | 通常ROMの拡張子 |
|---|---|---|---|
| ファミコン | `NES` | `/media/fat/games/NES` | `.nes` |
| スーパーファミコン | `SNES` | `/media/fat/games/SNES` | `.sfc`, `.smc`, `.bin` |
| メガドライブ | `MD` | `/media/fat/games/MegaDrive` または `/media/fat/games/Genesis` | `.bin`, `.gen`, `.md` |
| ゲームボーイ | `GB` | `/media/fat/games/GAMEBOY` | `.gb` |
| ゲームボーイカラー | `GBC` | `/media/fat/games/GBC` | `.gbc` |
| ゲームボーイアドバンス | `GBA` | `/media/fat/games/GBA` | `.gba` |
| PCエンジン | `PCE` | `/media/fat/games/TGFX16` | `.pce`, `.sgx` |

全機種で対象ROMが1本入ったZIPも扱えます。PCエンジンは実機未検証です。対応機種でも、すべてのROM・Console Mode構成を確認したわけではありません。
定義は [systems.py](../src/systems.py) にまとめています。

## フォルダーの扱い

PC側の登録はサブフォルダーを探索し、MiSTerへの転送は機種ルート直下へ集約します。
同じファイル名が複数あれば最初の1本を登録します。既存ROMは同名でスキップします。
子フォルダーを含む実機一覧はCLIのscanで取得できますが、その子フォルダーへの転送は対応しません。

メガドライブの2つのルートは、使用中の実機の構成に合わせて選びます。同じ転送で両ルートを混在させることはできません。
ゲームボーイとゲームボーイカラーは混在したPCフォルダーから登録できますが、画像取り込みと転送は機種ごとに行います。

## ZIP

ZIPは展開せず、そのまま同じファイル名で転送します。内部ROMは日本語名照合用のハッシュ計算に使います。
画像名と別名キーはZIPの基本名に対応します。例として `Example.zip` の画像は `media/Example.png`、別名は `Example=日本語名` となります。

選択機種の対象ROMはZIP内に1本だけ必要です。暗号化ZIP、64 MiBを超える内部ROM、危険なエントリ名やリンクは拒否します。
7z・RAR・複数の対象ROMを含むZIPは扱いません。

## 表示データ

画像は `<ROMルート>/media/<ROM基本名>.png` へ保存します。背景やoptimizedの内容は変更しません。
日本語名はConsole Modeの `GLOBAL.aliasPath` で指定されたtxtファイルへ統合します。
別名キーには機種が含まれないため、異なる機種に同じ基本名があれば確認が必要です。

## 対象外

- 独自のROMルートや子フォルダーへの転送
- PCエンジンのCD-ROM版などのディスクイメージ
- ROMの入手、名前の自動変更、消えたROMの自動削除
- 背景画像生成、汎用リサイズ、Console Modeのキャッシュ更新
- SFTP接続、汎用Console Modeエクスポート
