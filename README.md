# yomiage-bot (Google Cloud TTS 版)

Discord の VC 内蔵チャットに投稿されたメッセージを、**Google Cloud Text-to-Speech** で読み上げる Bot。

VOICEVOX 版（[../yomiage-bot](../yomiage-bot)）から派生。**合成をクラウドに委譲**することで、Mac でもラグが体感ほぼゼロ（短文 0.2〜0.5 秒）になる。

- 非公開 Bot（自分のサーバー専用）
- `bot` コンテナ 1 つだけのシンプル構成
- 詳細な設計方針は [SPEC.md](./SPEC.md) を参照

---

## クイックスタート

```bash
# 1. .env に Discord トークンを設定
cp .env.example .env   # 既に .env があれば不要
nano .env              # DISCORD_TOKEN= の右側を書き換える（Mac なら open -e .env）

# 2. GCP サービスアカウント鍵 JSON を gcp-key.json として配置(下記 §2 参照)

# 3. 起動
docker compose up --build -d
docker compose logs -f bot   # "Logged in as ..." が出れば成功
```

---

## 1. Discord Developer Portal での設定

VOICEVOX 版と完全に同じ手順です。<https://discord.com/developers/applications/>

### Installation タブ
- インストールリンク → **「なし」** に変更して保存

### Bot タブ
- **Reset Token** でトークンを発行（一度しか表示されない）
- **PUBLIC BOT**: OFF
- **Privileged Gateway Intents**
  - MESSAGE CONTENT INTENT: **ON** ← 必須
  - SERVER MEMBERS INTENT: ON 推奨
  - PRESENCE INTENT: OFF

### OAuth2 → URL Generator
**SCOPES**
- `bot`
- `applications.commands`

**BOT PERMISSIONS**
- Send Messages
- Read Message History
- Connect
- Speak

生成された招待 URL をブラウザで開き、自分のサーバーで認証する。

---

## 2. GCP 側の準備（初回だけ）

1. <https://console.cloud.google.com/> でプロジェクト作成（無料）
2. **Cloud Text-to-Speech API** を有効化
3. **IAM と管理 → サービスアカウント** で新規アカウント作成
   - ロール: `Cloud Text-to-Speech User`
4. そのサービスアカウントを開き、**キー → 新しい鍵を作成 → JSON**
5. ダウンロードされた JSON を **`gcp-key.json`** という名前でプロジェクト直下に配置

```
yomiage-bot-gcloud/
├── .env
├── gcp-key.json          ← ここに配置
├── docker-compose.yml
└── ...
```

⚠️ `gcp-key.json` は **絶対に Git に commit しない**（`.gitignore` で除外済み）。

---

## 3. プロジェクト構成

```
yomiage-bot-gcloud/
├── .env                 # 自分で作成（Git 除外）
├── .gitignore
├── gcp-key.json         # 自分で配置（Git 除外）
├── Dockerfile
├── SPEC.md
├── bot.py
├── docker-compose.yml
└── requirements.txt
```

---

## 4. `.env` の設定

```env
DISCORD_TOKEN=（Bot タブで発行したトークン）
GCP_VOICE_NAME=ja-JP-Neural2-B
GCP_LANGUAGE_CODE=ja-JP
GCP_SPEAKING_RATE=1.0
```

### 主な話者

| 名前 | 種別 | 性質 |
|------|------|------|
| `ja-JP-Neural2-B` | Neural2 | 男性、最新の高品質 |
| `ja-JP-Neural2-C` | Neural2 | 男性 |
| `ja-JP-Neural2-D` | Neural2 | 女性 |
| `ja-JP-Wavenet-A`〜`D` | Wavenet | 旧世代だが十分自然 |
| `ja-JP-Standard-A`〜`D` | Standard | 軽量、料金枠を広く使える |

`GCP_SPEAKING_RATE` は `0.25` 〜 `4.0`（既定 `1.0`）。

---

## 5. Docker 操作

```bash
docker compose up --build -d                # 起動
docker compose logs -f bot                  # ログ確認
docker compose down                         # 停止
docker compose up -d --force-recreate bot   # .env を読み直して再起動
```

