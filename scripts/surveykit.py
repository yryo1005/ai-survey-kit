#!/usr/bin/env python3
"""文献調査を行い，検証済みの文献ライブラリと参考文献リストを作成するためのCLI．

プロジェクトルートで実行する．標準ライブラリのみで動作する．
文献のメタデータは OpenAlex，arXiv，Crossref の API から取得し，`survey/library.json` に保存する．
AIエディタが記憶に基づいて文献の情報を書くことを防ぐため，引用してよいのはこのライブラリに
API から登録された文献のみとし，`check` と `cite-check` で機械的に検証する．

    python .ai/ai-survey-kit/scripts/surveykit.py init --theme "Information Bottleneck による表現学習"
    python .ai/ai-survey-kit/scripts/surveykit.py search "variational information bottleneck" --from-year 2016
    python .ai/ai-survey-kit/scripts/surveykit.py add --doi 10.48550/arXiv.1612.00410
    python .ai/ai-survey-kit/scripts/surveykit.py snowball alemi2016deep --direction both
    python .ai/ai-survey-kit/scripts/surveykit.py set alemi2016deep --status included
    python .ai/ai-survey-kit/scripts/surveykit.py note alemi2016deep
    python .ai/ai-survey-kit/scripts/surveykit.py table
    python .ai/ai-survey-kit/scripts/surveykit.py bib --format bibtex --out survey/references.bib
    python .ai/ai-survey-kit/scripts/surveykit.py cite-check paper/ survey/survey_report.md
    python .ai/ai-survey-kit/scripts/surveykit.py check
"""

import argparse
import datetime
import difflib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = KIT_DIR / "templates"
SURVEY_DIR_NAME = "survey"
TODO_MARK = "TODO(AI)"
USER_AGENT = "ai-survey-kit/0.1"
VERIFIED_SOURCES = {"openalex", "arxiv", "crossref"}
STATUSES = ["candidate", "included", "excluded"]
NOTE_FIELDS = ["task", "method", "dataset", "metric", "result", "code"]
READ_LEVELS = ["abstract", "full_text"]
TABLE_MARKERS = ("<!-- surveykit:table:start -->", "<!-- surveykit:table:end -->")
TITLE_STOPWORDS = {"a", "an", "the", "on", "of", "for", "in", "to", "and", "with", "towards", "toward",
                   "via", "by", "from", "is", "are", "do", "does", "how", "what", "why", "when", "at", "as"}

CITE_TEX_PATTERN = re.compile(r"\\(?:no)?cite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^}]*)\}")
BIBITEM_PATTERN = re.compile(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}")
BIB_ENTRY_PATTERN = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")
CITE_MD_PATTERN = re.compile(r"\[(@[^\]]+)\]")
MD_KEY_PATTERN = re.compile(r"@([A-Za-z0-9_:\-]+)")
ARXIV_ID_PATTERN = re.compile(r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.IGNORECASE)


class SurveyKitError(Exception):
    """利用者に表示するエラー．"""


# =====================================================================================
# 共通
# =====================================================================================

def now():
    """現在時刻を文字列で返す．

    Returns:
        str: "YYYY-MM-DD HH:MM:SS" 形式の現在時刻．
    """
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def survey_dir(root):
    """調査ディレクトリのパスを返す．

    Args:
        root (Path): プロジェクトルート．

    Returns:
        Path: `{root}/survey`．
    """
    return root / SURVEY_DIR_NAME


def read_json(path, default):
    """JSONファイルを読み込む．存在しない場合は default を返す．

    Args:
        path (Path): JSONファイルのパス．
        default (Any): ファイルが無い場合の値．

    Returns:
        Any: 読み込んだデータ．
    """
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path, text):
    """テキストファイルを書き込む（改行はLFに統一する）．

    Args:
        path (Path): 保存先．
        text (str): 内容．

    Returns:
        None
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path, data):
    """データをJSONファイルとして保存する．

    Args:
        path (Path): 保存先．
        data (Any): 保存するデータ．

    Returns:
        None
    """
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def normalize_title(title):
    """重複判定用にタイトルを正規化する（小文字化し，英数字以外を除去する）．

    Args:
        title (str): タイトル．

    Returns:
        str: 正規化したタイトル．
    """
    text = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_doi(doi):
    """DOIを正規化する（URLの接頭辞を除去し，小文字にする）．

    Args:
        doi (str | None): DOI．

    Returns:
        str | None: 正規化したDOI．
    """
    if not doi:
        return None
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.IGNORECASE)
    return doi.lower() or None


def normalize_arxiv(arxiv_id):
    """arXiv ID を正規化する（URLとバージョンを除去する）．

    Args:
        arxiv_id (str | None): arXiv ID または arXiv のURL．

    Returns:
        str | None: 正規化した arXiv ID（例: "1612.00410"）．
    """
    if not arxiv_id:
        return None
    match = ARXIV_ID_PATTERN.search(arxiv_id)
    return match.group(1) if match else None


def ascii_word(text):
    """文字列をASCIIの英小文字・数字のみに変換する．

    Args:
        text (str): 文字列．

    Returns:
        str: 変換後の文字列．
    """
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def family_name(author):
    """著者名から姓を取り出す．

    Args:
        author (str): 著者名（"Given Family" 形式）．

    Returns:
        str: 姓．
    """
    parts = author.replace(",", " ").split()
    return parts[-1] if parts else ""


# =====================================================================================
# 文献ライブラリ
# =====================================================================================

class Library:
    """`survey/library.json` に保存する文献ライブラリ．

    各文献は引用キーをキーとする辞書で，以下のフィールドを持つ．
    title, authors (list[str]), year, venue, doi, arxiv, openalex, url, pdf_url, abstract,
    cited_by, type, sources (list[str]: 取得元のAPI), status (candidate/included/excluded),
    reason, tags (list[str]), queries (list[str]: 見つけた検索・スノーボーリング), added_at

    Args:
        root (Path): プロジェクトルート．
    """

    def __init__(self, root):
        self.root = root
        self.path = survey_dir(root) / "library.json"
        if not self.path.exists():
            raise SurveyKitError("survey/library.json がありません．先に `surveykit.py init` を実行してください．")
        self.entries = read_json(self.path, {})

    def save(self):
        """ライブラリを保存する．

        Returns:
            None
        """
        write_json(self.path, dict(sorted(self.entries.items())))

    def find_duplicate(self, entry):
        """DOI，arXiv ID，OpenAlex ID，タイトルのいずれかが一致する文献の引用キーを返す．

        Args:
            entry (dict): 判定する文献．

        Returns:
            str | None: 一致した文献の引用キー．無い場合は None．
        """
        title = normalize_title(entry.get("title"))
        for key, other in self.entries.items():
            for field in ("doi", "arxiv", "openalex"):
                if entry.get(field) and entry.get(field) == other.get(field):
                    return key
            if title and title == normalize_title(other.get("title")):
                return key
        return None

    def make_key(self, entry):
        """引用キーを作成する．形式は "{第一著者の姓}{年}{タイトルの最初の主要な単語}"．

        Args:
            entry (dict): 文献．

        Returns:
            str: 他の文献と重複しない引用キー（例: "alemi2016deep"）．
        """
        authors = entry.get("authors") or []
        name = ascii_word(family_name(authors[0])) if authors else ""
        words = [ascii_word(word) for word in (entry.get("title") or "").split()]
        word = next((w for w in words if w and w not in TITLE_STOPWORDS), "")
        base = f"{name or 'anon'}{entry.get('year') or 'nd'}{word}"
        key = base
        suffix = ord("a")
        while key in self.entries:
            key = f"{base}{chr(suffix)}"
            suffix += 1
        return key

    def add(self, entry, query=None):
        """文献を追加する．重複する文献がある場合は不足しているフィールドを補って統合する．

        Args:
            entry (dict): 追加する文献．
            query (str | None): 文献を見つけた検索・スノーボーリングの記録．

        Returns:
            tuple[str, bool]: (引用キー, 新規に追加した場合 True)．
        """
        key = self.find_duplicate(entry)
        is_new = key is None
        if is_new:
            key = self.make_key(entry)
            base = {"status": "candidate", "reason": "", "tags": [], "queries": [], "sources": [], "added_at": now()}
            self.entries[key] = {**base, **{k: v for k, v in entry.items() if k != "sources"}}
        target = self.entries[key]
        for field, value in entry.items():
            if field == "sources":
                continue
            if value not in (None, "", []) and target.get(field) in (None, "", []):
                target[field] = value
        for source in entry.get("sources", []):
            if source not in target["sources"]:
                target["sources"].append(source)
        if query and query not in target["queries"]:
            target["queries"].append(query)
        return key, is_new

    def get(self, key):
        """引用キーから文献を取得する．

        Args:
            key (str): 引用キー．

        Returns:
            dict: 文献．

        Raises:
            SurveyKitError: 存在しない場合．
        """
        if key not in self.entries:
            close = difflib.get_close_matches(key, self.entries.keys(), n=3)
            hint = f"（候補: {', '.join(close)}）" if close else ""
            raise SurveyKitError(f"文献 '{key}' はライブラリにありません{hint}．")
        return self.entries[key]


def is_verified(entry):
    """文献のメタデータがAPIから取得されたものかを判定する．

    Args:
        entry (dict): 文献．

    Returns:
        bool: OpenAlex，arXiv，Crossref のいずれかから取得した場合 True．
    """
    return bool(VERIFIED_SOURCES & set(entry.get("sources", [])))


# =====================================================================================
# API
# =====================================================================================

def http_get(url, params=None, accept="application/json", retries=3):
    """HTTP GET を実行する．429・5xx の場合は待機して再試行する．

    Args:
        url (str): URL．
        params (dict | None): クエリパラメータ．
        accept (str): Accept ヘッダ．
        retries (int): 再試行の回数．

    Returns:
        str: レスポンスの本文．

    Raises:
        SurveyKitError: 通信に失敗した場合．
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise SurveyKitError(f"見つかりません（404）: {url}") from error
            if error.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise SurveyKitError(f"HTTP {error.code}: {url}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise SurveyKitError(f"接続できません: {url}（{getattr(error, 'reason', error)}）．"
                                 "ネットワークの設定を確認してください（`surveykit.py doctor`）．") from error
    raise SurveyKitError(f"取得に失敗しました: {url}")


def polite_params(params):
    """OpenAlex・Crossref の polite pool 用に，環境変数 SURVEYKIT_MAILTO があれば mailto を付ける．

    Args:
        params (dict): クエリパラメータ．

    Returns:
        dict: mailto を加えたクエリパラメータ．
    """
    mailto = os.environ.get("SURVEYKIT_MAILTO")
    return {**params, "mailto": mailto} if mailto else params


