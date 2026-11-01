# 対応機種とROM形式

本文とGUIでは日本語の機種名を使います。CLIの `--system` とDBでは下表の機種IDを使います。たとえばゲームボーイカラーは、CLIでは `--system GBC` と指定します。PCの既定ROMフォルダーは、MiSTerの標準ROMルート末尾と同じ名前です（例: `MD` → `work/Roms/MegaDrive/`、`PS1` → `work/Roms/PSX/`）。複数ルートのある機種は、表の先頭のルート名を使います。

| 機種名 | 機種ID | MiSTerのROMルート | 通常ROMの拡張子 |
|---|---|---|---|
| ファミコン | `NES` | `/media/fat/games/NES` | `.nes` |
| スーパーファミコン | `SNES` | `/media/fat/games/SNES` | `.sfc`, `.smc`, `.bin` |
| メガドライブ | `MD` | `/media/fat/games/MegaDrive` または `/media/fat/games/Genesis` | `.bin`, `.gen`, `.md` |
| ゲームボーイ | `GB` | `/media/fat/games/GAMEBOY` | `.gb` |
| ゲームボーイカラー | `GBC` | `/media/fat/games/GBC` | `.gbc` |
| ゲームボーイアドバンス | `GBA` | `/media/fat/games/GBA` | `.gba` |
| PCエンジン | `PCE` | `/media/fat/games/TGFX16` | `.pce`, `.sgx` |
| PCエンジン CD-ROM² | `PCECD` | `/media/fat/games/TGFX16-CD` | `.chd` |
| PlayStation | `PS1` | `/media/fat/games/PSX` | `.chd` |
| セガサターン | `SATURN` | `/media/fat/games/Saturn` | `.chd` |
| NINTENDO 64 | `N64` | `/media/fat/games/N64` | `.n64`, `.z64`, `.v64` |
| ネオジオ | `NEOGEO` | `/media/fat/games/NEOGEO` | `.neo` |
| ワンダースワン | `WS` | `/media/fat/games/WonderSwan` | `.ws` |
| ワンダースワンカラー | `WSC` | `/media/fat/games/WonderSwanColor` または `/media/fat/games/WONDERSWAN_COLOR` | `.wsc` |
| ファミコン ディスクシステム | `FDS` | `/media/fat/games/FDS` | `.fds` |
| SG-1000 | `SG1000` | `/media/fat/games/SG1000` | `.sg` |
| セガ・マークIII／マスターシステム | `SMS` | `/media/fat/games/SMS` | `.sms` |
| ゲームギア | `GG` | `/media/fat/games/GameGear` | `.gg` |
| スーパー32X | `S32X` | `/media/fat/games/S32X` | `.32x` |
| メガCD | `MCD` | `/media/fat/games/MegaCD` | `.chd` |
| ネオジオポケット | `NGP` | `/media/fat/games/NGPC` | `.ngp` |
| ネオジオポケットカラー | `NGPC` | `/media/fat/games/NGPC` | `.ngc`, `.npc` |
| バーチャルボーイ | `VB` | `/media/fat/games/VirtualBoy` | `.vb` |

対象ROMが1本入ったZIPも扱えます。PCエンジンは「モトローダー」で転送・実機動作まで確認済みです。対応機種でも、すべてのROM・Console Mode構成を確認したわけではありません。
定義は [systems.py](../src/systems.py) にまとめています。

