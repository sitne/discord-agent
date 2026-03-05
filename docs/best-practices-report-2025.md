# 機能別ベストプラクティス調査レポート (2025-2026)

> 2026年3月時点の最新調査結果。現在の実装との差分分析と改善提案。

---

## 1. 記憶システム (remember / recall / forget / list_memory_categories)

### 現在の実装
- SQLite + FTS5 (BM25) でキーワード検索
- guild_id スコープ、category + key + content モデル
- システムプロンプトに最新10件を自動注入（content 150文字切り詰め）
- recall は FTS5 フレーズマッチ or カテゴリ一覧 or 最新20件

### 業界の主要アプローチ

#### Mem0 (Stars: 30K+)
- **3種メモリ**: user memory, session memory, agent memory
- **Graph Memory**: Neo4j/Memgraph でエンティティ＋関係を抽出・保存
- Vector DB (embeddings) + Graph DB のハイブリッド検索
- 取得時: vector search で候補絞り → graph が関連コンテキスト追加
- `user_id`, `agent_id`, `run_id` でマルチスコープ

#### Letta/MemGPT (論文: arXiv 2310.08560)
- **3層メモリアーキテクチャ**:
  | レイヤー | 目的 | サイズ制限 | 検索方法 |
  |---------|------|----------|--------|
  | Core Memory (blocks) | 常にコンテキストに存在するID/ペルソナ | ブロックあたり2000文字 | ラベルで直接アクセス |
  | Recall Memory | 会話履歴 | コンテキストウィンドウ制限 | テキスト + セマンティック |
  | Archival Memory | 長期知識 | **無制限** | セマンティック類似度 + タグ + タイムスタンプ |
- **自己編集メモリ**: エージェントが自分のメモリブロックを編集可能
- ブロックに文字数上限を設けてコンテキストウィンドウ配分を制御
- Optimistic locking で並行アクセス安全

#### FTS5 vs Embeddings (Substratia の分析)
- **FTS5が勝つケース**: 100〜1,000件のメモリ、ローカル環境、パーソナルAI
  - 46MB のモデルウェイト不要
  - 起動 30秒→1秒以下
  - 1,500トークン節約/レスポンス
- **Embeddingsが勝つケース**: 数百万ドキュメント、多言語、画像-テキスト類似度
- **ハイブリッドスコアリング** (推奨):
  ```
  score = 0.4 * relevance + 0.3 * importance + 0.2 * recency + 0.1 * frequency
  ```
- **3段階レスポンス**: Minimal (30トークン) / Standard (200) / Full (500)

### 現在の実装の問題点

1. **ナイーブなコンテキスト注入**: 最新10件のみ。クエリとの関連性なし
2. **FTSフレーズマッチのみ**: 「server rules」で「the rules of the server」がヒットしない
3. **ユーザースコープなし**: guild_id のみ。ユーザー固有記憶なし
4. **メモリ圧縮/統合なし**: 古いメモリが蓄積するだけ
5. **重要度/頻度なし**: すべてのメモリが同等扱い
6. **forget が ID 必須**: key/category での削除不可
7. **コンテンツ切り詰め 150文字**: 長いメモリの情報損失

### 改善提案 (優先順位順)

#### P1: ハイブリッドスコアリング
```python
# memories テーブルに追加カラム
importance INTEGER DEFAULT 5,    -- 1-10 (LLMが設定)
access_count INTEGER DEFAULT 0,  -- recall時にインクリメント
last_accessed_at REAL            -- 最終アクセス日時

# スコア計算
score = 0.4 * fts_rank + 0.3 * (importance/10) + 0.2 * recency + 0.1 * (access_count/max_count)
```

#### P2: クエリ関連メモリの注入
- ユーザーメッセージからキーワード抽出 → FTS5 で関連メモリ検索
- 最新10件 → 「関連5件 + 最新5件」に変更

#### P3: user_id スコープ追加
```sql
ALTER TABLE memories ADD COLUMN user_id TEXT;  -- NULLable (guild全体メモリ)
CREATE INDEX idx_memories_user ON memories(guild_id, user_id);
```

#### P4: メモリ統合/圧縮
- 同カテゴリの古いメモリ (30日以上、アクセスなし) を LLM で要約統合
- `consolidate_memory` ツール or 定期タスク

#### P5: forget の拡張
- `forget_by_key(category, key)` 追加
- `forget_category(category)` 追加 (カテゴリ一括削除)