def openalex_abstract(inverted_index):
    """OpenAlex の abstract_inverted_index から要旨の文章を復元する．

    Args:
        inverted_index (dict[str, list[int]] | None): 単語と出現位置の辞書．

    Returns:
        str: 要旨．無い場合は空文字列．
    """
    if not inverted_index:
        return ""
    positions = [(pos, word) for word, indices in inverted_index.items() for pos in indices]
    return " ".join(word for _, word in sorted(positions))


def openalex_to_entry(work):
    """OpenAlex の work を文献の辞書に変換する．

    Args:
        work (dict): OpenAlex API の work．

    Returns:
        dict: 文献．
    """
    doi = normalize_doi(work.get("doi"))
    arxiv = None
    urls = [work.get("doi") or ""]
    for location in work.get("locations") or []:
        urls += [location.get("landing_page_url") or "", location.get("pdf_url") or ""]
    for url in urls:
        if "arxiv.org" in url.lower() or "10.48550/arxiv" in url.lower():
            arxiv = normalize_arxiv(url)
            if arxiv:
                break

    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    best_oa = work.get("best_oa_location") or {}
    return {
        "title": work.get("title") or work.get("display_name") or "",
        "authors": [a.get("author", {}).get("display_name", "") for a in work.get("authorships") or []],
        "year": work.get("publication_year"),
        "venue": source.get("display_name") or "",
        "doi": doi,
        "arxiv": arxiv,
        "openalex": (work.get("id") or "").rsplit("/", 1)[-1] or None,
        "url": primary.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else ""),
        "pdf_url": best_oa.get("pdf_url") or (f"https://arxiv.org/pdf/{arxiv}" if arxiv else ""),
        "abstract": openalex_abstract(work.get("abstract_inverted_index")),
        "cited_by": work.get("cited_by_count"),
        "type": work.get("type") or "",
        "referenced_works": [w.rsplit("/", 1)[-1] for w in work.get("referenced_works") or []],
        "sources": ["openalex"],
    }


def openalex_search(query, limit=20, from_year=None, to_year=None, sort="relevance"):
    """OpenAlex で文献を検索する．

    Args:
        query (str): 検索語．
        limit (int): 取得する件数（最大200）．
        from_year (int | None): この年以降に出版された文献に限る．
        to_year (int | None): この年以前に出版された文献に限る．
        sort (str): "relevance"（関連度順）または "cited"（被引用数順）．

    Returns:
        list[dict]: 文献のリスト．
    """
    filters = []
    if from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")
    if to_year:
        filters.append(f"to_publication_date:{to_year}-12-31")
    params = {"search": query, "per-page": min(limit, 200), "filter": ",".join(filters) or None,
              "sort": "cited_by_count:desc" if sort == "cited" else None}
    data = json.loads(http_get("https://api.openalex.org/works", polite_params(params)))
    return [openalex_to_entry(work) for work in data.get("results", [])]


def openalex_work(identifier):
    """OpenAlex の work を1件取得する．

    Args:
        identifier (str): OpenAlex ID（例: "W2963000224"）または "doi:10.xxx"．

    Returns:
        dict: 文献．
    """
    path = urllib.parse.quote(identifier, safe=":/")
    return openalex_to_entry(json.loads(http_get(f"https://api.openalex.org/works/{path}", polite_params({}))))


def openalex_citing(openalex_id, limit=20):
    """指定した文献を引用している文献を，被引用数の多い順に取得する．

    Args:
        openalex_id (str): OpenAlex ID．
        limit (int): 取得する件数．

    Returns:
        list[dict]: 文献のリスト．
    """
    params = {"filter": f"cites:{openalex_id}", "per-page": min(limit, 200), "sort": "cited_by_count:desc"}
    data = json.loads(http_get("https://api.openalex.org/works", polite_params(params)))
    return [openalex_to_entry(work) for work in data.get("results", [])]


ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def arxiv_to_entry(node):
    """arXiv API の Atom の entry 要素を文献の辞書に変換する．

    Args:
        node (xml.etree.ElementTree.Element): entry 要素．

    Returns:
        dict: 文献．
    """
    def text(tag):
        element = node.find(tag, ATOM)
        return " ".join(element.text.split()) if element is not None and element.text else ""

    arxiv = normalize_arxiv(text("a:id"))
    journal_doi = normalize_doi(text("arxiv:doi"))
    return {
        "title": text("a:title"),
        "authors": [" ".join((a.findtext("a:name", "", ATOM)).split()) for a in node.findall("a:author", ATOM)],
        "year": int(text("a:published")[:4]) if text("a:published") else None,
        "venue": text("arxiv:journal_ref") or "arXiv",
        "doi": journal_doi or (f"10.48550/arxiv.{arxiv}" if arxiv else None),
        "arxiv": arxiv,
        "url": f"https://arxiv.org/abs/{arxiv}" if arxiv else "",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv}" if arxiv else "",
        "abstract": text("a:summary"),
        "type": "preprint",
        "sources": ["arxiv"],
    }


def arxiv_query(params):
    """arXiv API を呼び出し，文献のリストを返す．

    Args:
        params (dict): arXiv API のクエリパラメータ．

    Returns:
        list[dict]: 文献のリスト．
    """
    root = ET.fromstring(http_get("https://export.arxiv.org/api/query", params, accept="application/atom+xml"))
    entries = [arxiv_to_entry(node) for node in root.findall("a:entry", ATOM)]
    return [entry for entry in entries if entry["arxiv"] and entry["title"] and entry["title"].lower() != "error"]


def arxiv_search(query, limit=20, sort="relevance"):
    """arXiv で文献を検索する．

    Args:
        query (str): 検索語．
        limit (int): 取得する件数．
        sort (str): "relevance"（関連度順）または "date"（投稿日の新しい順）．

    Returns:
        list[dict]: 文献のリスト．
    """
    return arxiv_query({"search_query": f"all:{query}", "max_results": limit,
                        "sortBy": "submittedDate" if sort == "date" else "relevance"})


def crossref_work(doi):
    """Crossref から DOI の文献を取得する．

    Args:
        doi (str): DOI．

    Returns:
        dict: 文献．
    """
    message = json.loads(http_get(f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}",
                                  polite_params({})))["message"]
    date_parts = (message.get("issued") or {}).get("date-parts") or [[None]]
    abstract = re.sub(r"<[^>]+>", "", message.get("abstract") or "").strip()
    return {
        "title": (message.get("title") or [""])[0],
        "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in message.get("author") or []],
        "year": date_parts[0][0],
        "venue": (message.get("container-title") or [""])[0],
        "doi": normalize_doi(message.get("DOI")),
        "url": f"https://doi.org/{normalize_doi(message.get('DOI'))}",
        "abstract": abstract,
        "cited_by": message.get("is-referenced-by-count"),
        "type": message.get("type") or "",
        "sources": ["crossref"],
    }


def resolve_identifier(doi=None, arxiv=None, openalex=None):
    """DOI，arXiv ID，OpenAlex ID から文献を取得する．

    Args:
        doi (str | None): DOI．
        arxiv (str | None): arXiv ID．
        openalex (str | None): OpenAlex ID．

    Returns:
        dict: 文献．
    """
    if openalex:
        return openalex_work(openalex)
    if arxiv:
        arxiv = normalize_arxiv(arxiv)
        if not arxiv:
            raise SurveyKitError("arXiv ID の形式が正しくありません（例: 1612.00410）．")
        results = arxiv_query({"id_list": arxiv, "max_results": 1})
        if not results:
            raise SurveyKitError(f"arXiv に {arxiv} が見つかりません．")
        return results[0]
    doi = normalize_doi(doi)
    try:
        return openalex_work(f"doi:{doi}")
    except SurveyKitError:
        return crossref_work(doi)


def title_similarity(a, b):
    """2つのタイトルの類似度を返す．

    Args:
        a (str): タイトル．
        b (str): タイトル．

    Returns:
        float: 0〜1 の類似度．
    """
    return difflib.SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


# =====================================================================================
# 表示・出力
# =====================================================================================

def print_entries(rows):
    """文献の一覧を表示する．

    Args:
        rows (list[tuple[str, dict, str]]): (引用キー, 文献, 印) のリスト．印は "new" 等．

    Returns:
        None
    """
    for index, (key, entry, mark) in enumerate(rows, start=1):
        cited = entry.get("cited_by")
        cited_text = f"{cited:>6}" if isinstance(cited, int) else "     -"
        title = (entry.get("title") or "")[:90]
        print(f"{index:>3}. [{mark:^8}] {key:<28} {entry.get('year') or '----'} 被引用{cited_text}  {title}")
        if entry.get("venue"):
            print(f"{'':>16}{entry['venue'][:80]}")


def append_search_log(root, record):
    """検索の記録を `survey/search_log.json` に追加する．

    Args:
        root (Path): プロジェクトルート．
        record (dict): 記録．

    Returns:
        None
    """
    path = survey_dir(root) / "search_log.json"
    log = read_json(path, [])
    log.append({**record, "at": now()})
    write_json(path, log)


def latex_escape(text):
    """BibTeX・LaTeX 用に特殊文字をエスケープする．

    Args:
        text (str): 文字列．

    Returns:
        str: エスケープ後の文字列．
    """
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
                    "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(replacements.get(char, char) for char in text or "")


def bibtex_entry(key, entry):
    """文献を BibTeX のエントリに変換する．

    Args:
        key (str): 引用キー．
        entry (dict): 文献．

    Returns:
        str: BibTeX のエントリ．
    """
    work_type = entry.get("type") or ""
    is_arxiv_only = entry.get("arxiv") and (not entry.get("venue") or entry["venue"].lower() in ("arxiv", "arxiv (cornell university)"))
    if is_arxiv_only or work_type in ("preprint", "posted-content"):
        bib_type = "misc"
    elif work_type in ("proceedings-article",) or re.search(r"proceedings|conference|workshop|symposium", entry.get("venue", ""), re.I):
        bib_type = "inproceedings"
    else:
        bib_type = "article"

    fields = [("title", "{" + latex_escape(entry.get("title")) + "}"),
              ("author", " and ".join(latex_escape(author) for author in entry.get("authors") or []))]
    if entry.get("year"):
        fields.append(("year", str(entry["year"])))
    if bib_type == "article" and entry.get("venue"):
        fields.append(("journal", latex_escape(entry["venue"])))
    if bib_type == "inproceedings":
        fields.append(("booktitle", latex_escape(entry["venue"])))
    if entry.get("arxiv"):
        fields += [("eprint", entry["arxiv"]), ("archiveprefix", "arXiv")]
    if entry.get("doi"):
        fields.append(("doi", entry["doi"]))
    if entry.get("url"):
        fields.append(("url", entry["url"]))
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields if value)
    return f"@{bib_type}{{{key},\n{body}\n}}"


