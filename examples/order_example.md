# 文献調査を依頼する order の例

`.orders/order_{n:03}.md` に記述する例である．項目が無い場合は `survey.md` の Phase 0 の既定値が用いられる．

## 例1: テーマのみを指定する

```text
@.ai/ai-survey-kit/README.md に従い，Information Bottleneck を用いた表現学習の文献調査を行ってください．
```

## 例2: 目的・起点・規模を指定する

```text
@.ai/ai-survey-kit/README.md に従い，Deep Variational Information Bottleneck の文献調査を行ってください．
* 目的: 論文の関連研究の章を書くため．特に，分類以外への応用と，β の決め方を調べたい
* 起点: arXiv:1612.00410（Deep VIB）
* 期間: 2015年以降
* 規模: 採録20件程度．全文を読むのは5件程度
```

## 例3: 論文の関連研究の章まで作成する

```text
@.ai/ai-survey-kit/README.md に従い，survey/survey_report.md をもとに paper/sections/02_related_work.tex を作成してください．
* 参考文献は surveykit.py bib --format bibitem で本文の引用順に出力し，main.tex の thebibliography を置き換える
* 最後に surveykit.py cite-check paper/ を実行して問題が無いことを確認する
```

## 例4: 既存の調査に追加する

```text
既存の文献調査に，2024年以降の Information Bottleneck と大規模言語モデルに関する研究を追加してください．
新しく採録した文献を survey_report.md の該当する分類に追記してください．
```

## 例5: 研究のアイデアを出す

```text
@.ai/ai-survey-kit/README.md に従い，これまでの文献調査と .reports/ の実験結果から，次の研究の候補を考えてください．
* surveykit.py ideas で材料を集め，ideation.md の観点のうち少なくとも4種類から候補を出す
* 各候補の最小の検証実験は，既存の programs/ex003 を少し変えるだけで試せる形にする
```
