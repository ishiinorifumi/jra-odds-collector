# jra-odds-collector

JRA公式サイト（jra.go.jp）の公開オッズページから、発走前の複数時点（90/60/30/10/2分前）のオッズを
GitHub Actions上で自動収集し、Googleスプレッドシートに記録するツールです。

## 事前準備（手動、初回のみ）

### 1. Googleサービスアカウントの作成
1. [Google Cloud Console](https://console.cloud.google.com/) で新規プロジェクトを作成（または既存のものを利用）
2. 「APIとサービス」→「ライブラリ」から **Google Sheets API** を有効化
3. 「IAMと管理」→「サービスアカウント」→「作成」
4. 作成したサービスアカウントの「鍵」タブから JSON鍵を作成・ダウンロード

### 2. スプレッドシートの準備
1. 記録用のGoogleスプレッドシートを新規作成
2. 共有設定で、サービスアカウントのメールアドレス（`xxx@xxx.iam.gserviceaccount.com`）を
   **編集者**として追加
3. スプレッドシートのURLからIDを控える（`https://docs.google.com/spreadsheets/d/【ここ】/edit`）

### 3. GitHub Secretsの設定
このリポジトリの Settings → Secrets and variables → Actions で以下を登録:
- `GOOGLE_SERVICE_ACCOUNT_JSON`: ダウンロードしたJSON鍵ファイルの中身をそのまま貼り付け
- `ODDS_SPREADSHEET_ID`: 上記で控えたスプレッドシートID

## 動作の仕組み
- GitHub Actionsが毎週土日の8:00/13:00(JST)に自動起動（`.github/workflows/collect.yml`）
- 起動直後にJRA公式サイトから当日の開催・レース一覧・発走時刻を取得
- 各レースの発走90/60/30/10/2分前になるたびオッズを取得してスプレッドシートに追記
- 生HTMLはActionsのartifact（90日保持）として別途保存（パーサーの不備に備えるため）

## 収集データ
- `tanpuku_odds` シート: 単勝・複勝オッズ（構造化済み）
- `other_odds_raw` シート: 枠連・馬連・ワイド・馬単・3連複・3連単（JSON形式で生データ保存、今後構造化予定）
- `collection_log` シート: 実行ログ（開始・終了・エラー）

## 注意事項
- リクエスト間隔は最低1.5秒空け、サーバー負荷に配慮しています
- 個人の研究目的での利用を前提としており、再配布・商用利用は想定していません
