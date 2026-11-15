# tomlcut

[![CI](https://github.com/vbe-robotron/tomlcut/actions/workflows/ci.yml/badge.svg)](https://github.com/vbe-robotron/tomlcut/actions/workflows/ci.yml)

TOML ファイルに「元動画・切り出す秒数・出力ファイル名」を書いて、FFmpeg でまとめて切り出す CLI ツールです。
出力先ディレクトリにレンダリング履歴を保存し、**同じレンダリングを繰り返さない重複スキップモード**を標準で搭載しています。

- 複数のクリップを 1 つの TOML で管理
- 開始位置は秒数でも `MM:SS` / `HH:MM:SS` でも指定可能
- `duration`（長さ）または `end`（終了位置）で範囲指定
- 同じ入力・範囲・出力名・設定のレンダリング結果があれば自動でスキップ
- 出力は一時ファイルに書き、成功時だけ原子的に置き換え
- Python 3.11+ / FFmpeg があれば動作（Python 側の追加依存なし）
- `--dry-run` / `--no-dedupe` / `--fail-fast` / quiet / verbose

## 必要環境

- Python 3.11 以上
- FFmpeg（`ffmpeg` コマンド）

```sh
ffmpeg -version
```

## インストール

```sh
git clone https://github.com/vbe-robotron/tomlcut.git
cd tomlcut

# uv を使う場合
uv tool install .

# pipx を使う場合
pipx install .
```

リポジトリ内で直接実行する場合はインストール不要です。

```sh
python -m tomlcut --help
```

## クイックスタート

```sh
tomlcut init clips.toml     # サンプル設定を生成
# clips.toml と元動画を編集
tomlcut validate clips.toml # 設定チェック
tomlcut render --dry-run clips.toml
tomlcut render clips.toml
```

## TOML の書き方

```toml
# 出力先。この TOML ファイルからの相対パス、または絶対パス。
output_dir = "./clips"

# 重複スキップモード（デフォルト true）
dedupe = true

[render]
# "encode": 再エンコードして正確に切り出す（デフォルト）
# "copy":   ストリームコピー。速いがキーフレーム単位でしか切れない
mode = "encode"
ffmpeg = "ffmpeg"
preset = "medium"
crf = 18
audio_bitrate = "192k"
# extra_args = ["-movflags", "+faststart"]

[[clips]]
source = "./videos/source.mp4"
start = "00:01:23.500"
duration = 12.5
name = "intro.mp4"

[[clips]]
source = "./videos/source.mp4"
start = 95
end = 110
name = "sub/scene-02.mp4"
```

### フィールド

| キー | 必須 | 説明 |
| --- | --- | --- |
| `output_dir` | はい（`-o` で上書き可） | 出力先ディレクトリ。TOML からの相対パスまたは絶対パス。存在しなければ作成されます。`-o` で指定した場合はカレントディレクトリからの相対パスになります。 |
| `dedupe` | いいえ（既定 `true`） | 重複スキップモード。 |
| `[render]` | いいえ | FFmpeg の共通設定。 |
| `[[clips]]` | 1 つ以上 | クリップ定義。`[[clip]]` という単数形でも書けます。 |
| `source` | はい | 元動画。TOML からの相対パスまたは絶対パス。 |
| `start` | いいえ（既定 `0`） | 開始位置。秒数または時刻文字列。 |
| `duration` | `end` と排他 | 切り出す長さ。0 より大きい必要があります。 |
| `end` | `duration` と排他 | 切り出し終了位置。`start` より後である必要があります。 |
| `name` | はい | 出力ファイル名。`output_dir` からの相対パス。拡張子必須。`..` や絶対パスは不可。 |
| `extra_args` | いいえ | このクリップだけに追加する FFmpeg 引数の配列。 |

`[render]` のキー:

| キー | 既定値 | 説明 |
| --- | --- | --- |
| `mode` | `"encode"` | `"encode"` または `"copy"`。 |
| `ffmpeg` | `"ffmpeg"` | FFmpeg 実行ファイル名またはパス。 |
| `preset` | `"medium"` | H.264 のプリセット。 |
| `crf` | `18` | 画質（0–63）。小さいほど高画質・大容量。 |
| `audio_bitrate` | `"192k"` | 音声ビットレート。 |
| `extra_args` | `[]` | 全クリップ共通の追加 FFmpeg 引数。 |

### 時刻の書式

```toml
start = 12.5          # 秒
start = "01:23.5"     # 分:秒
start = "01:02:03.5"  # 時:分:秒
```

## 重複スキップモード

`dedupe = true`（デフォルト）では、以下の条件を満たすクリップのレンダリングを省略します。

- 出力先ディレクトリの `.tomlcut-manifest.json` に、同じレンダリングキーが記録されている
- その出力ファイルが存在し、0 バイトではない

レンダリングキーには次を含みます。

- 元動画の絶対パス・ファイルサイズ・更新日時
- 開始位置・終了位置
- 出力ファイル名
- `mode` / `preset` / `crf` / `audio_bitrate` / `ffmpeg`
- 共通・クリップ個別の `extra_args`

そのため、元動画が更新された場合やエンコード設定を変えた場合は自動的に再レンダリングされます。
同じ設定のクリップが 1 つの TOML に複数書かれている場合も、レンダリングは 1 回だけです。

出力ファイルが存在してもマニフェストに記録がない場合は、既存ファイルを守るためスキップします（メッセージに理由が出ます）。
既存ファイルを上書きしてでも作り直したい場合は `--no-dedupe` を指定してください。

```sh
# 重複していても全クリップをレンダリングし、既存ファイルを上書き
tomlcut render --no-dedupe clips.toml
```

マニフェストを削除すると履歴はリセットされますが、既存の出力ファイルは「マニフェスト未記録」としてスキップされます。作り直す場合は `--no-dedupe` を併用してください。出力ファイル自体を消した場合は、次回の実行で再レンダリングされます。

## 切り出し方式

### encode（デフォルト）

再エンコードして指定位置から正確に切り出します。

- `.mp4` / `.m4v` / `.mov`: H.264 + AAC、`+faststart`
- `.webm`: VP9 + Opus
- その他: H.264 + AAC

`extra_args` でコーデックなどを上書きできます。

### copy

`-c copy` でストリームをそのままコピーします。再エンコードしないため高速で画質劣化もありませんが、切り出し位置は最寄りのキーフレームに丸められます。正確な位置・長さが必要な場合は `encode` を使ってください。

```toml
[render]
mode = "copy"
```

## コマンド

```text
tomlcut render CONFIG [options]
  -o, --output-dir DIR   TOML の output_dir を上書き
  --ffmpeg PATH          FFmpeg 実行ファイルを指定
  --dedupe / --no-dedupe 重複スキップを明示的に切り替え
  --dry-run              実行内容だけ表示（FFmpeg は実行しない）
  --fail-fast            最初の失敗で停止
  -q, --quiet            エラー以外を表示しない
  -v, --verbose          FFmpeg コマンドを表示

tomlcut validate CONFIG  # 設定を検証
tomlcut init [PATH]      # サンプル TOML を生成
```

終了コード:

- `0`: 成功
- `1`: FFmpeg の実行失敗など
- `2`: TOML・マニフェスト・パスの設定エラー
- `130`: 中断

## 開発

```sh
git clone https://github.com/vbe-robotron/tomlcut.git
cd tomlcut
python3 -m unittest discover -s tests -v
```

FFmpeg / ffprobe がインストールされていれば、実際に動画を生成して切り出す結合テストも実行されます。ない場合は自動的にスキップされます。

## ライセンス

MIT License. 詳細は [LICENSE](LICENSE) を参照してください。