#### P6: Letta式メモリブロック導入 (将来)
- Core Memory: 常にコンテキストに入る固定ブロック (サーバールール、ボット設定)
- Archival Memory: 検索でアクセスする大量ストレージ
- ブロックごとの文字数制限でコンテキストウィンドウ管理

---

## 2. スケジューラ (create/list/delete/toggle_scheduled_task)

### 現在の実装
- SQLite の scheduled_tasks テーブル + 30秒ポーリング
- 5フィールド cron + プリセット (@hourly, @daily...)
- next_run_at 事前計算 → get_due_tasks でフェッチ
- AgentCog._run_agent() にプロキシメッセージを渡して実行
- エラー時はチャンネルに通知 + next_run 計算失敗でタスク無効化

### 業界の主要アプローチ

#### OpenClaw (最も関連性の高い参照実装)
- **Coordinator パターン**: メインエージェントがワーカーサブエージェントを生成
- エージェントが**自分自身のcronジョブを管理** (作成/変更/削除)
- モニタリングcron: 60秒ごとに進捗チェック → 15分超でキル
- **90秒更新サイクル**: ユーザーへの進捗通知の最適間隔

#### 信頼性パターン (業界コンセンサス)
- **指数バックオフ + ジッター**: 回復率 91% (リトライなしの 20% vs)
- **サーキットブレーカー**: 5連続失敗 → OPEN (60秒 fail fast) → HALF_OPEN → CLOSED
- **Dead Letter Queue**: max retry 超過タスクを保存、手動リプレイ可能
- **15分ルール**: 15分以内に完了しないタスクはスタック → キル

#### ガードレール (5層防御)
| 層 | 内容 | 推奨値 |
|---|------|-------|
| 予算 | タスクあたりトークン上限 | 10,000 tokens |
| 時間 | ハードキル | 5分 (デフォルト)、最大15分 |
| スコープ | 許可ツールのホワイトリスト | デフォルト拒否 |
| 並行性 | 同時実行数上限 | 5タスク |
| 入力 | プロンプトインジェクション防止 | サニタイズ |

#### Human-in-the-Loop
| リスク | 例 | 動作 |
|-------|---|------|
| 低 | 要約、ステータスチェック | 自動実行、ログのみ |
| 中 | メッセージ送信、ファイル作成 | 実行 + ログ + 定期レビュー |
| 高 | 外部API、一括操作 | **承認必須** |

### 現在の実装の問題点

1. **並行性ガードなし**: 30秒以上かかるタスクが二重実行される可能性
2. **実行履歴テーブルなし**: last_run_at のみ。過去の実行結果/エラーが追えない
3. **リトライロジックなし**: 1回の next_run 計算失敗でタスク永久無効化
4. **タイムアウトなし**: エージェントループが無限に走る可能性 (max 10ラウンドのみ)
5. **予算管理なし**: タスクあたりのトークン/API呼び出し制限なし
6. **タイムゾーン**: UTC固定、ギルドごとのTZ設定なし
7. **cron パーサー**: 分単位ブルートフォース (最大525,960回イテレーション)
8. **ProxyObject が最小限**: `.id`, `.mentions` 等なく、一部のエージェントコードが失敗する可能性

### 改善提案 (優先順位順)

#### P1: 実行履歴テーブル
```sql
CREATE TABLE task_executions (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES scheduled_tasks(id),
    status TEXT NOT NULL,          -- 'running', 'success', 'failed', 'timeout', 'dlq'
    started_at REAL NOT NULL,
    completed_at REAL,
    tokens_used INTEGER DEFAULT 0,
    tool_calls INTEGER DEFAULT 0,
    error_message TEXT,
    result_summary TEXT,
    retry_count INTEGER DEFAULT 0
);
```

#### P2: 並行性ガード + タイムアウト
```python
# タスク取得時にステータスを 'running' に更新 (楽観的ロック)
# UPDATE scheduled_tasks SET status='running' WHERE id=? AND status='pending'
# 5分ハードタイムアウト (asyncio.wait_for)
# 完了/失敗で 'pending' に戻す
```

#### P3: リトライ + Dead Letter Queue
- エラー分類: retryable (429, 500, timeout) vs non-retryable (400, auth)
- 指数バックオフ: 1分 → 2分 → 4分 → 8分 → 16分 (max 5回)
- max retry 超過 → task_dlq テーブルに移動 + Discord 通知

