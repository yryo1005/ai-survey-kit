"""surveykit.py のテスト．

API に接続しない処理（ライブラリ，ノート，比較表，参考文献，引用の検証，応答の変換）を検証する．
API への実際の接続は `surveykit.py doctor` で確認する．

実行方法（ai-survey-kit のルートで）:
    python -m pytest tests/
"""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import surveykit as sk  # noqa: E402


def entry(title, authors, year, **fields):
    """テスト用の文献（APIから取得した扱い）を作る．"""
    return {"title": title, "authors": authors, "year": year, "sources": ["openalex"], **fields}


@pytest.fixture
def project(tmp_path):
    sk.command_init(tmp_path, "テスト用の調査")
    library = sk.Library(tmp_path)
    library.add(entry("Deep Variational Information Bottleneck", ["Alexander A. Alemi", "Ian Fischer", "Joshua V. Dillon", "Kevin Murphy"],
                      2016, arxiv="1612.00410", doi="10.48550/arxiv.1612.00410", venue="arXiv", type="preprint",
                      abstract="We present a variational approximation."), "search:openalex:vib")
    library.add(entry("The Information Bottleneck Method", ["Naftali Tishby", "Fernando C. Pereira"], 2000,
                      venue="Proceedings of the Allerton Conference", type="proceedings-article"), "search:openalex:ib")
    library.add(entry("Learning & Compression: A Study", ["Ana Müller"], 2021, venue="Journal of ML_Research",
                      doi="10.1000/xyz", type="journal-article"), "search:openalex:ib")
    library.save()
    return tmp_path


def test_keys_and_duplicates(project):
    library = sk.Library(project)
    assert set(library.entries) == {"alemi2016deep", "tishby2000information", "muller2021learning"}

    # DOI の表記違い・タイトルの大文字小文字違いは同じ文献として統合される
    key, is_new = library.add({"title": "deep variational information bottleneck.", "authors": [], "year": 2016,
                               "cited_by": 1000, "sources": ["arxiv"]}, "search:arxiv:vib")
    assert (key, is_new) == ("alemi2016deep", False)
    assert library.entries[key]["cited_by"] == 1000
    assert library.entries[key]["sources"] == ["openalex", "arxiv"]
    assert library.entries[key]["queries"] == ["search:openalex:vib", "search:arxiv:vib"]

    # 同じ引用キーになる別の文献には接尾辞が付く
    key, is_new = library.add(entry("Deep Learning Again", ["Bob Alemi"], 2016))
    assert (key, is_new) == ("alemi2016deepa", True)