ネオジオは `.neo` のSkraperでの画像取得、MiSTerへの転送、実機での起動・プレイまで確認済みです。SkraperではNeo Geoの対象拡張子に `.neo` を追加してください。
実機検証済みはファミコン・スーパーファミコン・メガドライブ・ゲームボーイ・ゲームボーイカラー・ゲームボーイアドバンス・PCエンジン・ネオジオです。対応機種はGUIの機種フィルター・PC登録ダイアログ・CLIから選べます。
PCエンジン CD-ROM²・PS1・セガサターン・メガCDは単体CHDのみ対応し、CUE/BINの関連ファイル転送やM3Uによる複数ディスク管理は行いません。ネオジオは単体 `.neo` のみ対応し、複数チップのROMを含むMAME・FBNeo ZIPは扱いません。
PCエンジン CD-ROM²はHuCARD版とは別の機種として登録します。SkraperではPC Engine CD-Rom（機種ID 114）を選び、`.chd` が対象拡張子に含まれていることを確認してください。画像・別名の反映先もCD用のROMルートになります。
PCエンジン CD-ROM²・PS1・セガサターンの日本語名はRetronianとRedump Local Namesを併用します。CHDに変換する前のRedump DAT名がファイル名に残っていると照合しやすくなります。表記の違う候補は出典を確認して選んでください。
メガCDの日本語名にはRedump Local Namesを、ディスクシステム・SG-1000・マークIII・ゲームギア・32X・ネオジオポケット／カラー・バーチャルボーイにはPigSaintのGameDataBaseを使います。日本語名が未収録のソフトもあり、全タイトルの変換を保証するものではありません。
GameDataBaseはFC・SFC・GB系・N64・メガドライブ・メガCD・サターン・PCエンジン系の補完にも利用し、機種ごとに利用可能なDBをすべて照合します。[候補の統合・採用ルール](data.md#日本語名データ)も参照してください。
CHD・.neo・N64のバイト順違いなど、DBのROMと内容が異なる形式はハッシュが一致しないため、ROM名・タイトル名の照合候補を確認してください。
転送先と形式は [Console Mode配布設定](https://github.com/Retro-Remake/ConsoleMode_Distribution/tree/main/section_groups) に合わせています。

## フォルダーの扱い

PC側の登録はサブフォルダーを探索し、MiSTerへの転送は機種ルート直下へ集約します。
ネオジオポケット／カラーの既定PCフォルダーは共通の `work/Roms/NGPC/` です。`FDS` と `SG1000` は従来のフォルダー名を維持します。保存済みのPCフォルダー指定は、既定名の変更後も保持します。
FDSは[NESコア](https://github.com/MiSTer-devel/NES_MiSTer)、SG-1000は[SMSコア](https://github.com/MiSTer-devel/SMS_MiSTer)などで対応しています。実機に専用フォルダーがなくても未対応という意味ではありません。コアの標準案内ではそれぞれ `NES`・`SMS` にROMを置きますが、このツールは[Console Modeの配布設定](https://github.com/Retro-Remake/ConsoleMode_Distribution/blob/main/section_groups/Console.ini)に合わせ、`FDS`・`SG1000` をPCの既定フォルダー名と実機への転送先に使います。FDSの起動には別途BIOSの設定が必要です。
同じファイル名が複数あれば最初の1本を登録します。既存ROMは同名でスキップします。
子フォルダーを含む実機一覧はCLIのscanで取得できますが、その子フォルダーへの転送は対応しません。

メガドライブの2つのルートは、使用中の実機の構成に合わせて選びます。同じ転送で両ルートを混在させることはできません。
ゲームボーイとゲームボーイカラーは混在したPCフォルダーから登録できますが、画像取り込みと転送は機種ごとに行います。
ワンダースワンとワンダースワンカラーも、拡張子により混在フォルダーから振り分けて登録します。
ネオジオポケットとカラーは、それぞれの機種でPCのROMフォルダーを登録します。PCフォルダー・Skraperの画像取得設定は機種ごとに保持し、一方の登録でもう一方の設定を変更しません。MiSTerへの転送先は両方とも `NGPC` に固定します。

## ZIP

ZIPは展開せず、そのまま同じファイル名で転送します。内部ROMは日本語名照合用のハッシュ計算に使います。
画像名と別名キーはZIPの基本名に対応します。例として `Example.zip` の画像は `media/Example.png`、別名は `Example=日本語名` となります。

選択機種の対象ROMはZIP内に1本だけ必要です。内部ROMの上限は通常64 MiB、PCエンジン CD-ROM²・PS1・セガサターン・メガCDは1 GiB、ネオジオは128 MiBです。暗号化ZIP、上限を超える内部ROM、危険なエントリ名やリンクは拒否します。
7z・RAR・複数の対象ROMを含むZIPは扱いません。

## 表示データ

画像は `<ROMルート>/media/<ROM基本名>.png` へ保存します。背景やoptimizedの内容は変更しません。
日本語名はConsole Modeの `GLOBAL.aliasPath` で指定されたtxtファイルへ統合します。
別名キーには機種が含まれないため、異なる機種に同じ基本名があれば確認が必要です。