> ⚠️ **`docker compose restart` では `.env` の変更が反映されない**。`.env` を編集したら必ず `up -d --force-recreate` を使うこと。

### 常駐運用（ラズパイ / 再起動後の自動復帰）

`docker-compose.yml` の `restart: unless-stopped` と、Docker 自体の自動起動の
2 つが揃っていれば、**ホストを再起動しても Bot は自動で復帰する**。

```bash
sudo systemctl enable --now docker   # Docker をホスト起動時に自動起動（初回だけ）
docker compose up --build -d         # 一度起動しておけば以後は自動復帰
```

確認:

```bash
systemctl is-enabled docker          # → enabled
docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' yomiage-bot   # → unless-stopped
```

- `docker compose down` / `stop` で**手動停止した場合は再起動後も起動しない**（`unless-stopped` の仕様）。
  再び常駐させるには `docker compose up -d` を実行する。
- ログは 10MB × 3 世代でローテーションされる（SD カード保護のため `logging` で設定済み）。

> 別の Bot（`discord-todo-bot`）は systemd サービスで常駐しているが、こちらは Docker の
> 再起動ポリシーが同じ役割を果たすので、systemd unit を別途作る必要はない。

---

## 6. Discord での使い方

| コマンド | 動作 |
|---------|------|
| `/join`  | 実行者がいる VC に Bot を呼び、その VC に紐づくチャットを読み上げ対象に登録 |
| `/leave` | VC から退出し、読み上げ対象を解除 |

1. 自分が VC に入る
2. `/join` を実行（どのチャンネルで打っても OK）
3. **VC のチャット欄**（VC アイコン横のチャット）に送ったメッセージが読み上げられる

別の VC で `/join` を打ち直すと、Bot は新しい VC に移動。

---

## 7. 読み上げ仕様

- Bot 自身のメッセージと DM は読み上げない
- URL → 「URL」に置換
- 絵文字（Unicode / カスタム）→ 文の区切りに昇格
- `(笑)` `(泣)` `(怒)` `笑` 等 → 文の区切りに昇格
- メンション → 削除
- 100 文字超 → 切り詰めて末尾に「以下略」
- 添付ファイル・スタンプ・埋め込みは対象外
- **文単位パイプライン再生**で、最初の音までのラグを最小化

---

## 8. 料金

GCP TTS の無料枠（毎月）:

| 種別 | 無料枠 |
|------|--------|
| Standard 音声 | 400 万文字 |
| Wavenet / Neural2 | 100 万文字 |

個人 Bot 用途なら **事実上ずっと無料** で使えます。

---

## 9. トラブルシューティング

| 症状 | 対処 |
|------|------|
| `env file ... .env not found` | プロジェクト直下に `.env` を作成 |
| `gcp-key.json` を mount できない | プロジェクト直下に `gcp-key.json` を配置（GCP コンソールから DL） |
| `gcp-key.json` が**ディレクトリ**になっている | 鍵を置く前に `up` すると Docker が空ディレクトリを作る。`rm -rf gcp-key.json` してから鍵 JSON を配置し直し、`up -d --force-recreate` |
| `google.auth.exceptions.DefaultCredentialsError` | `gcp-key.json` のパスや権限、`docker-compose.yml` の volume マウントを確認 |
| `401 Unauthorized` / `Improper token has been passed` | Discord トークンを `.env` に正しく設定し、`docker compose up -d --force-recreate bot` で再起動 |
| `PrivilegedIntentsRequired` | Bot タブの MESSAGE CONTENT INTENT を ON |
| `/join` が候補に出ない | OAuth2 SCOPES に `applications.commands` を含めて再招待 |
| Bot がオフライン | `docker compose up -d` でコンテナ起動 |

---

## 10. セキュリティ注意事項

- `.env`（Discord トークン）と `gcp-key.json`（GCP 認証鍵）は **絶対にチャット・SNS・Git に貼らない**。
- 万一漏洩した場合: Discord は **Reset Token**、GCP は **サービスアカウントの鍵を削除して再発行**。
- `.gitignore` に両方が登録されていることを定期的に確認する。