def short_author(author):
    """著者名を "A. Family" の形式にする．

    Args:
        author (str): 著者名（"Given Family" 形式）．

    Returns:
        str: 短い形式の著者名．
    """
    parts = author.split()
    if len(parts) <= 1:
        return author
    initials = " ".join(f"{part[0]}." for part in parts[:-1] if part)
    return f"{initials} {parts[-1]}"


def bibitem_entry(key, entry):
    """文献を ai-tex-kit の論文テンプレートの \\bibitem の形式に変換する．

    形式: "A. Family, B. Family, IEEE Access, 2022." 著者が3名以上の場合は "A. Family, et al., ..."．

    Args:
        key (str): 引用キー．
        entry (dict): 文献．

    Returns:
        str: \\bibitem の行．
    """
    authors = entry.get("authors") or []
    if len(authors) >= 3:
        author_text = f"{short_author(authors[0])}, et al."
    else:
        author_text = ", ".join(short_author(author) for author in authors)
    venue = entry.get("venue") or ""
    if entry.get("arxiv") and (not venue or venue.lower().startswith("arxiv")):
        venue = f"arXiv:{entry['arxiv']}"
    parts = [part for part in (author_text, latex_escape(venue), str(entry.get("year") or "")) if part]
    return f"    \\bibitem{{{key}}}\n    {', '.join(parts)}."


# =====================================================================================
# 引用キーの抽出
# =====================================================================================

def extract_citations(path):
    """TeX・Markdown ファイルから引用キーを抽出する．

    TeX は \\cite{a,b}，\\citep[p.1]{a} 等，Markdown は [@a; @b] の形式に対応する．

    Args:
        path (Path): ファイルのパス．

    Returns:
        set[str]: 引用キー．
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    keys = set()
    if path.suffix == ".tex":
        text = re.sub(r"(?<!\\)%.*", "", text)
        for match in CITE_TEX_PATTERN.finditer(text):
            keys.update(key.strip() for key in match.group(1).split(",") if key.strip())
    else:
        for match in CITE_MD_PATTERN.finditer(text):
            keys.update(MD_KEY_PATTERN.findall(match.group(1)))
    return keys


def extract_defined_keys(path):
    """.bib・.tex ファイルから定義済みの文献キーを抽出する．

    Args:
        path (Path): ファイルのパス．

    Returns:
        set[str]: 定義済みの文献キー．
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix == ".bib":
        return set(BIB_ENTRY_PATTERN.findall(text))
    return set(BIBITEM_PATTERN.findall(re.sub(r"(?<!\\)%.*", "", text)))


def expand_paths(paths, suffixes):
    """ファイルとディレクトリのリストを，指定した拡張子のファイルのリストに展開する．

    Args:
        paths (list[Path]): ファイルまたはディレクトリ．
        suffixes (tuple[str]): 対象の拡張子．

    Returns:
        list[Path]: ファイルのリスト．
    """
    files = []
    for path in paths:
        if path.is_dir():
            files += sorted(p for p in path.rglob("*") if p.suffix in suffixes and ".ai" not in p.parts)
        elif path.exists() and path.suffix in suffixes:
            files.append(path)
        elif not path.exists():
            raise SurveyKitError(f"ファイルがありません: {path}")
    return files


# =====================================================================================
# ノート
# =====================================================================================

def parse_front_matter(text):
    """ノートの先頭の "---" で囲まれた "key: value" の行を辞書にする．

    Args:
        text (str): ノートの内容．

    Returns:
        dict[str, str]: フロントマター．
    """
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).split("\n"):
        if ":" in line:
            name, value = line.split(":", 1)
            fields[name.strip()] = value.strip()
    return fields


def note_path(root, key):
    """ノートのパスを返す．

    Args:
        root (Path): プロジェクトルート．
        key (str): 引用キー．

    Returns:
        Path: `survey/notes/{key}.md`．
    """
    return survey_dir(root) / "notes" / f"{key}.md"


