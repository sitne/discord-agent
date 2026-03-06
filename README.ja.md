# 🤖 discord-agent

**[English](README.md)**

79種のツールと5つのスキル、長期記憶、cronベースの自律スケジューラ、Web機能、コード生成+GitHub CI、アイデア→プロジェクト管理のVisionシステム、[SKILL.md標準](https://agentskills.io)準拠の拡張可能スキルシステムを備えた自律AIエージェント型Discord Bot。[OpenRouter](https://openrouter.ai) 経由で任意のLLMを利用可能。

Python 17ファイル・約6,500行。[discord.py](https://github.com/Rapptz/discord.py) 使用。

## 機能

- **LLMエージェント** — OpenRouter経由で任意のモデルを利用、`/model` で実行時に切り替え可能
- **79種のツール** — Discord管理・スレッド・フォーラム・記憶・スケジューラ・Web・システム・コード生成・HTTP・スキル・Visionの11カテゴリ
- **スキルシステム** — [SKILL.md標準](https://agentskills.io)準拠、段階的開示（Progressive Disclosure）、GitHubからコミュニティスキルをインストール可能、botが自律的にスキルを作成
- **長期記憶** — SQLite + FTS5ハイブリッド検索、関連記憶の自動コンテキスト注入
- **自律スケジューラ** — cron式、リトライ・デッドレターキュー・実行履歴
- **Webアクセス** — 検索・ニュース・ページ読み取り（3階層抽出 + キャッシュ）・Playwrightスクリーンショット
- **会話圧縮** — 20メッセージ超過時に自動要約化
- **安全機構** — 破壊的操作（kick/ban/削除）に権限チェック + 確認ボタンUI
- **堅牢なAPI通信** — 指数バックオフリトライ、並列ツール実行、画像/テキスト添付ファイル処理
- **Visionシステム** — アイデア蓄積→プロジェクト構造化→マイルストーン管理、アクティブプロジェクトをコンテキストに自動注入
- **MCPサポート** — `mcp_servers.json` で外部ツールサーバーを接続可能

## ツール一覧

| カテゴリ | 数 | ツール |
|---|---|---|
| **Discord管理** | 18 | `send_message` `edit_message` `delete_messages` `create_channel` `delete_channel` `rename_channel` `set_channel_topic` `list_channels` `list_members` `get_member_info` `set_nickname` `create_role` `assign_role` `remove_role` `kick_member` `ban_member` `unban_member` `get_server_info` |
| **記憶** | 5 | `remember` `recall` `forget` `forget_by_key` `list_memory_categories` |
| **スケジューラ** | 5 | `create_scheduled_task` `list_scheduled_tasks` `delete_scheduled_task` `toggle_scheduled_task` `get_task_history` |
| **Web** | 4 | `web_search` `web_news` `read_webpage` `screenshot_webpage` |
| **システム** | 11 | `run_shell` + GitHub CLIツール 10種 |
| **コード生成** | 6 | `codegen_create_project` `codegen_update_files` `codegen_check_ci` `codegen_list_projects` `codegen_read_file` `codegen_delete_file` |
| **HTTP** | 1 | `http_request` |
| **スレッド** | 7 | `create_thread` `list_threads` `edit_thread` `delete_thread` `thread_add_member` `thread_remove_member` `send_thread_message` |
| **フォーラム** | 5 | `create_forum` `create_forum_post` `list_forum_posts` `manage_forum_tags` `edit_forum` |
| **スキル** | 6 | `list_skills` `load_skill` `create_skill` `install_skill` `remove_skill` `search_community_skills` |
| **Vision** | 8 | `capture_idea` `list_ideas` `search_ideas` `update_idea` `create_project` `list_projects` `update_project` `project_dashboard` |

## セットアップ

**要件:** Python 3.11+, [uv](https://docs.astral.sh/uv/)

1. **クローンと依存関係のインストール**

   ```bash
   git clone https://github.com/sitne/discord-agent.git
   cd discord-agent
   uv sync
   uv run playwright install chromium
   ```

   <details>
   <summary>pipを使う場合</summary>

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium
   ```
   </details>

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

4. **任意: Cloudflare・GitHub CLIのセットアップ**（推奨）

   Webデプロイ（Cloudflare）とGitHub連携を使う場合:

   ```bash
   # GitHub CLI（リポ管理・CI・Issue操作）
   # https://cli.github.com/
   gh auth login

   # Cloudflare CLI（Pages/Workersデプロイ）
   npm install -g wrangler
   wrangler login
   # ブラウザ認証の代わりに .env に CLOUDFLARE_API_TOKEN を設定してもOK
   ```

5. **起動**

   ```bash
   uv run python bot.py
   ```

   または venv を有効化済みなら: `python bot.py`

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
tools_codegen.py        コード生成・GitHubプロジェクト管理・CI連携
tools_http.py           汎用HTTPクライアント（SSRF保護付き）
tools_skills.py         スキル管理ツール（SKILL.md標準）
tools_vision.py         Visionシステム: アイデア・プロジェクト管理
tools_permissions.py    権限チェック・確認ゲート
skills_manager.py       スキル検出・読み込み・インストール・作成
skills/*/SKILL.md       スキルパッケージ（5種: Cloudflareデプロイ・画像生成・翻訳・動画・データ分析）
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
