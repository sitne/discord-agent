# Discord AI Agent

Discordサーバー管理用AIエージェントBot。
OpenRouter API経由でLLMを呼び出し、tool callingでDiscordサーバーを操作する。

## セットアップ

### 1. Discord Developer Portal
1. https://discord.com/developers/applications でアプリ作成
2. Bot → Reset Token でトークン取得
3. Bot → Privileged Gateway Intents:
   - **MESSAGE CONTENT INTENT** ✅
   - **SERVER MEMBERS INTENT** ✅
4. OAuth2 → URL Generator:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Administrator` (または個別に設定)
   - 生成されたURLでサーバーに招待

### 2. 環境変数
```bash
cp .env.example .env
# .env を編集してトークンを入力
```

### 3. 起動
```bash
source .venv/bin/activate
python bot.py
```

### 4. systemdで永続化
```bash
sudo cp discord-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable discord-agent
sudo systemctl start discord-agent
```

## 使い方

- `@Bot名 サーバーの情報を教えて` — サーバー情報取得
- `@Bot名 チャンネル一覧を見せて` — チャンネル一覧
- `@Bot名 "test" というテキストチャンネルを作って` — チャンネル作成
- `/ask prompt:ロール一覧を見せて` — スラッシュコマンド
- `/clear` — 会話履歴クリア
- `/model name:openai/gpt-4o-mini` — モデル変更

## ツール一覧

| ツール | 説明 |
|---|---|
| get_server_info | サーバー情報取得 |
| list_channels | チャンネル一覧 |
| list_roles | ロール一覧 |
| get_member_info | メンバー情報 |
| read_messages | メッセージ読み取り |
| create_channel | チャンネル作成 |
| delete_channel | チャンネル削除 |
| edit_channel | チャンネル編集 |
| create_category | カテゴリ作成 |
| create_role | ロール作成 |
| assign_role | ロール付与 |
| remove_role | ロール削除 |
| send_message | メッセージ送信 |
| pin_message | メッセージピン留め |
| delete_messages | メッセージ一括削除 |
| kick_member | キック |
| ban_member | BAN |
| timeout_member | タイムアウト |
