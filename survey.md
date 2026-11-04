# 文献調査に関する規約
本ファイルは，AIエディタが文献調査を行い，調査報告・関連研究の章・参考文献リストを作成する際の手順と規約を定める．
文章の表記（句読点，略語，数式）は `@.ai/ai-dev-kit/documentation.md` に従う．
改善の候補（次の研究のアイデア）を考える際は `@.ai/ai-survey-kit/ideation.md` を必ず併せて読むこと．

以下，`surveykit.py` は `python .ai/ai-survey-kit/scripts/surveykit.py` を指す．

---

# 0. 最重要の原則：文献を捏造しない
AIエディタは，学習した知識から文献の書誌情報（著者，年，タイトル，掲載先，DOI）や内容を書いてはならない．
記憶に基づく書誌情報は，存在しない文献や誤った著者・年を含む危険が高いためである．

- **書誌情報は API から取得する**：文献は `surveykit.py search`，`add`，`snowball` で OpenAlex，arXiv，Crossref から取得し，`survey/library.json` に登録する．`library.json` を手作業で編集しない．
- **引用はライブラリの引用キーで行う**：Markdown では `[@key]`，TeX では `\cite{key}` とし，引用してよいのは `status` が `included` の文献のみとする．
- **内容は読んだ範囲のみ書く**：ノートの `read_level` に，要旨のみを読んだ（`abstract`）か全文を読んだ（`full_text`）かを正直に記録する．要旨のみの文献について，要旨に書かれていない手法の詳細や数値を書かない．
- **数値には出典を付ける**：文献の実験結果の数値を書く場合は，原文の表・図の番号を併記する（例: Table 2）．
- **検証で確認する**：作業の終了前に `surveykit.py check` と `surveykit.py cite-check` を実行し，エラーを0件にする．

WebSearch・WebFetch 等で文献を見つけた場合も，必ず `surveykit.py add --doi`／`--arxiv`／`--title` でライブラリに登録してから扱う．

---

# 1. 作業手順
AIエディタは以下の手順を順に実施する．各Phaseの終了時に進捗を報告する．

| Phase | 内容 | 成果物 |
| :--- | :--- | :--- |
| 0 | orderの解釈 | `.orders/order_{n:03}.md` への解釈の追記 |
| 1 | 調査計画 | `survey/plan.md` |
| 2 | 収集 | `survey/library.json`，`survey/search_log.json` |
| 3 | スクリーニング | 各文献の `status` と除外の理由 |
| 4 | 精読とノート | `survey/notes/{key}.md`，`survey/ours.md`，`survey/comparison.md` |
| 5 | 統合と執筆 | `survey/feature_matrix.md`，`survey/idea_sources.md`，`survey/survey_report.md`（必要に応じて関連研究の章，参考文献リスト） |
| 6 | 検証と報告 | `check`・`cite-check` の合格，`.reports/report_{n:03}.md` |

## Phase 0: orderの解釈
orderから以下を読み取る．記載が無い項目は既定値を用い，採用した値をorderファイルに「解釈」として追記する．

| 項目 | 既定値 |
| :--- | :--- |
| 調査のテーマ | （必須．無い場合はユーザーに確認する） |
| 調査の目的 | 関連研究の把握と，本研究の位置づけの明確化 |
| 期間 | 制限なし（ただし直近5年の文献を重点的に調べる） |
| 採録する文献数 | 15〜30件 |
| 全文を読む文献数 | 採録した文献のうち，本研究に直接関係する5〜10件 |
| 出力 | `survey/survey_report.md`．論文の関連研究の章や参考文献リストは，orderで指示された場合のみ作成する |

## Phase 1: 調査計画
1. `surveykit.py init --theme "..."` で `survey/` を作成する（既にある場合は実行しない）．
2. `survey/plan.md` に，調査の目的，調査で答える問い（RQ），検索語，採録・除外の基準，起点となる文献を記入する．
3. 検索語は英語で書き，同義語・略語・上位概念を含める（例: "variational information bottleneck"，"VIB"，"information bottleneck deep learning"）．
4. 手法の立ち位置を示す特徴（特徴行列の列）を3〜8個考え，`surveykit.py feature add {id} --name ... --definition ...` で定義する．特徴の選び方は「3. 特徴行列と改善の候補」に従う．この時点の特徴は仮のものでよく，スクリーニング・精読の結果に応じて見直す．

## Phase 2: 収集
1. **キーワード検索**：検索語ごとに `surveykit.py search "..."` を実行する．被引用数の多い基礎文献を拾うために `--sort cited` も併用し，最新の研究を拾うために `--from-year` や `--source arxiv --sort date` も用いる．
2. **起点の登録**：ユーザーが指定した文献や既知の重要な文献は `surveykit.py add --doi/--arxiv/--title` で登録する．
3. **スノーボーリング**：起点の文献と，Phase 3 で採録した主要な文献について `surveykit.py snowball {key}` を実行し，参考文献（過去の研究）と被引用文献（後続の研究）を候補に加える．
4. **終了の判断**：新しい検索・スノーボーリングで追加される関連する文献が少なくなった（目安として取得件数の1割未満）時点で収集を終える．
5. 検索の記録は `search_log.json` に自動で保存される．報告で検索の方法を説明する根拠とする．