#### P4: タスクあたりガードレール
```sql
ALTER TABLE scheduled_tasks ADD COLUMN max_execution_seconds INTEGER DEFAULT 300;
ALTER TABLE scheduled_tasks ADD COLUMN max_token_budget INTEGER DEFAULT 10000;
ALTER TABLE scheduled_tasks ADD COLUMN allowed_tools TEXT;  -- JSON array, NULL = all
```

#### P5: タイムゾーン対応
```sql
ALTER TABLE scheduled_tasks ADD COLUMN timezone TEXT DEFAULT 'UTC';
-- ユーザーが「毎朝9時」と言ったら JST で保存
```

#### P6: HITL 承認フロー (将来)
- 高リスクタスクは実行前に Discord で ✅/❌ リアクション待ち
- タイムアウト (10分) → スキップ + ログ

---

## 3. Web ツール (web_search / web_news / read_webpage / screenshot_webpage)

### 現在の実装
- **検索**: ddgs (DuckDuckGo) — 無料、APIキー不要、max 10件
- **ニュース**: ddgs.news() — 同上
- **ページ読み取り**: trafilatura.fetch_url() + extract() — 3000文字上限
- **スクリーンショット**: Playwright headless Chromium — 1280x800、networkidle 待ち
- すべて asyncio.to_thread() で非同期化

### 業界の検索 API 比較

| API | コスト/1K件 | APIキー | 速度 | 特徴 |
|-----|-----------|---------|------|------|
| **ddgs** | 無料 | 不要 | 速い | プロトタイプ向き。レート制限 20-30 req/min |
| **Tavily** | $5-8 | 必要 | ~1.9s | AI特化。抽出済みコンテンツ。無料1,000/月 |
| **Serper** | $0.30-1.00 | 必要 | 速い | 最安 Google SERP アクセス |
| **Brave** | $3-5 | 必要 | <1s | 独自インデックス (30B+ページ)。無料2,000/月 |
| **SearXNG** | 無料 (自前) | 不要 | 可変 | メタ検索70+エンジン。Docker必要 |

### コンテンツ抽出ベンチマーク (750文書、2236セグメント)

| ライブラリ | Precision | Recall | **F-Score** | 速度 |
|-----------|-----------|--------|------------|------|
| **trafilatura** | 0.914 | 0.904 | **0.909** | 7.1x |
| readability-lxml | 0.891 | 0.729 | 0.801 | 5.8x |
| newspaper3k | 0.895 | 0.593 | 0.713 | 12x |
| goose3 | 0.934 | 0.690 | 0.793 | 22x |

> trafilatura は F-Score で圧倒的トップ。現在の選択は正しい。

| サービス | タイプ | 強み | 弱み |
|---------|-------|------|------|
| **Jina Reader** | API (`r.jina.ai/{url}`) | 超簡単。クリーンなMarkdown。無料枠あり | 単一ページのみ。レート制限 |
| **Firecrawl** | 有料 API | 検索+抽出一体。再帰クロール | 有料。ベンダー依存 |
| **Crawl4AI** | OSS (61K★) | ブラウザベース非同期クローラー。LLM向けMarkdown | 重い。セットアップ複雑 |

### 現在の実装の問題点

1. **BeautifulSoup フォールバックなし**: trafilatura 失敗時はエラーメッセージのみ
2. **JS レンダリングなし** (read_webpage): SPA/動的ページの内容取得不可
3. **キャッシュなし**: 同じ検索/ページを毎回ネットワークアクセス
4. **レート制限なし**: ddgs に対する連続リクエストでブロックされる可能性
5. **スニペット切り詰め 200文字**: LLM への情報量不足
6. **ページ内容 3000文字**: 長い記事の重要情報を逃す
7. **Playwright ブラウザ毎回起動**: 2-5秒のオーバーヘッド
8. **エラーメッセージが汎用的**: ネットワークエラー/404/抽出失敗の区別なし

### 改善提案 (優先順位順)

#### P1: 3段階コンテンツ抽出
```
Tier 1: requests.get() + trafilatura.extract()  ← 高速、90%のケース
    ↓ 失敗
Tier 2: Jina Reader API (r.jina.ai/{url})       ← シンプルなフォールバック  
    ↓ 失敗 or JS必要
Tier 3: Playwright → trafilatura.extract(html)   ← 重いが確実
```

#### P2: キャッシュ層
```python
# SQLite キャッシュ
CREATE TABLE web_cache (
    url_hash TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    content TEXT,
    fetched_at REAL NOT NULL,
    ttl_seconds INTEGER DEFAULT 86400  -- 24時間デフォルト
);

# 検索キャッシュ
CREATE TABLE search_cache (
    query_hash TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    results TEXT,  -- JSON
    searched_at REAL NOT NULL,
    ttl_seconds INTEGER DEFAULT 3600  -- 1時間デフォルト
);
```

