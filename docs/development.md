# 開発ガイド

## 開発環境

Python 3.10以上を使用します。GUIの開発・テストにはPyQt6が必要です。テストはunittestを使用します。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
```

主要モジュールはsrc直下に置きます。コードの役割は[アーキテクチャ](architecture.md)を参照してください。
コンソールコマンドはpyproject.tomlで `main:main` と `gui.app:main` に登録しています。

## ソースからの起動

インストールせずにCLIやGUIを起動する場合は、次のコマンドを使えます。

```powershell
python src --help
python src/main.py gui
```

モジュール形式で起動する場合はsrcをインポートパスへ指定します。

```powershell
$env:PYTHONPATH = 'src'
python -m main --help
python -m gui
```

インストール済み環境で構成・エントリーポイントを変えた場合は、`pip install -e ".[gui]"` を再実行します。
srcやtestsが実データに依存しないよう、ROM・取得画像・API認証はリポジトリへ入れません。

## 自動テスト

```powershell
.\.venv\Scripts\python.exe scripts/verify.py
```

srcをインポートパスに追加して全テストを実行し、UTF-8ログを `work/verification.txt` へ出力します。
画面なしの環境では `QT_QPA_PLATFORM=offscreen` を指定します。

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

GUI依存がない環境ではGUI関連テストをスキップします。GUIの変更を検証するときはPyQt6を導入してください。
テストは一時DB・合成画像・通信モックを使い、実機や外部APIの認証を必要としません。

| 対象 | 主な確認点 |
|---|---|
| 登録・照合 | 機種分離、再登録、ZIP、候補と確定名の保持 |
| 画像 | PNG検査、コピー、内容更新時の再確認、補正元の更新 |
| 転送 | 事前検査、退避、読み戻し、既存ROM照合、別名衝突 |
| 復旧 | 中断、通信断、rename応答の消失、外部変更、再実行 |
| GUI / CLI | 共通登録、対象選択、ダイアログ、ワーカーの終了 |

## パッケージ

```powershell
.\.venv\Scripts\python.exe -m pip wheel . --no-deps --wheel-dir dist
```

wheelにはsrcのトップレベルモジュールと各サブパッケージを含めます。work、tests、実行ログは含めません。
配布物を変更した場合は、生成したwheelを別環境へインストールし、CLIとGUIの起動を確認してください。

## 実機確認

自動テストの成功だけではConsole Mode上の表示や通信環境を保証しません。
変更内容に応じ、所有する実機で次を確認します。

1. 接続・ROM一覧の取得と機種ルートの一致
2. 少数のゲームで名前・画像・向き・ゲーム起動
3. 転送記録と、元データのPC・実機バックアップ
4. 中断・再接続・記録からの復旧
5. Console Modeに入り直した後の表示と必要な画像最適化

実機の操作は転送画面で対象を確認して実行します。実機試験の記録にパスワードや認証付きURLを含めないでください。

## リポジトリに含めるファイル

公開対象はソース、テスト用の合成fixtures、スクリプト、プロジェクト設定、利用・開発ドキュメントです。
ローカルデータと開発ログはwork、外部ROMはリポジトリ外か除外対象のromsへ保存します。
Gitで既に追跡済みのファイルはgitignoreで除外されないため、コミット対象の差分も確認してください。
