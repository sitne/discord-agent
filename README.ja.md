# 🤖 discord-agent

**[English](README.md)**

45種のツール、長期記憶、cronベースの自律スケジューラ、Web機能を備えた自律AIエージェント型Discord Bot。[OpenRouter](https://openrouter.ai) 経由で任意のLLMを利用可能（デフォルト: Gemini 2.5 Flash）。

Python 12ファイル・約3,600行。[discord.py](https://github.com/Rapptz/discord.py) 使用。

## 機能

- **LLMエージェント** — OpenRouter経由で任意のモデルを利用、`/model` で実行時に切り替え可能
- **45種のツール** — Discord管理・記憶・スケジューラ・Web・システムの5カテゴリ
- **長期記憶** — SQLite + FTS5ハイブリッド検索、関連記憶の自動コンテキスト注入
- **自律スケジューラ** — cron式、リトライ・デッドレターキュー・実行履歴
- **Webアクセス** — 検索・ニュース・ページ読み取り（3階層抽出 + キャッシュ）・Playwrightスクリーンショット
- **会話圧縮** — 20メッセージ超過時に自動要約化
- **安全機構** — 破壊的操作（kick/ban/削除）に権限チェック + 確認ボタンUI
- **堅牢なAPI通信** — 指数バックオフリトライ、並列ツール実行、画像/テキスト添付ファイル処理
- **MCPサポート** — `mcp_servers.json` で外部ツールサーバーを接続可能

## ツール一覧

| カテゴリ | 数 | ツール |
|---|---|---|
| **Discord管理** | 18 | `send_message` `edit_message` `delete_messages` `create_channel` `delete_channel` `rename_channel` `set_channel_topic` `list_channels` `list_members` `get_member_info` `set_nickname` `create_role` `assign_role` `remove_role` `kick_member` `ban_member` `unban_member` `get_server_info` |
| **記憶** | 5 | `remember` `recall` `forget` `forget_by_key` `list_memory_categories` |
| **スケジューラ** | 5 | `create_scheduled_task` `list_scheduled_tasks` `delete_scheduled_task` `toggle_scheduled_task` `get_task_history` |
| **Web** | 4 | `web_search` `web_news` `read_webpage` `screenshot_webpage` |
| **システム** | 11 | `run_shell` + GitHub CLIツール 10種 |

## セットアップ

**要件:** Python 3.12+

1. **クローンと依存関係のインストール**

   ```bash
   git clone https://github.com/sitne/discord-agent.git
   cd discord-agent
   pip install -r requirements.txt
   playwright install chromium
   ```

2. **環境変数の設定**

   ```bash
   cp .env.example .env
   ```

   `.env` を編集:

   ```env
   DISCORD_TOKEN=your-bot-token
   OPENROUTER_API_KEY=your-api-key

   # 任意: デフォルトモデルの変更
   # OPENROUTER_MODEL=openai/gpt-4o-mini
   ```

3. **Discord Botの準備**

   [Discord Developer Portal](https://discord.com/developers/applications) でBotを作成し、以下の **Privileged Intents** を有効化:
   - Message Content
   - Server Members

   チャンネル・ロール・メッセージ・メンバーの管理権限を付与してサーバーに招待。

4. **起動**

   ```bash
   python bot.py
   ```

## コマンド

| コマンド | 説明 |
|---|---|
| `/ask` | AIにプロンプトを送信（画像添付可） |
| `/model` | LLMモデルの確認・切り替え（例: `google/gemini-2.5-flash`, `openai/gpt-4o-mini`） |

## 📂 プロジェクト構成

```
bot.py                  エントリポイント、Bot初期化
db.py                   SQLiteデータベース、記憶ストア、FTS5検索
tools.py                Discord管理ツール (18種)
tools_web.py            Web検索・ページ読み取り・スクリーンショット
tools_system.py         シェル実行・GitHub CLIツール
tools_permissions.py    権限チェック・確認ゲート
context_manager.py      会話履歴管理・圧縮
cron_parser.py          cron式パーサー
mcp_manager.py          MCPサーバー管理・ツールルーティング
cogs/
  agent.py              エージェントコアループ、LLM呼び出し、ツールディスパッチ
  collector.py          メッセージ・添付ファイル収集
  scheduler.py          cronスケジューラ、リトライ、DLQ、実行履歴
```

## MCPサーバー

外部の [Model Context Protocol](https://modelcontextprotocol.io/) ツールサーバーを接続するには、プロジェクトルートに `mcp_servers.json` を作成:

```json
{
  "servers": [
    {
      "name": "example",
      "command": "npx",
      "args": ["-y", "@example/mcp-server"]
    }
  ]
}
```

MCPツールは自動的に検出され、組み込みツールと併せてエージェントから利用可能になります。

## 依存ライブラリ

- [discord.py](https://github.com/Rapptz/discord.py) — Discord API
- [openai](https://github.com/openai/openai-python) — OpenRouter互換LLMクライアント
- [aiosqlite](https://github.com/omnilib/aiosqlite) — 非同SQLite + FTS5
- [ddgs](https://github.com/deedy5/duckduckgo_search) — Web検索
- [trafilatura](https://github.com/adbar/trafilatura) + [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/) — コンテンツ抽出
- [playwright](https://playwright.dev/python/) — スクリーンショット
- [mcp](https://github.com/modelcontextprotocol/python-sdk) — MCPクライアント

## ライセンス

MIT