#### P3: レート制限
```python
# ddgs: 最低2秒間隔
# ユーザーあたり: 5検索/分
# 429 時: 指数バックオフ
import asyncio
from collections import defaultdict

class RateLimiter:
    def __init__(self, min_interval=2.0):
        self.last_call = 0
    
    async def wait(self):
        now = asyncio.get_event_loop().time()
        wait_time = self.min_interval - (now - self.last_call)
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        self.last_call = asyncio.get_event_loop().time()
```

#### P4: コンテンツ上限拡張
- ページ内容: 3000 → 6000文字 (Gemini Flash のコンテキストは十分大きい)
- 検索スニペット: 200 → 400文字
- trafilatura に `favor_recall=True`, `deduplicate=True` 追加

#### P5: Playwright ブラウザプール
```python
# シングルトンブラウザインスタンス
# コンテキスト分離で並行利用
# アイドル5分でブラウザ閉じる
class BrowserPool:
    _browser = None
    _last_used = 0
    
    @classmethod
    async def get_page(cls):
        if cls._browser is None:
            playwright = await async_playwright().start()
            cls._browser = await playwright.chromium.launch(...)
        ctx = await cls._browser.new_context(viewport={...})
        return await ctx.new_page()
```

#### P6: リソースブロッキング (スクリーンショット高速化)
```python
# 画像/CSS/フォントをブロック → 3-5倍高速化
await page.route("**/*.{png,jpg,jpeg,gif,svg,css,font,woff,woff2}",
                 lambda route: route.abort())
# read_webpage用: domcontentloaded で十分 (networkidle 不要)
await page.goto(url, wait_until="domcontentloaded", timeout=30000)
```

---

## 総合まとめ: 実装ロードマップ

### フェーズ1 (すぐやるべき — 低コスト高効果)
| 機能 | 改善 | 工数 |
|------|------|------|
| 記憶 | クエリ関連メモリの注入 (P2) | 小 |
| 記憶 | forget_by_key 追加 (P5) | 小 |
| Web | コンテンツ上限拡張 + trafilatura オプション (P4) | 小 |
| Web | レート制限 (P3) | 小 |
| スケジューラ | 実行履歴テーブル (P1) | 中 |

### フェーズ2 (次にやる — 信頼性向上)
| 機能 | 改善 | 工数 |
|------|------|------|
| 記憶 | ハイブリッドスコアリング (P1) | 中 |
| 記憶 | user_id スコープ (P3) | 中 |
| Web | 3段階コンテンツ抽出 (P1) | 中 |
| Web | キャッシュ層 (P2) | 中 |
| スケジューラ | 並行性ガード + タイムアウト (P2) | 中 |
| スケジューラ | リトライ + DLQ (P3) | 中 |

### フェーズ3 (将来 — アーキテクチャ進化)
| 機能 | 改善 | 工数 |
|------|------|------|
| 記憶 | メモリ統合/圧縮 (P4) | 大 |
| 記憶 | Letta式メモリブロック (P6) | 大 |
| Web | Playwright ブラウザプール (P5) | 中 |
| スケジューラ | ガードレール (P4) | 中 |
| スケジューラ | HITL 承認フロー (P6) | 大 |
| スケジューラ | タイムゾーン対応 (P5) | 中 |

---

## 調査ソース

- [Substratia: Why We Chose FTS5 Over Embeddings](https://substratia.io/blog/why-fts5-over-embeddings/)
- [Letta: Memory Blocks](https://www.letta.com/blog/memory-blocks)
- [Mem0: Graph Memory](https://docs.mem0.ai/open-source/features/graph-memory)
- [DeepWiki: Letta Memory System](https://deepwiki.com/letta-ai/letta/3-memory-system)
- [Maxim: Demystifying AI Agent Memory](https://www.getmaxim.ai/articles/demystifying-ai-agent-memory-long-term-retention-strategies/)
- [MemGPT Paper](https://arxiv.org/abs/2310.08560)
- [Zep/Graphiti: Temporal Knowledge Graph](https://arxiv.org/abs/2501.13956)
- [trafilatura ベンチマーク](https://trafilatura.readthedocs.io/)
- OpenClaw GitHub (cron/scheduler architecture)
- BunQueue, BullMQ ドキュメント
