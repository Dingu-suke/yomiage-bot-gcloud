# yomiage-bot (Google Cloud TTS 版) 仕様・方針

Discord のテキストチャンネル(VC 内蔵チャット)に投稿されたメッセージを、**Google Cloud Text-to-Speech** でボイスチャンネルに読み上げさせる、個人／小規模サーバー向け Bot。

VOICEVOX 版から派生。**合成を外部クラウドに委譲することでローカル CPU 負荷をゼロにし、読み上げのラグを大幅に削減する**ことが目的。

## 1. 目的とスコープ

- 非公開 Bot として運用する（Public Bot を OFF にし、自分が招待したサーバーでのみ動作）。
- 1 サーバーにつき 1 つの VC を読み上げ対象とする（VC 内蔵チャットの投稿を読み上げる）。
- 合成は Google Cloud Text-to-Speech API に委譲し、Bot 本体は I/O とキュー制御に専念する。

## 2. 構成

```
[Discord] ── Gateway ──> [bot コンテナ (Python / discord.py)]
                              │
                              └── HTTPS ──> [Google Cloud TTS API]
```

VOICEVOX 版とは異なり、**`bot` サービスのみ**で構成される。GCP 認証はサービスアカウント鍵 JSON（`gcp-key.json`）を `/app/gcp-key.json` にマウントし、`GOOGLE_APPLICATION_CREDENTIALS` 経由で読み込む。

## 3. コマンド（Slash Commands）

| コマンド | 動作 |
|---|---|
| `/join` | 実行者が参加している VC に Bot が参加し、その **VC に紐づくチャット** を読み上げ対象として登録する。 |
| `/leave` | VC から退出し、読み上げ対象を解除する。 |

別の VC で `/join` を叩き直すと、Bot はその VC に移動し、読み上げ対象もその VC のチャットへ切り替わる。

## 4. 読み上げ仕様

- 対象は Bot が参加している VC のチャットのみ。ギルド単位で 1 チャンネル。
- Bot 自身のメッセージと DM は読み上げない。
- ギルドごとに `asyncio.Queue` で逐次再生する。割り込みは行わず FIFO。
- 1 メッセージは句点（`。．！？!?` / 改行 / `、`）で文（節）に分割し、**文単位パイプライン**で再生する: 現在の文を再生している間に次の文の合成を裏で走らせ、最初の音までのラグを「1 文分の合成時間」に抑える。
- GCP TTS の合成時間は短文で 0.2〜0.5 秒程度のため、Mac でも体感ほぼリアルタイム。

## 5. テキスト前処理

`bot.py` の `TextProcessor.sanitize()` で以下を行う。

- URL（`https?://...`）→ 文字列「URL」に置換。
- カスタム絵文字（`<a?:name:id>`）→ 改行に置換（=文の区切りに昇格）。
- メンション（`<@id>` / `<#id>` / `<@&id>`）→ 削除。
- Unicode 絵文字（主要レンジ + variation selector / ZWJ）→ 改行に置換。
- `(笑)` `(泣)` `(怒)` `(爆)` `(嬉)` `(w)` 等（全角・半角・連続カッコ可）→ 改行に置換。
- 末尾の `笑` / `笑笑` 等（句読点・空白・文末の直前のみ）→ 改行に置換。「笑顔」のような通常語の中の `笑` は残す。
- 前後の空白を除去。
- **100 文字を超える場合は切り詰め、末尾に「以下略」を付与**。
- 前処理後に空文字となったメッセージはキューに入れない。

そのうえで `TextProcessor.split_sentences()` が `。．！？!?` / `、` / 改行 で文（節）に分割する。これがパイプライン再生の段の粒度になる。

## 6. 音声・話者

GCP TTS の日本語ニューラル音声を利用する。代表例:

| 名前 | 種別 | 性質 |
|---|---|---|
| `ja-JP-Neural2-B` | Neural2 | 男性、最新世代の高品質 |
| `ja-JP-Neural2-C` | Neural2 | 男性 |
| `ja-JP-Neural2-D` | Neural2 | 女性 |
| `ja-JP-Wavenet-A〜D` | Wavenet | 旧世代だが十分自然 |
| `ja-JP-Standard-A〜D` | Standard | 軽量。料金枠を広く使える |

`.env` の `GCP_VOICE_NAME` で切り替え可。`GCP_SPEAKING_RATE` で話速（0.25〜4.0）を調整可。

## 7. 環境変数

| 変数 | 必須 | 既定 | 用途 |
|---|---|---|---|
| `DISCORD_TOKEN` | ✅ | — | Discord Developer Portal の **Bot** タブで発行したトークン。 |
| `GOOGLE_APPLICATION_CREDENTIALS` | ✅ | `/app/gcp-key.json` (compose で注入) | GCP サービスアカウント鍵 JSON のパス。 |
| `GCP_VOICE_NAME` | ❌ | `ja-JP-Neural2-B` | TTS の話者名。 |
| `GCP_LANGUAGE_CODE` | ❌ | `ja-JP` | 言語コード。 |
| `GCP_SPEAKING_RATE` | ❌ | `1.0` | 話速（0.25〜4.0）。 |

## 8. GCP 側の準備

1. <https://console.cloud.google.com/> で GCP プロジェクト作成。
2. **Cloud Text-to-Speech API** を有効化。
3. **IAM と管理 → サービスアカウント** で新規アカウントを作成し、ロールに `Cloud Text-to-Speech User` を付与。
4. そのアカウントの **キー（JSON）を作成 → ダウンロード** し、プロジェクト直下に `gcp-key.json` として配置。
5. `.gitignore` で `gcp-key.json` を必ず除外する（鍵漏洩防止）。

## 9. 料金

GCP TTS の無料枠（毎月）:

- Standard 音声: 400 万文字
- Wavenet / Neural2 / Polyglot 音声: 100 万文字

個人 Bot 用途であれば事実上無料枠内で収まる。超過分は Neural2 で $16/100 万文字程度。

## 10. Discord 側の設定

### Intents（Bot タブ）
- `MESSAGE CONTENT INTENT`: **ON**
- `SERVER MEMBERS INTENT`: ON 推奨
- `PRESENCE INTENT`: OFF

### Bot 公開設定
- `PUBLIC BOT`: **OFF**（非公開 Bot のため）
- `REQUIRES OAUTH2 CODE GRANT`: OFF

### 招待時に要求する権限（OAuth2 → URL Generator）
- SCOPES: `bot`, `applications.commands`
- BOT PERMISSIONS: `Send Messages`, `Read Message History`, `Connect`, `Speak`

## 11. 運用方針

- コンテナは `restart: unless-stopped` で異常終了時に自動復旧する。
- ログは標準出力に Python `logging` で出力する（`logger=yomiage`）。
- 合成失敗時はそのメッセージをスキップしてログに記録し、キューの次のメッセージへ進む（Bot は落ちない）。
- VOICEVOX 版で必要だった `voicevox` コンテナや ARM/x86 イメージの切替は不要。

## 12. 既知の制約 / 非対応

- 読み上げ対象 VC の設定はメモリ上のみで、Bot 再起動で失われる。
- 添付ファイル・スタンプ・埋め込みは読み上げ対象外（テキスト本文のみ）。
- 1 ギルドあたりの並列再生・割り込み・スキップ機能は持たない。
- ユーザーごとの話者切替は持たない（ギルド共通）。
- ずんだもん等のキャラクター音声はない（GCP TTS は自然な人の声）。

## 13. 将来検討する拡張

- 読み上げ対象 VC の永続化（ファイル or SQLite）。
- `/skip`, `/clear` 等のキュー操作コマンド。
- ユーザーごとの話者割り当て。
- 辞書機能（読み替え登録）。