def test_normalizers():
    assert sk.normalize_doi("https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert sk.normalize_arxiv("https://arxiv.org/abs/1612.00410v3") == "1612.00410"
    assert sk.normalize_arxiv("arXiv:hep-th/9901001") == "hep-th/9901001"
    assert sk.normalize_arxiv("not an id") is None


def test_openalex_conversion():
    work = {
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.48550/arXiv.1612.00410",
        "title": "Deep Variational Information Bottleneck", "publication_year": 2016,
        "authorships": [{"author": {"display_name": "Alexander A. Alemi"}}],
        "primary_location": {"source": {"display_name": "arXiv"}, "landing_page_url": "https://arxiv.org/abs/1612.00410"},
        "abstract_inverted_index": {"We": [0], "present": [1], "approximation": [3], "a": [2]},
        "cited_by_count": 10, "type": "preprint", "referenced_works": ["https://openalex.org/W2"],
    }
    converted = sk.openalex_to_entry(work)
    assert converted["openalex"] == "W1" and converted["arxiv"] == "1612.00410"
    assert converted["abstract"] == "We present a approximation"
    assert converted["referenced_works"] == ["W2"]


def test_arxiv_conversion():
    xml = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>http://arxiv.org/abs/1612.00410v7</id><published>2016-12-01T00:00:00Z</published>
      <title>Deep Variational
        Information Bottleneck</title><summary>Abstract text.</summary>
      <author><name>Alexander A. Alemi</name></author></entry></feed>"""
    node = ET.fromstring(xml).find("a:entry", sk.ATOM)
    converted = sk.arxiv_to_entry(node)
    assert converted["title"] == "Deep Variational Information Bottleneck"
    assert converted["arxiv"] == "1612.00410" and converted["year"] == 2016
    assert converted["doi"] == "10.48550/arxiv.1612.00410"


def test_bib_formats(project):
    library = sk.Library(project)
    bibtex = sk.bibtex_entry("muller2021learning", library.entries["muller2021learning"])
    assert bibtex.startswith("@article{muller2021learning,")
    assert r"Learning \& Compression" in bibtex and r"Journal of ML\_Research" in bibtex
    assert sk.bibtex_entry("alemi2016deep", library.entries["alemi2016deep"]).startswith("@misc{")
    assert sk.bibtex_entry("tishby2000information", library.entries["tishby2000information"]).startswith("@inproceedings{")

    item = sk.bibitem_entry("alemi2016deep", library.entries["alemi2016deep"])
    assert "A. A. Alemi, et al., arXiv:1612.00410, 2016." in item
    item = sk.bibitem_entry("tishby2000information", library.entries["tishby2000information"])
    assert "N. Tishby, F. C. Pereira, Proceedings of the Allerton Conference, 2000." in item


def test_bib_cited_in_order(project, capsys):
    paper = project / "paper"
    paper.mkdir()
    (paper / "intro.tex").write_text(r"既存研究\cite{tishby2000information}と\citep[p.3]{alemi2016deep, tishby2000information}．% \cite{muller2021learning}")
    text = sk.command_bib(project, fmt="bibitem", cited_in=[paper])
    assert text.index("tishby2000information") < text.index("alemi2016deep")
    assert "muller2021learning" not in text            # コメント内の引用は対象外
    with pytest.raises(sk.SurveyKitError):
        (paper / "x.tex").write_text(r"\cite{unknown2020}")
        sk.command_bib(project, fmt="bibitem", cited_in=[paper])


def test_cite_check(project):
    sk.command_set(project, ["alemi2016deep", "tishby2000information"], status="included")
    library = sk.Library(project)
    library.add({"title": "Manual Paper", "authors": ["X Y"], "year": 2020, "sources": ["manual"]})
    library.entries["y2020manual"]["status"] = "included"
    library.save()

    paper = project / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text(
        r"\cite{alemi2016deep,ghost2023,muller2021learning,y2020manual}" "\n"
        r"\begin{thebibliography}{9}\bibitem{alemi2016deep} A.\bibitem{tishby2000information} B.\end{thebibliography}")
    issues = "\n".join(sk.collect_cite_issues(project, [paper]))
    assert "ghost2023" in issues and "ライブラリに無い" in issues
    assert "muller2021learning: 採録（included）されていない" in issues
    assert "y2020manual: API で検証されていない" in issues
    assert "muller2021learning: 参考文献リスト" in issues          # 未定義
    assert "tishby2000information: 参考文献リストに定義されていますが" in issues   # 未使用


def candidate(title, lens, evidence="[@alemi2016deep] の Table 2", drop=None):
    """改善の候補の小節を作る．"""
    fields = {"観点": lens, "内容": "x", "根拠": evidence, "何が新しいか": "x", "なぜ重要か": "x",
              "成功の基準": "x", "最小の検証実験": "x", "捨てる条件": "x", "難易度・リスク": "低"}
    if drop:
        fields.pop(drop)
    return f"### {title}\n\n" + "\n".join(f"- {k}: {v}" for k, v in fields.items()) + "\n\n"


VALID_IDEAS = ("## 7. 改善の候補\n\n| # | 候補 |\n|:---:|:---|\n| 1 | A |\n\n"
               + candidate("候補1: A", "空白", "特徴行列の閉形式の列")
               + candidate("候補2: B", "前提を壊す")
               + candidate("候補3: C", "実験の違和感", "report_003 の 4.2")
               + "## 8. 次に検討すべき事項\n\nx\n")


def test_notes_table_and_check(project):
    sk.command_set(project, ["alemi2016deep", "tishby2000information"], status="included")
    sk.command_set(project, ["muller2021learning"], status="excluded", reason="対象外の領域")
    for key in ("alemi2016deep", "tishby2000information"):
        path = sk.command_note(project, key)
        assert "We present a variational approximation." in path.read_text() or key != "alemi2016deep"

    errors, _ = sk.collect_issues(project)
    assert any("TODO(AI)" in e for e in errors)
    assert any("comparison.md がありません" in e for e in errors)

    # ノート・計画・報告を記入した状態にする
    for key in ("alemi2016deep", "tishby2000information"):
        path = sk.note_path(project, key)
        text = path.read_text().replace("TODO(AI) abstract または full_text", "abstract").replace("TODO(AI)", "記入済み")
        path.write_text(text)
    survey = sk.survey_dir(project)
    (survey / "plan.md").write_text((survey / "plan.md").read_text().replace("TODO(AI)", "記入済み"))
    (survey / "survey_report.md").write_text("# 報告\n\n基礎となる研究 [@tishby2000information] と深層化 [@alemi2016deep; @tishby2000information]．\n\n" + VALID_IDEAS)
    sk.command_ideas(project)
    sk.command_table(project)
    table = (survey / "comparison.md").read_text()
    assert table.index("tishby2000information") < table.index("alemi2016deep")   # 年の順

    errors, warnings = sk.collect_issues(project)
    assert errors == [], errors
    stats = sk.survey_stats(project, sk.Library(project))
    assert stats["included"] == 2 and stats["excluded"] == 1 and stats["unique"] == 3

    (survey / "survey_report.md").write_text("[@muller2021learning]\n")
    errors, _ = sk.collect_issues(project)
    assert any("muller2021learning" in e for e in errors)


def test_exclusion_requires_reason(project):
    with pytest.raises(sk.SurveyKitError):
        sk.command_set(project, ["muller2021learning"], status="excluded")


def test_library_file_is_sorted_json(project):
    data = json.loads((sk.survey_dir(project) / "library.json").read_text())
    assert list(data) == sorted(data)


def fill_note(root, key, name, features):
    """ノートの TODO(AI) を埋め，特徴の値を設定する．"""
    path = sk.note_path(root, key)
    text = path.read_text()
    for feature_id, value in features.items():
        text = __import__("re").sub(rf"^f\.{feature_id}: .*$", f"f.{feature_id}: {value}", text, flags=__import__("re").M)
    text = text.replace("name: TODO(AI) 特徴行列の行に表示する手法の略称（例: Deep VIB）", f"name: {name}")
    text = text.replace("TODO(AI) abstract または full_text", "abstract").replace("TODO(AI)", "記入済み")
    path.write_text(text)


def test_feature_matrix(project):
    sk.command_set(project, ["alemi2016deep", "tishby2000information", "muller2021learning"], status="included")
    sk.command_feature_add(project, "deep", "深層モデル", "深層ニューラルネットワークで表現を学習する")
    sk.command_feature_add(project, "closed_form", "閉形式", "目的関数を閉形式で計算できる")
    sk.command_feature_add(project, "theory", "理論", "情報理論に基づく定式化を持つ")
    for key in ("alemi2016deep", "tishby2000information", "muller2021learning"):
        sk.command_note(project, key)
    assert "f.closed_form: TODO(AI)" in sk.note_path(project, "alemi2016deep").read_text()

    fill_note(project, "alemi2016deep", "Deep VIB", {"deep": "yes (Sec. 3)", "closed_form": "no (abstract)", "theory": "yes (Sec. 2)"})
    fill_note(project, "tishby2000information", "IB", {"deep": "no (Sec. 1)", "closed_form": "yes (Sec. 4)", "theory": "yes (Sec. 2)"})
    fill_note(project, "muller2021learning", "LC", {"deep": "partial (abstract)", "closed_form": "unknown", "theory": "yes (abstract)"})

    # 特徴を後から追加すると，既存のノートと ours.md に未記入の行が追加される
    sk.command_ours(project, "本研究")
    sk.command_feature_add(project, "data", "データ", "実験に用いたデータ", kind="text")
    assert "f.data: TODO(AI)" in sk.note_path(project, "alemi2016deep").read_text()
    assert "f.data: TODO(AI)" in sk.ours_path(project).read_text()
    fill_note(project, "alemi2016deep", "Deep VIB", {"data": "MNIST (Table 1)"})
    fill_note(project, "tishby2000information", "IB", {"data": "− (Sec. 5)"})
    fill_note(project, "muller2021learning", "LC", {"data": "CIFAR-10 (abstract)"})
    ours = sk.ours_path(project)
    text = ours.read_text().replace("TODO(AI)", "記入済み")
    for line in ("f.deep: yes", "f.closed_form: yes", "f.theory: no", "f.data: MNIST"):
        text = __import__("re").sub(rf"^{line.split(':')[0]}: .*$", line, text, flags=__import__("re").M)
    ours.write_text(text)

    md = sk.command_matrix(project)
    assert "| Deep VIB [@alemi2016deep] | 2016 | ✓ | ✗ | ✓ | MNIST |" in md
    assert "| IB [@tishby2000information] | 2000 | ✗ | ✓ | ✓ | − |" in md
    assert "| LC [@muller2021learning] | 2021 | △ | ? | ✓ | CIFAR-10 |" in md
    assert "| **本研究** | − | ✓ | ✓ | ✗ | MNIST |" in md
    assert "「深層モデル」と「閉形式」を両立する既存手法がない（本研究が両立する）．" in md
    assert "| 閉形式 | 1 / 3 | ✓ |" in md

    transposed = sk.matrix_markdown(project, sk.Library(project), sk.load_features(project), transpose=True)
    assert "| 閉形式 | ✓ | ✗ | ? | ✓ |" in transposed   # 列は年の順（IB, Deep VIB, LC, 本研究）

    tex = sk.command_matrix(project, fmt="tex")
    assert r"Deep VIB~\cite{alemi2016deep} & $\checkmark$ & $\times$ & $\checkmark$ & MNIST \\" in tex
    assert r"\caption{既存手法と本研究の比較}" in tex

    errors, warnings = sk.collect_feature_issues(project, sk.Library(project), ["alemi2016deep", "tishby2000information", "muller2021learning"])
    assert errors == [], errors
    assert any("「理論」は既存手法の全てが同じ値" in w for w in warnings)

    # 根拠の無い値と，不正な値はエラーになる
    fill_note(project, "muller2021learning", "LC", {"closed_form": "yes", "deep": "maybe (Sec. 1)"})
    errors, _ = sk.collect_feature_issues(project, sk.Library(project), ["muller2021learning"])
    assert any("f.closed_form に根拠" in e for e in errors)
    assert any("f.deep は yes / partial / no / unknown" in e for e in errors)
    assert any("feature_matrix.md がノートと一致しません" in w for w in sk.collect_feature_issues(project, sk.Library(project), [])[1])


def test_feature_id_validation(project):
    with pytest.raises(sk.SurveyKitError):
        sk.command_feature_add(project, "Bad-ID", "x", "y")
    sk.command_feature_add(project, "ok_id", "x", "y")
    with pytest.raises(sk.SurveyKitError):
        sk.command_feature_add(project, "ok_id", "x", "y")


def test_idea_candidates_check(project):
    survey = sk.survey_dir(project)
    report = survey / "survey_report.md"
    report.write_text(VALID_IDEAS)
    assert len(sk.parse_idea_candidates(VALID_IDEAS)) == 3
    errors, warnings = sk.collect_idea_issues(project)
    assert errors == []
    assert any("idea_sources.md がありません" in w for w in warnings)

    bad = ("## 7. 改善の候補\n\n" + candidate("候補1: A", "空白", "特徴行列")
           + candidate("候補2: B", "なんとなく") + candidate("候補3: C", "空白", "直感", drop="捨てる条件") + "## 8. 次\n")
    report.write_text(bad)
    errors, warnings = sk.collect_idea_issues(project)
    assert any("観点が不正" in e and "なんとなく" in e for e in errors)
    assert any("候補3: C" in e and "捨てる条件" in e for e in errors)
    assert any("根拠に，文献" in w and "候補3: C" in w for w in warnings)
    assert any("観点が 1 種類" in w for w in warnings)

    report.write_text("# 報告\n")
    errors, _ = sk.collect_idea_issues(project)
    assert any("改善の候補」の節がありません" in e for e in errors)


def test_ideas_collects_material(project):
    sk.command_set(project, ["alemi2016deep"], status="included")
    sk.command_note(project, "alemi2016deep")
    path = sk.note_path(project, "alemi2016deep")
    text = path.read_text()
    text = text.replace(text[text.index("## 6. 前提としている仮定"):],
                        "## 6. 前提としている仮定\n\n- データは独立同分布である\n\n## 7. 読んで浮かんだ疑問\n\n- β を無限大にしたらどうなるか\n")
    path.write_text(text)

    reports = project / ".reports"
    reports.mkdir()
    (reports / "report_003.md").write_text("# レポート\n\n## 結果\n\n良好．\n\n## 予想と異なった結果\n\nβ=1e-4 で精度が逆転した．\n\n## まとめ\n\nx\n")
    sk.command_memo(project, "ゼミで β の決め方を質問された", source="ゼミ", lens="対話")
    with pytest.raises(sk.SurveyKitError):
        sk.command_memo(project, "x", lens="不明な観点")

    text = sk.command_ideas(project)
    assert "データは独立同分布である" in text and "### [@alemi2016deep]" in text
    assert "β を無限大にしたらどうなるか" in text
    assert "report_003.md: 予想と異なった結果" in text and "β=1e-4 で精度が逆転した．" in text
    assert "良好" not in text                           # 該当しない節は集めない
    assert "[対話] （ゼミ） ゼミで β の決め方を質問された" in text
    assert "### 極端化・単純化" in text
    assert (sk.survey_dir(project) / "idea_sources.md").exists()