## Phase 3: スクリーニング
1. `surveykit.py list --status candidate` と `surveykit.py show {key}` でタイトルと要旨を確認し，`plan.md` の基準に従って判定する．
2. 採録する文献は `surveykit.py set {key} --status included`，除外する文献は `surveykit.py set {key} --status excluded --reason "..."` とする．除外の理由は必須である．
3. 判定に迷う文献は `--tag maybe` を付けて候補のまま残し，精読の段階で判断する．
4. 必要に応じて分類のタグ（例: `--tag theory`，`--tag application`）を付ける．

## Phase 4: 精読とノート
1. 採録した文献ごとに `surveykit.py note {key}` でノートを作成し，`TODO(AI)` を埋める．
2. 全文を読む文献は，`pdf_url`（arXiv 等のオープンアクセス版）から PDF を `survey/pdfs/` にダウンロードして読む．`survey/pdfs/` は Git の管理対象外である．
3. 全文を入手できない文献は要旨のみに基づいてノートを書き，`read_level: abstract` とする．
4. フロントマター（`task`，`method`，`dataset`，`metric`，`result`，`code`）は比較表の列になるため，短く，文献間で表記を揃えて書く．
5. 特徴の欄（`f.{特徴ID}`）には，`yes` / `partial` / `no` / `unknown` と根拠（節・表の番号．要旨のみの場合は `abstract`）を書く（例: `f.closed_form: yes (Sec. 3.2)`）．文献から判断できない場合は推測せず `unknown` とする．
6. 特徴を途中で追加した場合，既存のノートには未記入の行が自動で追加されるため，全ての採録文献について記入し直す．
7. `surveykit.py ours` で `survey/ours.md` を作成し，本研究が各特徴を満たすかを記入する．
8. ノートの「前提としている仮定」と「読んで浮かんだ疑問」は，改善の候補の材料になるため，要旨のみを読んだ文献でも必ず書く．
9. ノートを書き終えたら `surveykit.py table` で `survey/comparison.md` を更新する．

## Phase 5: 統合と執筆
1. `surveykit.py matrix` で `survey/feature_matrix.md` を作成し，既存手法の充足状況と空白を確認する．
2. `surveykit.py ideas` で `survey/idea_sources.md` を作成し，アイデアの材料（特徴行列の空白，ノートの前提・限界・疑問，`.reports/` の予想と異なった結果，`survey/memo.md`）を確認する．
3. `survey/survey_report.md` の `TODO(AI)` を埋める．執筆の方法は「2. 調査報告の書き方」，特徴行列による立ち位置は「3. 特徴行列と改善の候補」，改善の候補は `ideation.md` に従う．
4. 論文の関連研究の章を作成する場合は，ai-tex-kit の規約に従って `\cite{key}` で引用し，「4. 論文の参考文献」に従って参考文献リストを作成する．

## Phase 6: 検証と報告
1. `surveykit.py check` を実行し，エラーを0件にする．警告は内容を確認し，対応しない場合は報告にその理由を書く．
2. 論文を作成した場合は `surveykit.py cite-check paper/` を実行し，問題を0件にする．
3. `.reports/report_{n:03}.md` に，調査の集計（`check` の出力），主要な知見，未解決の課題，次に検討すべき事項を書く．

---

# 2. 調査報告の書き方
- **羅列しない**：「Aは〜を提案した．Bは〜を提案した．」と文献を1件ずつ並べるのではなく，分類の軸を立て，研究同士の関係（どの研究がどの問題を解決し，何が残ったか）が分かるように書く．
- **分類を示す**：アプローチの違い（例: 目的関数の違い，推定方法の違い）で文献を分類し，分類の関係を mermaid の図で示す．
- **公平に比較する**：数値を比較する場合は，データセット，評価指標，実験条件が同じかどうかを明記する．条件の異なる数値を並べて優劣を論じない．
- **事実と解釈を分ける**：文献に書かれている事実と，AIエディタの解釈・推測を区別する．推測には「〜と考えられる」等の表現を用い，根拠を示す．
- **本研究の位置づけを示す**：未解決の課題と本研究の関係を，根拠となる文献とともに書く．
- **引用の形式**：Markdown では `[@key]`，複数の場合は `[@key1; @key2]` と書く．

---

# 3. 特徴行列と改善の候補
特徴行列は，行を手法，列を特徴（比較の観点）とし，各セルに ✓（満たす）・△（部分的に満たす）・✗（満たさない）・?（判断できない）を示す表である．
既存手法の立ち位置と空白，および本研究の立ち位置を一目で示すために作成する．行と列は `--transpose` で入れ替えられる．