def comparison_table(root, library, fields):
    """採録した文献のノートから比較表を作成する．

    Args:
        root (Path): プロジェクトルート．
        library (Library): 文献ライブラリ．
        fields (list[str]): 表の列にするフロントマターの項目．

    Returns:
        str: マークダウンの表．
    """
    header = "| 文献 | 年 | " + " | ".join(fields) + " |"
    lines = [header, "|" + ":---|" * (len(fields) + 2)]
    included = sorted(((key, e) for key, e in library.entries.items() if e["status"] == "included"),
                      key=lambda item: (item[1].get("year") or 0, item[0]))
    for key, entry in included:
        path = note_path(root, key)
        front = parse_front_matter(path.read_text(encoding="utf-8")) if path.exists() else {}
        cells = [front.get(field, "") or "−" for field in fields]
        cells = [cell.replace("|", "\\|") for cell in cells]
        lines.append(f"| [@{key}] | {entry.get('year') or ''} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def replace_table_block(text, table):
    """比較表のマーカーの間を置換する．

    Args:
        text (str): 対象の文字列．
        table (str): 表．

    Returns:
        str: 置換後の文字列．
    """
    start, end = TABLE_MARKERS
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    return pattern.sub(lambda _: f"{start}\n{table}\n{end}", text, count=1)


# =====================================================================================
# コマンド
# =====================================================================================

def command_init(root, theme):
    """調査ディレクトリを初期化する．

    Args:
        root (Path): プロジェクトルート．
        theme (str): 調査のテーマ．

    Returns:
        None
    """
    directory = survey_dir(root)
    if (directory / "library.json").exists():
        raise SurveyKitError("survey/library.json は既に存在します．")
    (directory / "notes").mkdir(parents=True, exist_ok=True)
    write_json(directory / "library.json", {})
    write_json(directory / "search_log.json", [])
    write_json(directory / "features.json", [])
    for name in ("plan.md", "survey_report.md"):
        text = (TEMPLATE_DIR / name).read_text(encoding="utf-8").replace("{{THEME}}", theme)
        write_text(directory / name, text)
    write_text(directory / ".gitignore", "pdfs/\n")
    print("[init] survey/ を作成しました（plan.md，survey_report.md，library.json，search_log.json，features.json，notes/）．")


def command_search(root, query, source="openalex", limit=20, from_year=None, to_year=None, sort="relevance", add=True):
    """文献を検索し，候補としてライブラリに追加する．

    Args:
        root (Path): プロジェクトルート．
        query (str): 検索語．
        source (str): "openalex" または "arxiv"．
        limit (int): 取得する件数．
        from_year (int | None): この年以降に限る（OpenAlex のみ）．
        to_year (int | None): この年以前に限る（OpenAlex のみ）．
        sort (str): 並び順．OpenAlex は "relevance"/"cited"，arXiv は "relevance"/"date"．
        add (bool): False の場合はライブラリに追加しない．

    Returns:
        list[str]: 検索結果の引用キー（add=False の場合は空のリスト）．
    """
    library = Library(root)
    if source == "arxiv":
        results = arxiv_search(query, limit, sort)
    else:
        results = openalex_search(query, limit, from_year, to_year, sort)

    rows, keys, new_count = [], [], 0
    record = f"search:{source}:{query}"
    for entry in results:
        if add:
            key, is_new = library.add(entry, record)
            new_count += is_new
            keys.append(key)
            rows.append((key, library.entries[key], "new" if is_new else library.entries[key]["status"]))
        else:
            rows.append(("-", entry, "preview"))
    print_entries(rows)
    if add:
        library.save()
        append_search_log(root, {"type": "search", "source": source, "query": query, "from_year": from_year,
                                 "to_year": to_year, "sort": sort, "limit": limit,
                                 "n_results": len(results), "n_new": new_count})
        print(f"\n[search] {len(results)} 件（新規 {new_count} 件）をライブラリに候補として登録しました．")
    return keys


def command_add(root, doi=None, arxiv=None, openalex=None, title=None, status=None):
    """識別子またはタイトルから文献を1件取得してライブラリに追加する．

    Args:
        root (Path): プロジェクトルート．
        doi (str | None): DOI．
        arxiv (str | None): arXiv ID．
        openalex (str | None): OpenAlex ID．
        title (str | None): タイトル（OpenAlex で検索し，類似度0.9以上の1件を登録する）．
        status (str | None): 登録時の状態．

    Returns:
        str: 引用キー．
    """
    library = Library(root)
    if title:
        results = openalex_search(title, limit=5)
        scored = sorted(((title_similarity(title, e["title"]), e) for e in results), key=lambda x: -x[0])
        if not scored or scored[0][0] < 0.9:
            print_entries([("-", entry, f"{score:.2f}") for score, entry in scored])
            raise SurveyKitError("タイトルが十分に一致する文献がありません．上の候補から DOI 等を指定して追加してください．")
        entry = scored[0][1]
        record = f"add:title:{title}"
    else:
        if not (doi or arxiv or openalex):
            raise SurveyKitError("--doi，--arxiv，--openalex，--title のいずれかを指定してください．")
        entry = resolve_identifier(doi, arxiv, openalex)
        record = f"add:{'doi:' + doi if doi else 'arxiv:' + arxiv if arxiv else 'openalex:' + openalex}"

    key, is_new = library.add(entry, record)
    if status:
        library.entries[key]["status"] = status
    library.save()
    append_search_log(root, {"type": "add", "query": record, "n_results": 1, "n_new": int(is_new)})
    print_entries([(key, library.entries[key], "new" if is_new else "exists")])
    return key


def command_snowball(root, key, direction="both", limit=20):
    """文献の参考文献（backward）と被引用文献（forward）を取得し，候補として追加する．

    Args:
        root (Path): プロジェクトルート．
        key (str): 起点の文献の引用キー．
        direction (str): "references"，"citations"，"both"．
        limit (int): 各方向で取得する最大件数．

    Returns:
        list[str]: 取得した文献の引用キー．
    """
    library = Library(root)
    seed = library.get(key)
    if not seed.get("openalex") or not seed.get("referenced_works"):
        identifier = seed.get("openalex") or (f"doi:{seed['doi']}" if seed.get("doi") else None)
        if not identifier:
            raise SurveyKitError(f"{key} には OpenAlex ID・DOI が無いため，スノーボーリングできません．")
        library.add(openalex_work(identifier))
        seed = library.get(key)

    results = []
    if direction in ("references", "both"):
        for work_id in seed.get("referenced_works", [])[:limit]:
            try:
                results.append(("references", openalex_work(work_id)))
            except SurveyKitError as error:
                print(f"[snowball] 取得に失敗しました: {work_id}（{error}）")
    if direction in ("citations", "both"):
        results += [("citations", entry) for entry in openalex_citing(seed["openalex"], limit)]

    rows, keys, new_count = [], [], 0
    for kind, entry in results:
        found_key, is_new = library.add(entry, f"snowball:{kind}:{key}")
        new_count += is_new
        keys.append(found_key)
        rows.append((found_key, library.entries[found_key], "new" if is_new else kind[:8]))
    library.save()
    append_search_log(root, {"type": "snowball", "query": f"{direction}:{key}", "n_results": len(results), "n_new": new_count})
    print_entries(rows)
    print(f"\n[snowball] {len(results)} 件（新規 {new_count} 件）を候補として登録しました．")
    return keys


def command_set(root, keys, status=None, reason=None, tags=None):
    """文献の状態（候補・採録・除外），理由，タグを設定する．

    Args:
        root (Path): プロジェクトルート．
        keys (list[str]): 引用キー．
        status (str | None): 状態．
        reason (str | None): 採録・除外の理由．除外の場合は必須．
        tags (list[str] | None): 追加するタグ．

    Returns:
        None
    """
    library = Library(root)
    if status == "excluded" and not reason:
        raise SurveyKitError("除外する場合は --reason で理由を指定してください．")
    for key in keys:
        entry = library.get(key)
        if status:
            entry["status"] = status
        if reason:
            entry["reason"] = reason
        for tag in tags or []:
            if tag not in entry["tags"]:
                entry["tags"].append(tag)
        print(f"[set] {key}: status={entry['status']} tags={entry['tags']}")
    library.save()


def command_list(root, status=None, tag=None, sort="year"):
    """ライブラリの文献を一覧表示する．

    Args:
        root (Path): プロジェクトルート．
        status (str | None): この状態の文献に限る．
        tag (str | None): このタグを持つ文献に限る．
        sort (str): "year"，"cited"，"key"．

    Returns:
        None
    """
    library = Library(root)
    items = [(k, e) for k, e in library.entries.items()
             if (status is None or e["status"] == status) and (tag is None or tag in e.get("tags", []))]
    sort_key = {"year": lambda i: (i[1].get("year") or 0, i[0]),
                "cited": lambda i: -(i[1].get("cited_by") or 0),
                "key": lambda i: i[0]}[sort]
    print_entries([(k, e, e["status"]) for k, e in sorted(items, key=sort_key)])
    print(f"\n[list] {len(items)} 件")


def command_show(root, key):
    """文献の詳細（要旨を含む）を表示する．

    Args:
        root (Path): プロジェクトルート．
        key (str): 引用キー．

    Returns:
        None
    """
    entry = Library(root).get(key)
    for field in ("title", "authors", "year", "venue", "doi", "arxiv", "openalex", "url", "pdf_url",
                  "cited_by", "type", "sources", "status", "reason", "tags", "queries"):
        value = entry.get(field)
        if isinstance(value, list):
            value = ", ".join(value)
        print(f"{field:>10}: {value if value not in (None, '') else '-'}")
    print(f"\n{entry.get('abstract') or '（要旨はありません）'}")


def command_note(root, key, force=False):
    """文献のノートを `survey/notes/{key}.md` にテンプレートから作成する．

    Args:
        root (Path): プロジェクトルート．
        key (str): 引用キー．
        force (bool): True の場合，既存のノートを上書きする．

    Returns:
        Path: ノートのパス．
    """
    entry = Library(root).get(key)
    path = note_path(root, key)
    if path.exists() and not force:
        print(f"[note] {path.relative_to(root)} は既に存在します．")
        return path
    values = {
        "KEY": key,
        "TITLE": entry.get("title") or "",
        "AUTHORS": ", ".join(entry.get("authors") or []),
        "YEAR": str(entry.get("year") or ""),
        "VENUE": entry.get("venue") or "",
        "LINK": entry.get("url") or (f"https://doi.org/{entry['doi']}" if entry.get("doi") else ""),
        "PDF": entry.get("pdf_url") or "−",
        "ABSTRACT": entry.get("abstract") or "（API から要旨を取得できなかった．原文を確認すること）",
        "FEATURE_LINES": "\n".join(feature_line(f) for f in load_features(root)),
    }
    text = (TEMPLATE_DIR / "note.md").read_text(encoding="utf-8")
    for name, value in values.items():
        text = text.replace("{{" + name + "}}", value)
    text = re.sub(r"\n\n(?=---\n)", "\n", text, count=1)   # 特徴が無い場合の空行を除く
    write_text(path, text)
    print(f"[note] 作成: {path.relative_to(root)}")
    return path


def command_table(root, fields=None, out=None):
    """採録した文献のノートから比較表を作成する．

    `out` が既存のファイルでマーカーを含む場合はマーカーの間のみを置換する．

    Args:
        root (Path): プロジェクトルート．
        fields (list[str] | None): 列にする項目．None の場合は NOTE_FIELDS．
        out (Path | None): 出力先．None の場合は `survey/comparison.md`．

    Returns:
        Path: 出力先．
    """
    library = Library(root)
    table = comparison_table(root, library, fields or NOTE_FIELDS)
    out = out or survey_dir(root) / "comparison.md"
    if out.exists() and TABLE_MARKERS[0] in out.read_text(encoding="utf-8"):
        text = replace_table_block(out.read_text(encoding="utf-8"), table)
    else:
        text = f"# 採録文献の比較表\n\n{TABLE_MARKERS[0]}\n{table}\n{TABLE_MARKERS[1]}\n"
    write_text(out, text)
    print(f"[table] 作成: {out.relative_to(root) if out.is_relative_to(root) else out}")
    return out


def command_bib(root, fmt="bibtex", status="included", cited_in=None, out=None):
    """ライブラリの文献から参考文献リストを作成する．

    Args:
        root (Path): プロジェクトルート．
        fmt (str): "bibtex"（.bib）または "bibitem"（ai-tex-kit の thebibliography 用）．
        status (str): 対象とする状態．"all" の場合は全て．
        cited_in (list[Path] | None): 指定した場合，これらのファイルで引用されている文献のみを対象とする（引用順）．
        out (Path | None): 出力先．None の場合は標準出力．

    Returns:
        str: 参考文献リスト．
    """
    library = Library(root)
    if cited_in:
        keys = []
        for path in expand_paths(cited_in, (".tex", ".md")):
            keys += [key for key in ordered_citations(path) if key not in keys]
        missing = [key for key in keys if key not in library.entries]
        if missing:
            raise SurveyKitError(f"ライブラリに無い引用キーがあります: {missing}")
    else:
        keys = sorted(k for k, e in library.entries.items() if status == "all" or e["status"] == status)

    unverified = [key for key in keys if not is_verified(library.entries[key])]
    if unverified:
        print(f"[bib] 警告: API で検証されていない文献が含まれます: {unverified}", file=sys.stderr)

    if fmt == "bibitem":
        body = "\n\n".join(bibitem_entry(key, library.entries[key]) for key in keys)
        text = f"\\begin{{thebibliography}}{{{max(len(keys), 9)}}}\n\n{body}\n\n\\end{{thebibliography}}\n"
    else:
        text = "\n\n".join(bibtex_entry(key, library.entries[key]) for key in keys) + "\n"
    if out:
        write_text(out, text)
        print(f"[bib] {len(keys)} 件を {out} に出力しました．")
    else:
        print(text)
    return text


def ordered_citations(path):
    """ファイル中の引用キーを出現順に返す．

    Args:
        path (Path): TeX または Markdown のファイル．

    Returns:
        list[str]: 引用キー（重複なし，出現順）．
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    keys = []
    if path.suffix == ".tex":
        text = re.sub(r"(?<!\\)%.*", "", text)
        found = [k.strip() for m in CITE_TEX_PATTERN.finditer(text) for k in m.group(1).split(",")]
    else:
        found = [k for m in CITE_MD_PATTERN.finditer(text) for k in MD_KEY_PATTERN.findall(m.group(1))]
    for key in found:
        if key and key not in keys:
            keys.append(key)
    return keys


def collect_cite_issues(root, paths, bib_paths=None):
    """文書の引用を検証する．

    Args:
        root (Path): プロジェクトルート．
        paths (list[Path]): 検証する TeX・Markdown のファイルまたはディレクトリ．
        bib_paths (list[Path] | None): 参考文献の定義（.bib，\\bibitem を含む .tex）．
            None の場合は paths 内の .bib と \\bibitem を用いる．

    Returns:
        list[str]: 問題の説明のリスト．
    """
    library = Library(root)
    documents = expand_paths(paths, (".tex", ".md"))
    definitions = expand_paths(bib_paths, (".bib", ".tex")) if bib_paths else expand_paths(paths, (".bib", ".tex"))

    cited = {}
    for path in documents:
        for key in extract_citations(path):
            cited.setdefault(key, []).append(path.name)
    defined = set()
    for path in definitions:
        defined |= extract_defined_keys(path)
    uses_definitions = bool(defined) or any(p.suffix == ".bib" for p in definitions)

    issues = []
    for key, files in sorted(cited.items()):
        entry = library.entries.get(key)
        if entry is None:
            issues.append(f"{key}（{', '.join(sorted(set(files)))}）: ライブラリに無い文献です．"
                          "`surveykit.py add` で API から登録してください．")
            continue
        if not is_verified(entry):
            issues.append(f"{key}: API で検証されていない文献です（sources={entry.get('sources')}）．")
        if entry["status"] != "included":
            issues.append(f"{key}: 採録（included）されていない文献を引用しています（status={entry['status']}）．")
        if uses_definitions and key not in defined and not all(f.endswith(".md") for f in files):
            issues.append(f"{key}: 参考文献リスト（.bib / \\bibitem）に定義がありません．")
    for key in sorted(defined - set(cited)):
        issues.append(f"{key}: 参考文献リストに定義されていますが，本文で引用されていません．")
    return issues


def command_cite_check(root, paths, bib_paths=None):
    """文書の引用を検証し，結果を表示する．

    Args:
        root (Path): プロジェクトルート．
        paths (list[Path]): 検証するファイルまたはディレクトリ．
        bib_paths (list[Path] | None): 参考文献の定義．

    Returns:
        int: 問題の件数．
    """
    issues = collect_cite_issues(root, paths, bib_paths)
    for issue in issues:
        print(f"  - {issue}")
    print(f"[cite-check] {len(issues)} 件の問題があります．" if issues else "[cite-check] 問題はありません．")
    return len(issues)


def survey_stats(root, library):
    """調査の件数の集計（PRISMA の流れ図に相当）を返す．

    Args:
        root (Path): プロジェクトルート．
        library (Library): 文献ライブラリ．

    Returns:
        dict[str, int]: 集計．
    """
    log = read_json(survey_dir(root) / "search_log.json", [])
    statuses = [entry["status"] for entry in library.entries.values()]
    return {
        "searches": sum(1 for record in log if record.get("type") == "search"),
        "snowballs": sum(1 for record in log if record.get("type") == "snowball"),
        "retrieved": sum(record.get("n_results", 0) for record in log),
        "unique": len(library.entries),
        "candidate": statuses.count("candidate"),
        "included": statuses.count("included"),
        "excluded": statuses.count("excluded"),
    }


def collect_issues(root):
    """調査全体を検証し，問題を収集する．

    Args:
        root (Path): プロジェクトルート．

    Returns:
        tuple[list[str], list[str]]: (エラー, 警告)．
    """
    library = Library(root)
    directory = survey_dir(root)
    errors, warnings = [], []

    for name in ("plan.md", "survey_report.md"):
        path = directory / name
        if not path.exists():
            errors.append(f"{name} がありません．")
        elif TODO_MARK in path.read_text(encoding="utf-8"):
            errors.append(f"{name} に {TODO_MARK} が残っています．")

    included = [k for k, e in library.entries.items() if e["status"] == "included"]
    if not included:
        errors.append("採録（included）された文献がありません．")
    for key in included:
        entry = library.entries[key]
        if not is_verified(entry):
            errors.append(f"{key}: API で検証されていない文献を採録しています．")
        path = note_path(root, key)
        if not path.exists():
            errors.append(f"{key}: ノートがありません（`surveykit.py note {key}`）．")
            continue
        text = path.read_text(encoding="utf-8")
        front = parse_front_matter(text)
        if TODO_MARK in text:
            errors.append(f"notes/{key}.md に {TODO_MARK} が残っています．")
        if front.get("read_level") not in READ_LEVELS:
            errors.append(f"notes/{key}.md の read_level を {READ_LEVELS} のいずれかにしてください．")

    for key, entry in library.entries.items():
        if entry["status"] == "excluded" and not entry.get("reason"):
            errors.append(f"{key}: 除外の理由がありません．")
    candidates = sum(1 for e in library.entries.values() if e["status"] == "candidate")
    if candidates:
        warnings.append(f"未判定（candidate）の文献が {candidates} 件あります．")

    report = directory / "survey_report.md"
    if report.exists():
        errors += [f"survey_report.md: {issue}" for issue in collect_cite_issues(root, [report])]
        cited = extract_citations(report)
        uncited = sorted(set(included) - cited)
        if uncited:
            warnings.append(f"survey_report.md で引用されていない採録文献: {', '.join(uncited)}")

    feature_errors, feature_warnings = collect_feature_issues(root, library, included)
    errors += feature_errors
    warnings += feature_warnings
    idea_errors, idea_warnings = collect_idea_issues(root)
    errors += idea_errors
    warnings += idea_warnings

    comparison = directory / "comparison.md"
    if comparison.exists():
        expected = replace_table_block(comparison.read_text(encoding="utf-8"), comparison_table(root, library, NOTE_FIELDS))
        if expected != comparison.read_text(encoding="utf-8"):
            warnings.append("comparison.md がノートと一致しません（`surveykit.py table` を実行してください）．")
    else:
        errors.append("comparison.md がありません（`surveykit.py table` を実行してください）．")
    return errors, warnings


def command_check(root):
    """調査全体を検証し，集計とともに表示する．

    Args:
        root (Path): プロジェクトルート．

    Returns:
        int: エラーの件数．
    """
    library = Library(root)
    stats = survey_stats(root, library)
    print("[check] 集計: " + ", ".join(f"{name}={value}" for name, value in stats.items()))
    errors, warnings = collect_issues(root)
    for message in errors:
        print(f"  [error] {message}")
    for message in warnings:
        print(f"  [warn]  {message}")
    print(f"[check] エラー {len(errors)} 件，警告 {len(warnings)} 件．")
    return len(errors)


def command_doctor():
    """各 API に接続できるかを確認する．

    Returns:
        int: 接続できなかった API の数．
    """
    checks = {
        "OpenAlex": lambda: openalex_search("information bottleneck", limit=1),
        "arXiv": lambda: arxiv_query({"id_list": "1612.00410", "max_results": 1}),
        "Crossref": lambda: crossref_work("10.1038/nature14539"),
    }
    failures = 0
    for name, check in checks.items():
        try:
            result = check()
            title = result[0]["title"] if isinstance(result, list) and result else result.get("title", "") if isinstance(result, dict) else ""
            print(f"  [OK] {name}: {title[:70]}")
        except Exception as error:   # 診断のため全ての例外を表示する
            failures += 1
            print(f"  [NG] {name}: {error}")
    return failures


# =====================================================================================
# 特徴行列（手法 × 特徴）
# =====================================================================================

FEATURE_STATUSES = {"yes": "✓", "partial": "△", "no": "✗", "unknown": "?"}
TEX_SYMBOLS = {"yes": r"$\checkmark$", "partial": r"$\triangle$", "no": r"$\times$", "unknown": "?"}
FEATURE_ID_PATTERN = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")
OURS_LABEL = "本研究"


def load_features(root):
    """特徴の定義（`survey/features.json`）を読み込む．

    Args:
        root (Path): プロジェクトルート．

    Returns:
        list[dict]: 特徴の定義．各要素は "id"，"name"，"definition"，"kind"（"binary" または "text"）を持つ．
    """
    return read_json(survey_dir(root) / "features.json", [])


def feature_line(feature):
    """ノートのフロントマターに追加する特徴の行（未記入）を作成する．

    Args:
        feature (dict): 特徴の定義．

    Returns:
        str: "f.{id}: ..." の行．
    """
    if feature["kind"] == "binary":
        return f"f.{feature['id']}: {TODO_MARK} yes / partial / no / unknown のいずれかと根拠（例: yes (Sec. 3.2)）"
    return f"f.{feature['id']}: {TODO_MARK} {feature['name']}の値と根拠（例: MNIST (Table 1)）"


def add_front_matter_lines(path, lines):
    """ノートのフロントマターの末尾に，まだ無い行を追加する．

    Args:
        path (Path): ノートのパス．
        lines (list[str]): 追加する "key: value" の行．

    Returns:
        bool: 追加した場合 True．
    """
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return False
    existing = parse_front_matter(text)
    new_lines = [line for line in lines if line.split(":", 1)[0].strip() not in existing]
    if not new_lines:
        return False
    head = match.group(1) + "\n" + "\n".join(new_lines)
    write_text(path, f"---\n{head}\n---\n" + text[match.end():])
    return True


def parse_feature_value(value, kind):
    """特徴の値を解析する．

    binary の場合は先頭の語を状態（yes / partial / no / unknown），括弧内または残りを根拠とする．

    Args:
        value (str | None): フロントマターの値（例: "yes (Sec. 3.2)"）．
        kind (str): "binary" または "text"．

    Returns:
        tuple[str | None, str, str]: (状態．text の場合は None, 根拠, 値の全体)．
    """
    value = (value or "").strip()
    if kind != "binary":
        match = re.search(r"\(([^()]*)\)\s*$", value)
        return None, (match.group(1).strip() if match else ""), value
    parts = value.split(None, 1)
    status = parts[0].lower() if parts else ""
    evidence = parts[1].strip() if len(parts) > 1 else ""
    evidence = re.sub(r"^[(（](.*)[)）]$", r"\1", evidence).strip()
    return status, evidence, value


def ours_path(root):
    """本研究の特徴を記入するファイルのパスを返す．

    Args:
        root (Path): プロジェクトルート．

    Returns:
        Path: `survey/ours.md`．
    """
    return survey_dir(root) / "ours.md"


def matrix_rows(root, library, include_ours=True):
    """特徴行列の行（採録した文献と本研究）を作成する．

    Args:
        root (Path): プロジェクトルート．
        library (Library): 文献ライブラリ．
        include_ours (bool): True の場合，`survey/ours.md` があれば本研究の行を最後に加える．

    Returns:
        list[dict]: 各行は "label"（手法名），"key"（引用キー．本研究は None），"year"，"front"（フロントマター），"ours" を持つ．
    """
    rows = []
    included = sorted(((k, e) for k, e in library.entries.items() if e["status"] == "included"),
                      key=lambda item: (item[1].get("year") or 0, item[0]))
    for key, entry in included:
        path = note_path(root, key)
        front = parse_front_matter(path.read_text(encoding="utf-8")) if path.exists() else {}
        name = front.get("name", "")
        label = name if name and TODO_MARK not in name else key
        rows.append({"label": label, "key": key, "year": entry.get("year"), "front": front, "ours": False})
    if include_ours and ours_path(root).exists():
        front = parse_front_matter(ours_path(root).read_text(encoding="utf-8"))
        rows.append({"label": front.get("name") or OURS_LABEL, "key": None, "year": None, "front": front, "ours": True})
    return rows


def cell_status(row, feature):
    """行と特徴に対応するセルの状態と表示を返す．

    Args:
        row (dict): matrix_rows の行．
        feature (dict): 特徴の定義．

    Returns:
        tuple[str | None, str]: (状態, 表示する文字列)．
    """
    status, _, raw = parse_feature_value(row["front"].get(f"f.{feature['id']}"), feature["kind"])
    if TODO_MARK in raw or not raw:
        return None, "−"
    if feature["kind"] == "binary":
        return status, FEATURE_STATUSES.get(status, "?")
    return None, re.sub(r"\s*\([^()]*\)\s*$", "", raw)


def find_gaps(rows, features):
    """既存手法（本研究以外）の特徴の充足状況から，空白を見つける．

    - 満たす手法が無い特徴
    - それぞれを満たす手法はあるが，両方を同時に満たす手法が無い特徴の組

    Args:
        rows (list[dict]): matrix_rows の行．
        features (list[dict]): 特徴の定義．

    Returns:
        dict: "coverage"（{特徴ID: 満たす既存手法の数}），"none"（満たす手法が無い特徴のIDのリスト），
            "pairs"（両立する手法が無い特徴の組 (ID, ID) のリスト），"n_methods"（既存手法の数）．
    """
    existing = [row for row in rows if not row["ours"]]
    binary = [f for f in features if f["kind"] == "binary"]
    satisfied = {f["id"]: {i for i, row in enumerate(existing) if cell_status(row, f)[0] == "yes"} for f in binary}
    coverage = {fid: len(indices) for fid, indices in satisfied.items()}
    pairs = []
    for index, first in enumerate(binary):
        for second in binary[index + 1:]:
            a, b = satisfied[first["id"]], satisfied[second["id"]]
            if a and b and not (a & b):
                pairs.append((first["id"], second["id"]))
    return {"coverage": coverage, "none": [fid for fid, n in coverage.items() if n == 0],
            "pairs": pairs, "n_methods": len(existing)}


def ours_status(rows, feature_id, features):
    """本研究の行の特徴の状態を返す．

    Args:
        rows (list[dict]): matrix_rows の行．
        feature_id (str): 特徴のID．
        features (list[dict]): 特徴の定義．

    Returns:
        str | None: 状態．本研究の行が無い場合は None．
    """
    feature = next(f for f in features if f["id"] == feature_id)
    ours = next((row for row in rows if row["ours"]), None)
    return cell_status(ours, feature)[0] if ours else None


def matrix_markdown(root, library, features, transpose=False):
    """特徴行列のマークダウン（定義，行列，凡例，充足状況，空白）を作成する．

    Args:
        root (Path): プロジェクトルート．
        library (Library): 文献ライブラリ．
        features (list[dict]): 特徴の定義．
        transpose (bool): True の場合，行を特徴，列を手法にする．

    Returns:
        str: マークダウン．
    """
    rows = matrix_rows(root, library)
    names = {f["id"]: f["name"] for f in features}
    lines = ["# 特徴行列", "", "`surveykit.py matrix` が生成するファイルである．編集せず，ノートと `features.json` を修正して再生成する．", "",
             "## 特徴の定義", "", "| ID | 特徴 | 定義 | 種類 |", "|:---|:---|:---|:---:|"]
    lines += [f"| {f['id']} | {f['name']} | {f['definition']} | {f['kind']} |" for f in features]

    def row_label(row):
        return f"**{row['label']}**" if row["ours"] else f"{row['label']} [@{row['key']}]"

    lines += ["", "## 行列", ""]
    if transpose:
        lines.append("| 特徴 | " + " | ".join(row_label(row) for row in rows) + " |")
        lines.append("|:---|" + ":---:|" * len(rows))
        for feature in features:
            lines.append(f"| {feature['name']} | " + " | ".join(cell_status(row, feature)[1] for row in rows) + " |")
    else:
        lines.append("| 手法 | 年 | " + " | ".join(f["name"] for f in features) + " |")
        lines.append("|:---|:---:|" + ":---:|" * len(features))
        for row in rows:
            cells = [cell_status(row, feature)[1] for feature in features]
            lines.append(f"| {row_label(row)} | {row['year'] or '−'} | " + " | ".join(cells) + " |")
    lines += ["", "凡例: ✓ 満たす，△ 部分的に満たす，✗ 満たさない，? 文献から判断できない，− 未記入．本研究の行は計画・主張であり，検証済みとは限らない．"]

    gaps = find_gaps(rows, features)
    if gaps["coverage"]:
        lines += ["", "## 既存手法の充足状況", "", "| 特徴 | 満たす既存手法 | 本研究 |", "|:---|:---:|:---:|"]
        for fid, count in gaps["coverage"].items():
            ours = ours_status(rows, fid, features)
            lines.append(f"| {names[fid]} | {count} / {gaps['n_methods']} | {FEATURE_STATUSES.get(ours, '−') if ours else '−'} |")

        lines += ["", "## 空白（既存手法が満たしていない特徴・組み合わせ）", ""]
        if not gaps["none"] and not gaps["pairs"]:
            lines.append("既存手法の組み合わせで満たされない特徴・組はない．")
        for fid in gaps["none"]:
            mark = "（本研究が満たす）" if ours_status(rows, fid, features) == "yes" else ""
            lines.append(f"- 「{names[fid]}」を満たす既存手法がない{mark}．")
        for first, second in gaps["pairs"]:
            both = ours_status(rows, first, features) == "yes" and ours_status(rows, second, features) == "yes"
            lines.append(f"- 「{names[first]}」と「{names[second]}」を両立する既存手法がない{'（本研究が両立する）' if both else ''}．")
    return "\n".join(lines) + "\n"


def matrix_tex(root, library, features, transpose=False):
    """特徴行列の LaTeX の表を作成する（ai-tex-kit の規約に従い，キャプションは表の上，項目は中央揃え）．

    Args:
        root (Path): プロジェクトルート．
        library (Library): 文献ライブラリ．
        features (list[dict]): 特徴の定義．
        transpose (bool): True の場合，行を特徴，列を手法にする．

    Returns:
        str: table 環境．記号に `\\checkmark` を用いるため amssymb が必要．
    """
    rows = matrix_rows(root, library)

    def label(row):
        return r"\textbf{" + latex_escape(row["label"]) + "}" if row["ours"] else latex_escape(row["label"]) + r"~\cite{" + row["key"] + "}"

    def cell(row, feature):
        status, text = cell_status(row, feature)
        return TEX_SYMBOLS.get(status, "?") if feature["kind"] == "binary" and status else latex_escape(text)

    if transpose:
        header = ["特徴"] + [label(row) for row in rows]
        body = [[latex_escape(f["name"])] + [cell(row, f) for row in rows] for f in features]
    else:
        header = ["手法"] + [latex_escape(f["name"]) for f in features]
        body = [[label(row)] + [cell(row, f) for f in features] for row in rows]

    lines = [r"\begin{table}[tb]", "    \\centering", "    \\caption{既存手法と本研究の比較}", "    \\label{tab_feature_matrix}",
             "    \\begin{tabular}{" + "c" * len(header) + "}", "        \\hline",
             "        " + " & ".join(header) + r" \\", "        \\hline"]
    lines += ["        " + " & ".join(cells) + r" \\" for cells in body]
    lines += ["        \\hline", "    \\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def command_feature_add(root, feature_id, name, definition, kind="binary"):
    """特徴を定義し，既存のノートと `ours.md` のフロントマターに未記入の行を追加する．

    Args:
        root (Path): プロジェクトルート．
        feature_id (str): 特徴のID（英小文字・数字・"_"．例: "closed_form"）．
        name (str): 表の見出しにする特徴の名前．
        definition (str): 満たす・満たさないを判断できる定義．
        kind (str): "binary"（✓/△/✗）または "text"（値を記入する）．

    Returns:
        None
    """
    if not FEATURE_ID_PATTERN.fullmatch(feature_id):
        raise SurveyKitError(f"特徴のIDは英小文字・数字・'_' で指定してください: {feature_id}")
    features = load_features(root)
    if any(f["id"] == feature_id for f in features):
        raise SurveyKitError(f"特徴 '{feature_id}' は既に定義されています．")
    feature = {"id": feature_id, "name": name, "definition": definition, "kind": kind}
    features.append(feature)
    write_json(survey_dir(root) / "features.json", features)

    paths = sorted((survey_dir(root) / "notes").glob("*.md"))
    if ours_path(root).exists():
        paths.append(ours_path(root))
    updated = [path.name for path in paths if add_front_matter_lines(path, [feature_line(feature)])]
    print(f"[feature] 追加: {feature_id}（{name}）．未記入の行を追加したファイル: {len(updated)} 件")


def command_feature_list(root):
    """定義済みの特徴を表示する．

    Args:
        root (Path): プロジェクトルート．

    Returns:
        None
    """
    features = load_features(root)
    for feature in features:
        print(f"{feature['id']:<20} [{feature['kind']}] {feature['name']}: {feature['definition']}")
    print(f"\n[feature] {len(features)} 件")


def command_ours(root, name=OURS_LABEL):
    """本研究の特徴を記入する `survey/ours.md` を作成する（既にある場合は不足している特徴の行を追加する）．

    Args:
        root (Path): プロジェクトルート．
        name (str): 特徴行列に表示する本研究の名前．

    Returns:
        Path: `survey/ours.md`．
    """
    path = ours_path(root)
    features = load_features(root)
    if not path.exists():
        text = (TEMPLATE_DIR / "ours.md").read_text(encoding="utf-8").replace("{{NAME}}", name)
        write_text(path, text)
        print(f"[ours] 作成: {path.relative_to(root)}")
    if add_front_matter_lines(path, [feature_line(f) for f in features]):
        print("[ours] 不足していた特徴の行を追加しました．")
    return path


def command_matrix(root, fmt="md", transpose=False, out=None):
    """特徴行列を作成する．

    Args:
        root (Path): プロジェクトルート．
        fmt (str): "md"（`survey/feature_matrix.md`）または "tex"（論文用の table 環境）．
        transpose (bool): True の場合，行を特徴，列を手法にする．
        out (Path | None): 出力先．None の場合，md は `survey/feature_matrix.md`，tex は標準出力．

    Returns:
        str: 作成した内容．
    """
    library = Library(root)
    features = load_features(root)
    if not features:
        raise SurveyKitError("特徴が定義されていません（`surveykit.py feature add ...`）．")
    if fmt == "tex":
        text = matrix_tex(root, library, features, transpose)
        if out:
            write_text(out, text)
            print(f"[matrix] 作成: {out}")
        else:
            print(text)
        return text
    text = matrix_markdown(root, library, features, transpose)
    out = out or survey_dir(root) / "feature_matrix.md"
    write_text(out, text)
    print(f"[matrix] 作成: {out.relative_to(root) if out.is_relative_to(root) else out}")
    return text


def collect_feature_issues(root, library, included):
    """特徴行列に関する問題を収集する．

    Args:
        root (Path): プロジェクトルート．
        library (Library): 文献ライブラリ．
        included (list[str]): 採録した文献の引用キー．

    Returns:
        tuple[list[str], list[str]]: (エラー, 警告)．
    """
    features = load_features(root)
    errors, warnings = [], []
    if not features:
        warnings.append("特徴が定義されていません．手法の立ち位置を示すため `surveykit.py feature add` で3〜8個定義してください．")
        return errors, warnings
    if len(features) < 3:
        warnings.append(f"特徴が {len(features)} 個です．立ち位置を示すには3〜8個を目安に定義してください．")

    for key in included:
        path = note_path(root, key)
        if not path.exists():
            continue
        front = parse_front_matter(path.read_text(encoding="utf-8"))
        for feature in features:
            value = front.get(f"f.{feature['id']}")
            if value is None:
                errors.append(f"notes/{key}.md に特徴 f.{feature['id']} がありません（`surveykit.py feature add` 後に作成したノートは自動で追加されます）．")
                continue
            if TODO_MARK in value:
                continue   # TODO(AI) の残りはノートの検証で報告される
            status, evidence, _ = parse_feature_value(value, feature["kind"])
            if feature["kind"] == "binary" and status not in FEATURE_STATUSES:
                errors.append(f"notes/{key}.md の f.{feature['id']} は yes / partial / no / unknown のいずれかで始めてください: {value}")
            elif status != "unknown" and not evidence:
                errors.append(f"notes/{key}.md の f.{feature['id']} に根拠（節・表の番号，または abstract）がありません: {value}")

    if not ours_path(root).exists():
        warnings.append("本研究の行がありません（`surveykit.py ours` で survey/ours.md を作成してください）．")
    else:
        text = ours_path(root).read_text(encoding="utf-8")
        if TODO_MARK in text:
            errors.append(f"ours.md に {TODO_MARK} が残っています．")
        front = parse_front_matter(text)
        for feature in features:
            status, _, value = parse_feature_value(front.get(f"f.{feature['id']}"), feature["kind"])
            if feature["kind"] == "binary" and TODO_MARK not in value and status not in FEATURE_STATUSES:
                errors.append(f"ours.md の f.{feature['id']} は yes / partial / no / unknown のいずれかで始めてください: {value}")

    rows = matrix_rows(root, library)
    existing = [row for row in rows if not row["ours"]]
    for feature in features:
        if feature["kind"] != "binary" or len(existing) < 2:
            continue
        statuses = {cell_status(row, feature)[0] for row in existing} - {None, "unknown"}
        if len(statuses) == 1:
            warnings.append(f"特徴「{feature['name']}」は既存手法の全てが同じ値です．手法を区別する特徴になっているか見直してください．")

    matrix = survey_dir(root) / "feature_matrix.md"
    if not matrix.exists():
        errors.append("feature_matrix.md がありません（`surveykit.py matrix` を実行してください）．")
    else:
        current = matrix.read_text(encoding="utf-8")
        if current not in (matrix_markdown(root, library, features), matrix_markdown(root, library, features, True)):
            warnings.append("feature_matrix.md がノートと一致しません（`surveykit.py matrix` を実行してください）．")
    return errors, warnings


# =====================================================================================
# アイデアの材料（ideas）と改善の候補の検証
# =====================================================================================

IDEA_LENSES = {
    "空白": "特徴行列で，満たす手法が無い特徴や，両立する手法が無い特徴の組を埋める",
    "前提を壊す": "既存研究が置いている仮定が成り立たない状況を問題にする",
    "極端化・単純化": "条件やハイパーパラメータを極端にする，手法から部品を削って本質を特定する",
    "他分野からの持ち込み": "他の分野の道具・考え方を自分の分野の問題に当てはめる",
    "現実の問題から逆算": "応用先の制約で既存手法が破綻する点を出発点にする",
    "理論と実践のずれ": "理論的な保証と実際の挙動の食い違いを説明する",
    "評価を疑う": "ベンチマーク・評価指標の偏りや欠陥を示す，新しい評価方法を作る",
    "状況の変化": "計算資源・大規模モデル・新しいデータにより，以前は不可能だった方法を見直す",
    "実験の違和感": "自分の実験で予想と異なった結果・説明できない挙動を掘り下げる",
    "対話": "議論・質疑・査読で受けた質問や指摘を出発点にする",
}
IDEA_REQUIRED_FIELDS = ["観点", "内容", "根拠", "何が新しいか", "なぜ重要か", "成功の基準", "最小の検証実験", "捨てる条件"]
NOTE_IDEA_SECTIONS = {"限界": "限界・未解決の点", "前提としている仮定": "前提としている仮定", "読んで浮かんだ疑問": "読んで浮かんだ疑問"}
REPORT_KEYWORDS = ["予想と異な", "予想と違", "想定外", "違和感", "未解決", "次に検討"]
LENS_PROMPTS = {
    "極端化・単純化": [
        "主要な手法のハイパーパラメータ（例: 正則化の重み，次元数，データ量）を 0 や無限大に近づけると，何が起きると予想されるか．",
        "主要な手法から部品を1つずつ取り除いたとき，性能を支えているのはどの部品か．文献に除去実験（ablation）はあるか．",
    ],
    "他分野からの持ち込み": [
        "この問題を最適化，統計，情報理論，物理，信号処理の言葉で言い換えると，どの既存の道具が使えそうか．",
        "似た構造の問題を扱っている別の分野はどこか．そこで標準的な手法は何か．",
    ],
    "現実の問題から逆算": [
        "応用先の制約（計算資源，ラベルの有無，説明可能性，プライバシー，実時間性）のうち，既存手法が想定していないものは何か．",
    ],
    "理論と実践のずれ": [
        "理論的な保証が無いのに実際には効く手法，または理論上は効くはずなのに効かない手法はあるか．",
    ],
    "評価を疑う": [
        "主要な文献の評価は同じデータセット・指標に偏っていないか．その指標で測れていない性質は何か．",
    ],
    "状況の変化": [
        "計算資源・大規模モデル・新しいデータセットの登場により，過去に見送られた方法を再検討できないか．",
    ],
    "対話": [
        "`surveykit.py memo` に記録した，議論・質疑・指導で受けた質問や指摘はあるか．",
    ],
}


def markdown_sections(text, keywords):
    """見出しにいずれかの語を含む節の本文を抽出する（同じ深さ以上の次の見出しまで）．

    Args:
        text (str): マークダウン．
        keywords (list[str]): 見出しに含まれる語．

    Returns:
        list[tuple[str, str]]: (見出し, 本文) のリスト．
    """
    lines = text.split("\n")
    sections = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match or not any(keyword in match.group(2) for keyword in keywords):
            continue
        level = len(match.group(1))
        body = []
        for following in lines[index + 1:]:
            next_match = re.match(r"^(#{1,6})\s", following)
            if next_match and len(next_match.group(1)) <= level:
                break
            body.append(following)
        sections.append((match.group(2).strip(), "\n".join(body).strip()))
    return sections


def note_idea_material(root, library):
    """採録した文献のノートから，限界・前提・疑問の記述を集める．

    Args:
        root (Path): プロジェクトルート．
        library (Library): 文献ライブラリ．

    Returns:
        dict[str, list[tuple[str, str]]]: 節の種類をキー，(引用キー, 本文) のリストを値とする辞書．
    """
    material = {name: [] for name in NOTE_IDEA_SECTIONS}
    for key, entry in sorted(library.entries.items()):
        path = note_path(root, key)
        if entry["status"] != "included" or not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for name, keyword in NOTE_IDEA_SECTIONS.items():
            for _, body in markdown_sections(text, [keyword]):
                if body and TODO_MARK not in body:
                    material[name].append((key, body))
    return material


def report_idea_material(root, max_chars=1500):
    """ai-dev-kit の `.reports/report_*.md` から，予想と異なった結果・未解決の論点の節を集める．

    Args:
        root (Path): プロジェクトルート．
        max_chars (int): 1つの節から取り出す最大の文字数．

    Returns:
        list[tuple[str, str, str]]: (ファイル名, 見出し, 本文) のリスト．
    """
    material = []
    for path in sorted((root / ".reports").glob("report_*.md")):
        for heading, body in markdown_sections(path.read_text(encoding="utf-8", errors="ignore"), REPORT_KEYWORDS):
            if body:
                material.append((path.name, heading, body[:max_chars] + (" …" if len(body) > max_chars else "")))
    return material


def memo_path(root):
    """気づきのメモのパスを返す．

    Args:
        root (Path): プロジェクトルート．

    Returns:
        Path: `survey/memo.md`．
    """
    return survey_dir(root) / "memo.md"


def command_memo(root, text, source="", lens=None):
    """研究の種になる気づき（疑問，違和感，受けた指摘等）を `survey/memo.md` に追記する．

    Args:
        root (Path): プロジェクトルート．
        text (str): 気づきの内容．
        source (str): 出どころ（例: "ゼミでの質問"，"report_012"）．
        lens (str | None): 対応する観点（IDEA_LENSES のキー）．

    Returns:
        None
    """
    if lens is not None and lens not in IDEA_LENSES:
        raise SurveyKitError(f"観点は {list(IDEA_LENSES)} のいずれかを指定してください: {lens}")
    path = memo_path(root)
    if not path.exists():
        write_text(path, "# 気づきのメモ\n\n研究の種になる疑問・違和感・受けた指摘を記録する．`surveykit.py ideas` がアイデアの材料として集める．\n\n")
    tags = " ".join(part for part in (f"[{lens}]" if lens else "", f"（{source}）" if source else "") if part)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"- {datetime.date.today().isoformat()} {tags} {text}".replace("  ", " ").rstrip() + "\n")
    print(f"[memo] 追記しました: {text}")


def idea_sources_markdown(root, library):
    """アイデアの材料を観点ごとにまとめたマークダウンを作成する．

    Args:
        root (Path): プロジェクトルート．
        library (Library): 文献ライブラリ．

    Returns:
        str: マークダウン．
    """
    lines = ["# アイデアの材料", "",
             "`surveykit.py ideas` が生成するファイルである．ここにある材料を `ideation.md` の観点で検討し，"
             "`survey_report.md` の「7. 改善の候補」を書く．材料は手がかりであり，候補にするかどうかは検討して決める．", ""]

    lines += ["## 空白（特徴行列）", ""]
    features = load_features(root)
    if features:
        rows = matrix_rows(root, library)
        gaps = find_gaps(rows, features)
        names = {f["id"]: f["name"] for f in features}
        found = False
        for fid in gaps["none"]:
            lines.append(f"- 「{names[fid]}」を満たす既存手法がない．")
            found = True
        for first, second in gaps["pairs"]:
            lines.append(f"- 「{names[first]}」と「{names[second]}」を両立する既存手法がない．")
            found = True
        ours = next((row for row in rows if row["ours"]), None)
        if ours:
            weak = [f["name"] for f in features if f["kind"] == "binary" and cell_status(ours, f)[0] in ("no", "partial")]
            if weak:
                lines.append(f"- 本研究が満たさない・部分的にしか満たさない特徴: {', '.join(weak)}（本研究の改善の候補）．")
                found = True
        if not found:
            lines.append("- 既存手法の組み合わせで満たされない特徴・組はない．特徴の選び方を見直す余地がある．")
    else:
        lines.append("- 特徴が定義されていない（`surveykit.py feature add`）．")

    material = note_idea_material(root, library)
    titles = {"前提としている仮定": "前提を壊す（ノートの「前提としている仮定」）",
              "限界": "限界（ノートの「限界・未解決の点」）",
              "読んで浮かんだ疑問": "疑問（ノートの「読んで浮かんだ疑問」）"}
    for name in ("前提としている仮定", "限界", "読んで浮かんだ疑問"):
        lines += ["", f"## {titles[name]}", ""]
        if not material[name]:
            lines.append("- 記入されたノートがない．")
        for key, body in material[name]:
            lines.append(f"### [@{key}]")
            lines += ["", body, ""]

    lines += ["", "## 実験の違和感（`.reports/` の予想と異なった結果・未解決の論点）", ""]
    reports = report_idea_material(root)
    if not reports:
        lines.append("- 該当する report がない．")
    for name, heading, body in reports:
        lines += [f"### {name}: {heading}", "", body, ""]

    lines += ["", "## 気づきのメモ（`survey/memo.md`）", ""]
    memo = memo_path(root)
    memo_lines = [line for line in memo.read_text(encoding="utf-8").split("\n") if line.startswith("- ")] if memo.exists() else []
    lines += memo_lines or ["- メモがない（`surveykit.py memo \"...\"` で追記できる）．"]

    lines += ["", "## 検討のための問い（自動で集められない観点）", ""]
    for lens, prompts in LENS_PROMPTS.items():
        lines.append(f"### {lens}")
        lines += [""] + [f"- {prompt}" for prompt in prompts] + [""]
    return "\n".join(lines).rstrip() + "\n"


def command_ideas(root, out=None):
    """アイデアの材料を集めて `survey/idea_sources.md` を作成する．

    Args:
        root (Path): プロジェクトルート．
        out (Path | None): 出力先．None の場合は `survey/idea_sources.md`．

    Returns:
        str: 作成した内容．
    """
    text = idea_sources_markdown(root, Library(root))
    out = out or survey_dir(root) / "idea_sources.md"
    write_text(out, text)
    print(f"[ideas] 作成: {out.relative_to(root) if out.is_relative_to(root) else out}")
    return text


def parse_idea_candidates(report_text):
    """調査報告の「改善の候補」の節から，候補の小節を解析する．

    Args:
        report_text (str): `survey_report.md` の内容．

    Returns:
        list[dict]: 各候補は "title" と，"- 項目: 値" の行から得た項目の辞書 "fields" を持つ．節が無い場合は空のリスト．
    """
    sections = markdown_sections(report_text, ["改善の候補"])
    if not sections:
        return []
    candidates = []
    for block in re.split(r"^###\s+", sections[0][1], flags=re.MULTILINE)[1:]:
        title, _, body = block.partition("\n")
        fields = {}
        for line in body.split("\n"):
            match = re.match(r"^\s*[-*]\s*([^:：]+)[:：]\s*(.*)$", line)
            if match:
                fields[match.group(1).strip()] = match.group(2).strip()
        candidates.append({"title": title.strip(), "fields": fields})
    return candidates


def collect_idea_issues(root):
    """改善の候補とアイデアの材料に関する問題を収集する．

    Args:
        root (Path): プロジェクトルート．

    Returns:
        tuple[list[str], list[str]]: (エラー, 警告)．
    """
    errors, warnings = [], []
    report = survey_dir(root) / "survey_report.md"
    if not (survey_dir(root) / "idea_sources.md").exists():
        warnings.append("idea_sources.md がありません（`surveykit.py ideas` でアイデアの材料を集めてください）．")
    if not report.exists():
        return errors, warnings

    text = report.read_text(encoding="utf-8")
    if not markdown_sections(text, ["改善の候補"]):
        errors.append("survey_report.md に「改善の候補」の節がありません．")
        return errors, warnings
    candidates = parse_idea_candidates(text)
    if len(candidates) < 3:
        warnings.append(f"改善の候補が {len(candidates)} 件です．3〜6件を目安に挙げてください．")

    lenses = set()
    for candidate in candidates:
        name = candidate["title"]
        fields = candidate["fields"]
        missing = [field for field in IDEA_REQUIRED_FIELDS if not fields.get(field)]
        if missing:
            errors.append(f"改善の候補「{name}」に項目がありません: {', '.join(missing)}")
        lens = fields.get("観点", "")
        if lens and TODO_MARK not in lens:
            if lens not in IDEA_LENSES:
                errors.append(f"改善の候補「{name}」の観点が不正です: {lens}（{' / '.join(IDEA_LENSES)}）")
            else:
                lenses.add(lens)
        evidence = fields.get("根拠", "")
        if evidence and TODO_MARK not in evidence and lens != "対話" and not re.search(r"\[@|feature_matrix|特徴行列|report_|memo", evidence):
            warnings.append(f"改善の候補「{name}」の根拠に，文献 [@引用キー]・特徴行列・report・メモへの参照がありません．")
    if candidates and len(lenses) < 3:
        warnings.append(f"改善の候補の観点が {len(lenses)} 種類です．少なくとも3種類の観点から検討してください（`ideation.md`）．")
    return errors, warnings


# =====================================================================================
# CLI
# =====================================================================================

def build_parser():
    """コマンドライン引数のパーサを作成する．

    Returns:
        argparse.ArgumentParser: パーサ．
    """
    parser = argparse.ArgumentParser(description="検証済みの文献ライブラリを作成する文献調査のCLI")
    parser.add_argument("--root", default=".", help="プロジェクトルート（既定: カレントディレクトリ）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="survey/ を初期化する")
    p.add_argument("--theme", required=True, help="調査のテーマ")

    p = sub.add_parser("search", help="文献を検索し，候補として登録する")
    p.add_argument("query", help="検索語（英語を推奨）")
    p.add_argument("--source", choices=["openalex", "arxiv"], default="openalex")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--from-year", type=int)
    p.add_argument("--to-year", type=int)
    p.add_argument("--sort", choices=["relevance", "cited", "date"], default="relevance",
                   help="OpenAlex: relevance/cited，arXiv: relevance/date")
    p.add_argument("--no-add", action="store_true", help="ライブラリに登録せずに表示のみ行う")

    p = sub.add_parser("add", help="DOI・arXiv ID・OpenAlex ID・タイトルから文献を登録する")
    p.add_argument("--doi")
    p.add_argument("--arxiv")
    p.add_argument("--openalex")
    p.add_argument("--title")
    p.add_argument("--status", choices=STATUSES)

    p = sub.add_parser("snowball", help="参考文献・被引用文献をたどって候補を登録する")
    p.add_argument("key")
    p.add_argument("--direction", choices=["references", "citations", "both"], default="both")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("set", help="文献の状態・理由・タグを設定する")
    p.add_argument("keys", nargs="+")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--reason")
    p.add_argument("--tag", action="append", dest="tags")

    p = sub.add_parser("list", help="文献を一覧表示する")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--tag")
    p.add_argument("--sort", choices=["year", "cited", "key"], default="year")

    p = sub.add_parser("show", help="文献の詳細と要旨を表示する")
    p.add_argument("key")

    p = sub.add_parser("note", help="文献のノートをテンプレートから作成する")
    p.add_argument("keys", nargs="+")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("table", help="採録文献の比較表を作成する")
    p.add_argument("--fields", nargs="+", help=f"列にする項目（既定: {' '.join(NOTE_FIELDS)}）")
    p.add_argument("--out", help="出力先（既定: survey/comparison.md）")

    p = sub.add_parser("feature", help="特徴行列の特徴（比較の観点）を定義・表示する")
    feature_sub = p.add_subparsers(dest="feature_command", required=True)
    q = feature_sub.add_parser("add", help="特徴を定義する")
    q.add_argument("feature_id", help="特徴のID（例: closed_form）")
    q.add_argument("--name", required=True, help="表の見出しにする名前")
    q.add_argument("--definition", required=True, help="満たす・満たさないを判断できる定義")
    q.add_argument("--kind", choices=["binary", "text"], default="binary", help="binary: ✓/△/✗，text: 値を記入")
    feature_sub.add_parser("list", help="定義済みの特徴を表示する")

    p = sub.add_parser("ours", help="本研究の特徴を記入する survey/ours.md を作成する")
    p.add_argument("--name", default=OURS_LABEL, help="特徴行列に表示する本研究の名前")

    p = sub.add_parser("matrix", help="特徴行列（手法 × 特徴）を作成する")
    p.add_argument("--format", choices=["md", "tex"], default="md", dest="fmt")
    p.add_argument("--transpose", action="store_true", help="行を特徴，列を手法にする")
    p.add_argument("--out", help="出力先（既定: md は survey/feature_matrix.md，tex は標準出力）")

    p = sub.add_parser("ideas", help="アイデアの材料（空白，前提，限界，疑問，実験の違和感，メモ）を集める")
    p.add_argument("--out", help="出力先（既定: survey/idea_sources.md）")

    p = sub.add_parser("memo", help="研究の種になる気づきを survey/memo.md に追記する")
    p.add_argument("text", help="気づきの内容")
    p.add_argument("--source", default="", help="出どころ（例: ゼミでの質問，report_012）")
    p.add_argument("--lens", help="対応する観点（例: 実験の違和感）")

    p = sub.add_parser("bib", help="参考文献リストを出力する")
    p.add_argument("--format", choices=["bibtex", "bibitem"], default="bibtex", dest="fmt")
    p.add_argument("--status", choices=STATUSES + ["all"], default="included")
    p.add_argument("--cited-in", nargs="+", help="これらのファイルで引用されている文献のみを引用順に出力する")
    p.add_argument("--out", help="出力先（既定: 標準出力）")

    p = sub.add_parser("cite-check", help="TeX・Markdown の引用を検証する")
    p.add_argument("paths", nargs="+", help="検証するファイルまたはディレクトリ")
    p.add_argument("--bib", nargs="+", help="参考文献の定義（.bib または \\bibitem を含む .tex）")

    sub.add_parser("check", help="調査全体を検証する")
    sub.add_parser("doctor", help="各 API に接続できるかを確認する")
    return parser


def main(argv=None):
    """CLI のエントリポイント．

    Args:
        argv (list[str] | None): コマンドライン引数．

    Returns:
        int: 終了コード．
    """
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()

    def to_path(value):
        path = Path(value)
        return path if path.is_absolute() else root / path

    try:
        if args.command == "init":
            command_init(root, args.theme)
        elif args.command == "search":
            command_search(root, args.query, args.source, args.limit, args.from_year, args.to_year, args.sort, not args.no_add)
        elif args.command == "add":
            command_add(root, args.doi, args.arxiv, args.openalex, args.title, args.status)
        elif args.command == "snowball":
            command_snowball(root, args.key, args.direction, args.limit)
        elif args.command == "set":
            command_set(root, args.keys, args.status, args.reason, args.tags)
        elif args.command == "list":
            command_list(root, args.status, args.tag, args.sort)
        elif args.command == "show":
            command_show(root, args.key)
        elif args.command == "note":
            for key in args.keys:
                command_note(root, key, args.force)
        elif args.command == "table":
            command_table(root, args.fields, to_path(args.out) if args.out else None)
        elif args.command == "feature":
            if args.feature_command == "add":
                command_feature_add(root, args.feature_id, args.name, args.definition, args.kind)
            else:
                command_feature_list(root)
        elif args.command == "ours":
            command_ours(root, args.name)
        elif args.command == "matrix":
            command_matrix(root, args.fmt, args.transpose, to_path(args.out) if args.out else None)
        elif args.command == "ideas":
            command_ideas(root, to_path(args.out) if args.out else None)
        elif args.command == "memo":
            command_memo(root, args.text, args.source, args.lens)
        elif args.command == "bib":
            command_bib(root, args.fmt, args.status, [to_path(p) for p in args.cited_in] if args.cited_in else None,
                        to_path(args.out) if args.out else None)
        elif args.command == "cite-check":
            return 1 if command_cite_check(root, [to_path(p) for p in args.paths],
                                           [to_path(p) for p in args.bib] if args.bib else None) else 0
        elif args.command == "check":
            return 1 if command_check(root) else 0
        elif args.command == "doctor":
            return 1 if command_doctor() else 0
    except SurveyKitError as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