## 3.1 特徴の選び方
- **判断できる定義にする**：「性能が高い」のような程度の表現ではなく，「目的関数が閉形式で計算できる」「ラベルなしデータで学習できる」のように，文献を読めば満たす・満たさないを判断できる定義にする．
- **手法を区別する**：全ての手法が同じ値になる特徴は立ち位置の説明に役立たない（`check` が警告する）．
- **問いに関係する**：各特徴が `plan.md` のどの RQ に関係するかを書く．
- **本研究に有利な特徴だけを選ばない**：本研究が満たさない特徴も含め，読者が公平に比較できる表にする．
- **数**：3〜8個を目安とする．多すぎる場合は，関係の深い特徴をまとめるか，`--kind text` の列（例: 対象のデータ，計算量のオーダー）にする．

## 3.2 記入の規約
- 既存手法の各セルには根拠（節・表の番号，または `abstract`）を付ける．根拠の無い `yes` / `partial` / `no` は `check` がエラーにする．
- 判断できない場合は推測せず `unknown`（?）とする．
- 本研究の行（`ours.md`）は計画・主張であり，検証済みの事実と区別して報告する．

## 3.3 特徴行列の作成
```bash
python .ai/ai-survey-kit/scripts/surveykit.py matrix                       # survey/feature_matrix.md（行: 手法，列: 特徴）
python .ai/ai-survey-kit/scripts/surveykit.py matrix --transpose           # 行: 特徴，列: 手法
python .ai/ai-survey-kit/scripts/surveykit.py matrix --format tex --out paper/sections/feature_matrix.tex   # 論文用の表
```

`feature_matrix.md` には，特徴の定義，行列，既存手法の充足状況（各特徴を満たす既存手法の数），空白（満たす既存手法が無い特徴，両立する既存手法が無い特徴の組）が出力される．
`feature_matrix.md` は直接編集せず，ノート・`ours.md`・`features.json` を修正して再生成する．
論文用の表は `\checkmark` を用いるため，プリアンブルで `amssymb` を読み込む．

## 3.4 改善の候補
特徴行列の空白は改善の候補の材料の一つにすぎない．改善の候補は `ideation.md` に従い，前提・極端化・評価・実験の違和感等を含む少なくとも3種類の観点から考える．

---

# 4. 論文の参考文献
ai-tex-kit の論文テンプレートは `thebibliography` 環境と短い形式の `\bibitem`（例: `A. Groshev, et al., IEEE Access, 2022.`）を用いる．

1. 本文では `\cite{key}` でライブラリの引用キーを用いる．
2. 本文で引用した文献のみを，引用順に `\bibitem` の形式で出力する．

```bash
python .ai/ai-survey-kit/scripts/surveykit.py bib --format bibitem --cited-in paper/sections --out paper/references.tex
```

3. 出力した `thebibliography` 環境で `main.tex` の既存の `thebibliography` 環境を置き換える（`\input{references}` としてもよい）．
4. BibTeX を用いる場合は `--format bibtex` で `.bib` を出力する．
5. `surveykit.py cite-check paper/` を実行し，未定義の引用，ライブラリに無い文献，採録していない文献の引用，引用されていない定義が無いことを確認する．

---

# 5. API の利用
| API | 用途 | 備考 |
| :--- | :--- | :--- |
| OpenAlex | 検索，DOI による取得，スノーボーリング | キー不要 |
| arXiv | プレプリントの検索，arXiv ID による取得 | キー不要．連続して大量に呼び出さない |
| Crossref | OpenAlex に無い DOI の取得 | キー不要 |

- 接続を確認するには `surveykit.py doctor` を実行する．接続できない場合は，ネットワークの設定をユーザーに確認する．API に接続できない状態で，記憶に基づいて文献を補ってはならない．
- 環境変数 `SURVEYKIT_MAILTO` が設定されている場合，OpenAlex・Crossref の polite pool（安定した応答の得られる接続）を利用する．AIエディタはユーザーのメールアドレスを自ら設定しない．
- API の応答のメタデータに誤りがある場合（例: 著者名の表記，掲載先の欠落）は，ノートにその旨を記録し，ユーザーに報告する．`library.json` を直接書き換えない．

---

# 6. ディレクトリ構成
```text
survey/
├── plan.md               # 調査計画
├── library.json          # 文献ライブラリ（surveykit.py のみが編集する）
├── search_log.json       # 検索・スノーボーリングの記録
├── notes/
│   └── {key}.md          # 文献ごとのノート
├── features.json         # 特徴行列の特徴の定義（surveykit.py feature add で追加）
├── ours.md               # 本研究の特徴（surveykit.py ours で作成）
├── comparison.md         # 採録文献の比較表（surveykit.py table が生成）
├── feature_matrix.md     # 特徴行列（surveykit.py matrix が生成）
├── idea_sources.md       # アイデアの材料（surveykit.py ideas が生成）
├── memo.md               # 研究の種になる気づき（surveykit.py memo で追記）
├── survey_report.md      # 調査報告
├── references.bib        # 参考文献（必要に応じて生成）
└── pdfs/                 # 全文のPDF（Git の管理対象外）
```

---

# 7. ai-agent との連携
`survey_report.md` の「6. 未解決の課題と本研究の位置づけ」「7. 改善の候補」「8. 次に検討すべき事項」は，`.ai/ai-agent/` の research エージェントが次の order を検討する際の手がかりとなる．
文献に基づく事実と推測を区別し，次の実験・開発・執筆の候補として具体的に書く．
