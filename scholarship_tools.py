"""獎學金推薦：從法規問答收錄的文件（data/）整理出獎學金、助學金、獎勵金的申請資格，再拿學生
自己的學制、年級、系所、成績跟排名比對，列出看起來可以申請的（agent 工具 recommend_scholarships_for_me）。

- 整理資格（extract_scholarships）：每份獎學金文件呼叫一次模型，把資格拆成欄位（學制年級、系所、
  成績與排名門檻、身分條件等）。結果跟文件卡片一樣以文件內容的 hash 快取在 storage/rag/scholarships/，
  文件沒變就不會重做。改了整理的 prompt 或欄位要把 SCHOLARSHIP_VERSION 加一，舊快取才會失效。
- 比對（evaluate）：查詢時只比對、不呼叫模型，同一份成績每次結果都一樣，每個條件怎麼判斷的也會列給
  使用者看。清寒、原住民這類成績單看不出來的條件列成「要確認」，使用者在對話裡自己說了才算符合。

同一個獎學金常常出現在好幾份文件裡（辦法、學務處的〈校內獎學金一覽〉、每學期的〈各項獎學金一覽表〉），
資格以辦法為準，金額、名額跟這學期的截止日以最新的一覽表為準（見 merge_entries）。

第一次要整理一百多份文件（約 5 分鐘），先跑一次，不然第一個查詢要等模型整理完：

    python scholarship_tools.py          # 加 --list 會列出整理好的每一項獎學金，檢查整理得對不對

之後每週的文件更新（update_documents.py）會順便整理新增、改版的文件。
"""

import hashlib
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Optional, get_args

from dotenv import load_dotenv
from openai import OpenAI

import academic_agent
import rag_documents
from academic_tools import course_failed, graded_cumulative_ranks
from logging_config import make_print_logger

print = make_print_logger(__name__)

load_dotenv()

SCHOLARSHIP_VERSION = "1"
EXTRACT_MODEL = os.getenv("SCHOLARSHIP_MODEL", "gpt-5.4")
CACHE_DIR = rag_documents.CACHE_DIR / "scholarships"
MANIFEST_PATH = rag_documents.DATA_DIR / ".crawler_manifest.json"
# 獎學金辦法大多一兩千字，最長的（弱勢助學計畫、安心就學細則）也不到一萬字
EXTRACT_INPUT_MAX_CHARS = 24000

# 學校的線上申請系統（115-1〈各項獎學金一覽表〉：Portal → 學生服務 → 生活助學服務 → 獎助學金暨工讀管理系統）
APPLY_URL = "https://cis.ncu.edu.tw/Scholarship"

# 標題或檔名有這些字的文件才交給模型整理，申請表跟舊版不整理
CANDIDATE_RE = re.compile(r"獎學金|助學金|獎助|獎勵|補助|書卷獎|急難|清寒")

DEGREES = ("學士班", "碩士班", "博士班", "在職專班")
KINDS = ("獎學金", "助學金", "獎勵金", "補助", "急難救助")
BASES = ("前一學期", "前一學年", "前一學年每學期", "歷年", "未說明")
NATIONALITIES = ("不限", "本國籍", "外籍生", "陸生", "僑生")
CONDITION_KINDS = (
    "經濟弱勢", "原住民", "新住民子女", "身心障礙", "設籍地區", "運動表現",
    "幹部或服務", "競賽或研究表現", "語言檢定", "出國交流", "其他",
)
# 使用者可以在對話裡自己說明的身分（agent 工具的 statuses 參數）
DeclaredStatus = Literal["經濟弱勢", "原住民", "新住民子女", "身心障礙", "外籍生", "陸生", "僑生"]
DECLARABLE_STATUSES: tuple[str, ...] = get_args(DeclaredStatus)
FOREIGN_STATUSES = ("外籍生", "陸生", "僑生")

# 獎學金文件常用的學院簡稱（系所的別名在 academic_hierarchy.json）
COLLEGE_ALIASES = {
    "電機資訊學院": "資訊電機學院", "資電學院": "資訊電機學院", "電資學院": "資訊電機學院",
    "地科學院": "地球科學學院", "生醫學院": "生醫理工學院", "永續學院": "永續與綠能科技研究學院",
}

_client: Optional[OpenAI] = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


# ============================================================
# 整理資格：每份文件呼叫一次模型
# ============================================================
def _object(properties: dict) -> dict:
    """strict 模式的 JSON schema：每個欄位都必填，不能有多的欄位。"""
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


_TEXT = {"type": "string"}
_BASIS = {"type": "string", "enum": list(BASES)}

SCHOLARSHIP_SCHEMA = _object({
    "name": _TEXT,
    "kind": {"type": "string", "enum": list(KINDS)},
    "amount": _TEXT,
    "quota": _TEXT,
    "groups": {"type": "array", "items": _object({
        "degree": {"type": "string", "enum": list(DEGREES)},
        "min_grade": {"type": "integer"},
        "max_grade": {"type": "integer"},
    })},
    "units": {"type": "array", "items": _TEXT},
    "grades": _object({"basis": _BASIS, "min_average": {"type": "number"}}),
    "rank": _object({
        "basis": _BASIS,
        "scope": {"type": "string", "enum": ["班", "系", "未說明"]},
        "percent": {"type": "number"},
        "top": {"type": "integer"},
    }),
    "no_failing": {"type": "boolean"},
    "conduct_min": {"type": "number"},
    "nationality": {"type": "string", "enum": list(NATIONALITIES)},
    "conditions": {"type": "array", "items": _object({
        "kind": {"type": "string", "enum": list(CONDITION_KINDS)},
        "text": _TEXT,
    })},
    "eligibility": _TEXT,
    "period": _TEXT,
    "deadline": _TEXT,
    "how_to_apply": _TEXT,
    "notes": _TEXT,
})

EXTRACTION_SCHEMA = _object({
    "document_kind": {"type": "string", "enum": ["辦法", "一覽表", "其他"]},
    "term": _TEXT,
    "scholarships": {"type": "array", "items": SCHOLARSHIP_SCHEMA},
})

EXTRACT_INSTRUCTIONS = """你要從中央大學的文件裡整理出學生可以申請（或學校會主動頒給學生）的獎學金、助學金、獎勵金，
給「獎學金推薦」功能用：系統會拿學生的學制、年級、系所、成績跟排名自動比對資格，所以資格要拆成欄位。
以文件內容為準，文件沒寫的欄位填空字串、0 或空陣列，不要猜。

要列的：
- 學生拿得到錢的獎學金、助學金、獎助學金、獎勵金（例如通過英檢、競賽得獎）、補助（例如出國研修、
  參加國際研討會）、急難救助金。
- 一份文件寫了好幾種（獎學金一覽表、同一份辦法裡大學部跟研究所分開的獎項）就分開列，一種一筆。
不要列的（文件只有這些就回傳空陣列）：
- 申請表、成果報告、獲獎名單這類沒有說明獎學金本身的文件。
- 給教師、助理、計畫主持人、課程的獎勵或經費，工讀、研究獎助生的聘用規定。
- 泛指一類、沒有具體名稱的項目（例如「各縣市政府、財團法人及私人企業等獎學金」）。

欄位：
- document_kind：辦法（辦法、要點、說明，講一種或少數幾種）、一覽表（列出很多種獎學金的清單）、其他。
- term：一覽表標明的學年度學期，寫成「115-1」這種格式；不是一覽表或沒寫就空字串。
- name：獎學金的完整名稱，照文件的寫法，不要加「國立中央大學」「辦法」「要點」這類字。系所自己的獎學金
  要帶出系所（例如「物理學系研究生獎助學金」），不能只寫「研究生獎助學金」。同一個獎學金分成好幾種獎項時，
  寫成「獎學金名稱（獎項）」，例如「朱順一合勤獎學金（學業優良）」「朱順一合勤獎學金（運動績優）」。
  同一個獎學金對不同學制有不同的門檻時（例如大學部平均 80 分、碩士班 85 分），也依學制分開列，寫成
  「羅家倫校長紀念獎學金（學士班）」「羅家倫校長紀念獎學金（碩士班）」。
- kind：獎學金（看成績或表現）、助學金（看經濟狀況）、獎勵金（達成某件事就發）、補助（出國、研討會等經費）、
  急難救助。
- amount、quota：金額、名額，照文件寫（例如「每名 10 萬元」「大學部 3 名、研究所 3 名」）。
- groups：可以申請的學制跟年級。degree 是學士班、碩士班、博士班、在職專班其中一個，min_grade、max_grade
  是那個學制的年級範圍，0 代表不限（「大二以上」→ 學士班 2～0，「碩二」→ 碩士班 2～2，「大學部」→ 學士班
  0～0，「研究生」→ 碩士班跟博士班各一筆）。在職專班要文件明寫可以申請才列。不限學制就給空陣列。
- units：限定的學院或系所，用最後面對照表裡的正式名稱（學院就寫學院名稱）。全校都可以申請就給空陣列。
- grades：學業成績門檻。basis 是成績的計算期間：前一學期、前一學年（學年平均）、前一學年每學期（上下學期
  都要達到）、歷年（累計）、未說明。min_average 是平均分數門檻，沒有就 0。
- rank：排名門檻。scope 是班或系，沒說就未說明。percent 是前百分之幾，top 是前幾名，沒有就 0。basis 同 grades。
- no_failing：要求各科都及格、沒有不及格的科目時是 true。
- conduct_min：操行成績門檻，沒有就 0。
- nationality：限本國籍（中華民國國籍）、限外籍生、限陸生、限僑生，或不限。
- conditions：成績單上查不到、要學生自己確認的其他資格，每項給 kind 跟 text。kind：經濟弱勢（清寒、低收入戶、
  中低收入戶、特殊境遇家庭、經濟困難，或要先具備安心就學支持計畫、弱勢助學計畫、學雜費減免、安心學習助學金
  這類弱勢資格）、原住民、新住民子女、身心障礙、設籍地區、運動表現、幹部或服務、
  競賽或研究表現、語言檢定、出國交流、其他。text 用簡短的話寫出條件本身（例如「低收入戶或中低收入戶」
  「設籍彰化縣」「多益 785 分以上」）。下面這些不是要確認的資格，不要列在 conditions：
  - 優先條件（「家境清寒者優先」）、排除條件（「不得有全職工作」「未享有其他獎學金」）、獲獎後的義務
    （繳交心得、參加活動、與導師晤談），寫在 notes。
  - 幾乎每個學生都符合的一般條件（在學學生、正式學籍、未受處分、無懲處紀錄、不含延長修業學生）。
  - 沒有具體標準的形容（品學兼優、優秀學生、品行良好、學業表現優異、熱心服務）。
  一覽表裡「類型」欄是「清寒」的每一列，都要列一項經濟弱勢（申請資格欄是空的也一樣）。文件完全沒寫申請資格
  （例如只寫「實際資訊以來文公告為主」）時，再列一項 kind 其他、text「申請資格依公告」。
- eligibility：一句話摘要申請資格，給學生看的。
- period：申請期間（例如「每年 9 月」「每學期開學後一個月內」）。deadline：一覽表上寫的這學期截止日
  （例如「10/2」），照抄，沒有就空字串。
- how_to_apply：怎麼申請、送到哪個單位，一句話。學校主動核發、不用申請的寫「免申請，學校主動核發」。
- notes：其他重要限制（不得同時領其他獎學金、優先條件、獲獎後的義務），沒有就空字串。
- 全部用繁體中文。

系所與學院對照（units 用這裡的正式名稱）：
{departments}"""


def candidate_documents(catalog: list[rag_documents.CatalogDocument]) -> list[rag_documents.CatalogDocument]:
    return [
        doc for doc in catalog
        if not doc.superseded_by
        and doc.card["doc_type"] != "申請表單"
        and CANDIDATE_RE.search(f"{doc.card['title']} {doc.file_name}")
    ]


def extract_scholarships(doc: rag_documents.CatalogDocument) -> dict:
    text = doc.text
    if len(text) > EXTRACT_INPUT_MAX_CHARS:
        text = text[:EXTRACT_INPUT_MAX_CHARS] + "\n（以下省略）"
    response = _openai().chat.completions.create(
        model=EXTRACT_MODEL,
        reasoning_effort="low",
        messages=[
            {"role": "system", "content": EXTRACT_INSTRUCTIONS.format(departments=academic_agent.department_directory())},
            {"role": "user", "content": f"文件標題：{doc.card['title']}\n檔名：{doc.file_name}\n\n文件內容：\n{text}"},
        ],
        response_format={"type": "json_schema", "json_schema": {"name": "scholarships", "schema": EXTRACTION_SCHEMA, "strict": True}},
    )
    return json.loads(response.choices[0].message.content)


def _cache_path(doc: rag_documents.CatalogDocument) -> Path:
    """跟文件卡片一樣用內容的 hash 當 key（同一份辦法放在不同單位網站上只整理一次）。"""
    key = hashlib.sha256(re.sub(r"\s", "", doc.text).encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{key}.json"


def cached_extraction(doc: rag_documents.CatalogDocument) -> Optional[dict]:
    try:
        cached = json.loads(_cache_path(doc).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cached["result"] if cached.get("version") == SCHOLARSHIP_VERSION else None


def _extract_and_cache(doc: rag_documents.CatalogDocument) -> Optional[dict]:
    try:
        result = extract_scholarships(doc)
    except Exception as e:
        print(f"[獎學金] {doc.file_name} 整理失敗：{e}")
        return None
    path = _cache_path(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"version": SCHOLARSHIP_VERSION, "file_name": doc.file_name, "result": result}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    tmp.replace(path)
    return result


def load_extractions(docs: list, max_workers: int = 8) -> list[tuple[rag_documents.CatalogDocument, dict]]:
    """每份文件整理好的資格，還沒整理過的現在整理（整理失敗的先略過，下次再試）。"""
    results = {doc.doc_id: cached_extraction(doc) for doc in docs}
    missing = [doc for doc in docs if results[doc.doc_id] is None]
    if missing:
        print(f"[獎學金] 有 {len(missing)} 份文件還沒整理過獎學金資格，開始整理...")
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for doc, result in zip(missing, pool.map(_extract_and_cache, missing)):
                results[doc.doc_id] = result
    return [(doc, results[doc.doc_id]) for doc in docs if results[doc.doc_id] is not None]


# ============================================================
# 合併：同一個獎學金出現在好幾份文件
# ============================================================
# 「獎學金名稱（獎項）」結尾的獎項；名稱中間的括號（「修習全英語授課(EMI)課程獎勵」）不算
_SUB_AWARD_RE = re.compile(r"[（(]([^（）()]*)[）)]\s*$")
_NAME_NOISE_RE = re.compile(r"[\s「」『』“”\"'()（）【】《》〈〉\-－—_、,，.。:：]")
_NAME_PREFIX_RE = re.compile(r"^(國立中央大學|中央大學|國立|本校)")
_RULE_WORDS_RE = re.compile(r"(實施|設置|申請|發給|發放|審核|獎勵|施行|處理|作業)?(辦法|要點|細則|簡則|準則|規則)(草案)?")


def scholarship_key(name: str, base: bool = False) -> str:
    """同一個獎學金在不同文件的寫法轉成一樣的 key，例如「國立中央大學羅家倫校長紀念獎學金辦法」跟一覽表的
    「羅家倫校長紀念獎學金」、辦法的「宏惠光電股份有限公司獎助學金」跟一覽表的「宏惠光電股份有限公司獎助金」。
    base=True 時拿掉括號裡的獎項（「朱順一合勤獎學金（學業優良）」→ 跟「朱順一合勤獎學金」一樣）。"""
    text = rag_documents.normalize_text(name).replace("奬", "獎").replace("昇", "升")
    if base:
        text = _SUB_AWARD_RE.sub("", text)
    text = _NAME_NOISE_RE.sub("", text)
    text = _NAME_PREFIX_RE.sub("", text)
    text = _RULE_WORDS_RE.sub("", text)
    return re.sub(r"獎助學金|獎助金|獎學金", "獎", text)


def resolve_unit(text: str) -> tuple[str, str]:
    """系所或學院的名稱（可以是別名，或「資訊電機學院資訊工程學系」這種學院加系所）→ (正式名稱, 所屬學院)。
    學院本身回傳 (學院, 學院)，認不出來回傳 ("", "")。"""
    text = re.sub(r"\s", "", text or "")
    for alias, college in COLLEGE_ALIASES.items():
        text = text.replace(alias, college)
    aliases = academic_agent.unit_aliases()
    for alias, name, college in aliases:
        if alias in text:
            return name, college
    for college in sorted({college for _, _, college in aliases if college}, key=len, reverse=True):
        if college in text:
            return college, college
    return "", ""


def _units_key(entry: dict) -> tuple[str, ...]:
    return tuple(sorted({resolve_unit(u)[0] or u for u in entry["units"] if u.strip()}))


def _term_order(term: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{2,3})-([12])", term or "")
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def merge_entries(rule_entries: list[dict], list_entries: list[dict]) -> list[dict]:
    """rule_entries 是辦法裡的獎學金，list_entries 是一覽表裡的（_term 是一覽表的學期）。

    - 名稱一樣、限定的系所也一樣的辦法合成一筆（同一份辦法放在好幾個單位網站上）。
    - 一覽表的項目併進同名的那筆，同名的有好幾筆（各系同名的獎學金）時併進系所一樣的。
    - 一覽表跟辦法的獎項拆法不一樣時（一覽表分「亞洲地區」「其他地區」，辦法沒分；或反過來），併進主名稱一樣的
      辦法，但只拿截止日（_exact 是 False），金額以辦法為準。
    - 都對不上就自己一筆（只出現在一覽表上的獎學金）。
    """
    groups: dict[tuple[str, tuple[str, ...]], list[dict]] = {}
    for entry in rule_entries:
        groups.setdefault((scholarship_key(entry["name"]), _units_key(entry)), []).append(entry)
    rule_bases = {key: scholarship_key(entries[0]["name"], base=True) for key, entries in groups.items()}
    for entry in sorted(list_entries, key=lambda e: _term_order(e["_term"]), reverse=True):
        key = (scholarship_key(entry["name"]), _units_key(entry))
        same_name = [k for k in groups if k[0] == key[0]]
        if key not in groups and len(same_name) == 1:
            key = same_name[0]
        if key not in groups:
            same_base = [k for k, b in rule_bases.items() if b == scholarship_key(entry["name"], base=True)]
            if same_base:
                for k in same_base:
                    groups[k].append({**entry, "_exact": False})
                continue
        groups.setdefault(key, []).append(entry)
    return [_merge_group(entries) for entries in groups.values()]


def _merge_group(entries: list[dict]) -> dict:
    rules = [e for e in entries if not e["_list"]]
    lists = sorted((e for e in entries if e["_list"]), key=lambda e: _term_order(e["_term"]), reverse=True)
    exact = [e for e in lists if e.get("_exact", True)]
    base = rules[0] if rules else lists[0]
    merged = {key: value for key, value in base.items() if not key.startswith("_")}
    for entry in rules[1:] + exact:
        for field in ("amount", "quota", "eligibility", "period", "how_to_apply", "notes"):
            if not merged[field] and entry[field]:
                merged[field] = entry[field]
    if not rules:
        # 只出現在一覽表上的：各份一覽表寫法不一樣時（一份寫「限清寒」、一份寫「清寒者優先」），
        # 身分條件取聯集，寧可多請使用者確認
        kinds = {c["kind"] for c in merged["conditions"]}
        for entry in lists[1:]:
            for condition in entry["conditions"]:
                if condition["kind"] not in kinds:
                    merged["conditions"] = merged["conditions"] + [condition]
                    kinds.add(condition["kind"])
    # 一覽表是每學期公告的，金額、名額、截止日以最新的一份為準
    if exact:
        for field in ("amount", "quota"):
            if exact[0][field]:
                merged[field] = exact[0][field]
    merged["deadline_term"] = ""
    dated = [e for e in lists if e["deadline"] and e["_term"]]
    if dated:
        merged["deadline"], merged["deadline_term"] = dated[0]["deadline"], dated[0]["_term"]
    else:
        # 辦法跟沒寫學期的一覽表，判斷不了截止日是哪一年，當成申請期間的說明
        undated = merged["deadline"] or next((e["deadline"] for e in lists if e["deadline"]), "")
        merged["period"] = merged["period"] or undated
        merged["deadline"] = ""
    sources = {}
    for entry in rules + lists:
        sources.setdefault(entry["_source"]["file"], entry["_source"])
    merged["sources"] = list(sources.values())
    return merged


def _sub_award(name: str) -> str:
    match = _SUB_AWARD_RE.search(rag_documents.normalize_text(name))
    return scholarship_key(match.group(1)) if match else ""


def _is_suffix(a: str, b: str) -> bool:
    short, long = sorted((a, b), key=len)
    return len(short) >= 5 and long.endswith(short)


def _bigram_similarity(a: str, b: str) -> float:
    grams_a = {a[i:i + 2] for i in range(len(a) - 1)}
    grams_b = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(grams_a & grams_b) / len(grams_a | grams_b) if grams_a | grams_b else 0.0


def similar_names(a: str, b: str) -> bool:
    """名稱寫法不同的同一個獎學金：
    - 一個是另一個前面多了單位或計畫名稱（「生活助學金」「學生生活助學金」、「安心學習助學金」
      「安心就學支持計畫安心學習助學金」）。
    - 獎項一樣、主名稱很像（「理學院提升學生外文能力獎勵（報名參加英語考試者）」「學生提昇外文能力獎勵
      （報名參加英語考試者）」）。獎項不一樣的（「…獎勵（多益）」「…獎勵（托福）」）不是同一個。
    """
    sub_a, sub_b = _sub_award(a), _sub_award(b)
    if sub_a and sub_b:
        if sub_a != sub_b:
            return False
        base_a, base_b = scholarship_key(a, base=True), scholarship_key(b, base=True)
        return _is_suffix(base_a, base_b) or _bigram_similarity(base_a, base_b) >= 0.5
    if sub_a or sub_b:
        return False
    return _is_suffix(scholarship_key(a), scholarship_key(b))


def dedupe(index: list[dict]) -> list[dict]:
    """merge_entries 靠名稱合併，名稱寫法差比較多的（不同單位網站放了不同版本、一覽表的簡稱）還會重複。
    限定的系所一樣、金額一樣（或其中一個沒寫金額），名稱又很像的，併成一筆（保留先出現的，通常是辦法）。"""
    kept: list[tuple[dict, tuple[str, ...], int]] = []
    for item in index:
        units, amount = _units_key(item), amount_value(item["amount"])
        for other, other_units, other_amount in kept:
            if (
                units == other_units
                and (not amount or not other_amount or amount == other_amount)
                and similar_names(item["name"], other["name"])
            ):
                for field in ("amount", "quota", "eligibility", "period", "how_to_apply", "notes"):
                    other[field] = other[field] or item[field]
                if not other["deadline"] and item["deadline"]:
                    other["deadline"], other["deadline_term"] = item["deadline"], item["deadline_term"]
                files = {src["file"] for src in other["sources"]}
                other["sources"] += [src for src in item["sources"] if src["file"] not in files]
                break
        else:
            kept.append((item, units, amount))
    return [item for item, _, _ in kept]


def _web_pages() -> dict[str, str]:
    """data/ 底下的檔名 → 學校網站上的頁面（爬蟲的紀錄：附檔記它所在的網頁，網頁快照記網頁本身）。"""
    try:
        files = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")).get("files", {})
    except (OSError, ValueError):
        return {}
    return {path: entry.get("page_url") or entry.get("url") or "" for path, entry in files.items()}


def build_index(catalog: list[rag_documents.CatalogDocument], max_workers: int = 8) -> list[dict]:
    """整理目錄裡所有的獎學金，同一個獎學金合併成一筆。"""
    pages = _web_pages()
    rule_entries, list_entries = [], []
    for doc, result in load_extractions(candidate_documents(catalog), max_workers):
        source = {"title": doc.card["title"], "file": doc.file_name, "url": pages.get(doc.file_name, "")}
        is_list = result["document_kind"] == "一覽表"
        for item in result["scholarships"]:
            entry = {**item, "_source": source, "_list": is_list, "_term": result["term"] if is_list else ""}
            (list_entries if is_list else rule_entries).append(entry)
    return dedupe(merge_entries(rule_entries, list_entries))


_index_cache: dict = {"catalog": None, "index": []}
_index_lock = threading.Lock()


def get_scholarship_index() -> list[dict]:
    """法規文件目錄沒變就沿用記憶體裡的；有新文件時只整理新的那幾份。"""
    catalog = academic_agent.get_catalog()
    with _index_lock:
        if _index_cache["catalog"] is not catalog:
            _index_cache.update(catalog=catalog, index=build_index(catalog))
        return _index_cache["index"]


# ============================================================
# 學生的資料（來自 iNCU 成績查詢，見 academic_tools.parse_transcript_html）
# ============================================================
@dataclass
class Semester:
    term: str          # "1142"
    label: str         # "114-2"
    average: float
    credits: int       # 修習學分，算學年平均的權重
    class_rank: str    # "5/52"，查不到是空字串
    dept_rank: str
    failed: list[str]  # 不及格的課（停修不算，見 _FAILING_REASONS）


@dataclass
class StudentProfile:
    department: str    # 正式名稱，認不出來時是成績單上的原文
    college: str       # 認不出來是空字串
    degree: str        # 學士班、碩士班、博士班、在職專班，看不出來是空字串
    grade: int         # 看不出來是 0
    semesters: list[Semester]  # 已經有學期平均的學期，舊的在前
    cumulative_average: Optional[float]
    cumulative_class_rank: str
    cumulative_dept_rank: str
    previous_year: int  # 「前一學年」是哪個學年度


_GRADE_NAMES = "一二三四五六七"
_GRADE_RE = re.compile(r"([一二三四五六七1-7])\s*年|[大碩博]([一二三四五六七])")


def parse_grade(text: str) -> int:
    """「三年A班」「碩一」→ 年級，看不出來回傳 0。"""
    match = _GRADE_RE.search(text or "")
    if not match:
        return 0
    ch = match.group(1) or match.group(2)
    return int(ch) if ch.isdigit() else _GRADE_NAMES.index(ch) + 1


def parse_degree(program: str) -> str:
    text = program or ""
    if "在職" in text:
        return "在職專班"
    for keyword, degree in (("博士", "博士班"), ("碩士", "碩士班"), ("學士", "學士班"), ("大學", "學士班")):
        if keyword in text:
            return degree
    return ""


# 辦法寫的「無不及格科目」只看有成績的課：停修（棄修）沒有成績，不是不及格
_FAILING_REASONS = ("不及格", "未通過")


def academic_year(today: date) -> int:
    """第 1 學期是 8 月到隔年 1 月（跟 academic_agent.today_context 一樣）。"""
    roc = today.year - 1911
    return roc if today.month >= 8 else roc - 1


def build_profile(transcript: dict, today: Optional[date] = None) -> StudentProfile:
    today = today or date.today()
    department, college = resolve_unit(transcript.get("department", ""))
    ranks = transcript.get("ranks", {})
    semester_ranks = ranks.get("semester", {})
    semesters = []
    for sem in transcript.get("semesters", []):
        if sem["term"][-1:] not in ("1", "2"):
            continue  # 暑修
        rank = semester_ranks.get(sem["term"], {})
        average = sem["summary"].get("average")
        if average is None:
            average = rank.get("average")
        if average is None:
            continue  # 這學期還沒有成績
        semesters.append(Semester(
            term=sem["term"],
            label=sem["label"],
            average=average,
            credits=sem["summary"].get("attempted_credits") or sum(c["credits"] for c in sem["courses"]),
            class_rank=rank.get("class_rank") or "",
            dept_rank=rank.get("dept_rank") or "",
            failed=[c["name"] for c in sem["courses"] if course_failed(c) in _FAILING_REASONS],
        ))
    cumulative_ranks = graded_cumulative_ranks(transcript)
    latest_rank = cumulative_ranks[-1][1] if cumulative_ranks else {}
    return StudentProfile(
        department=department or transcript.get("department", ""),
        college=college,
        degree=parse_degree(transcript.get("program", "")),
        grade=parse_grade(transcript.get("grade", "")),
        semesters=semesters,
        cumulative_average=transcript.get("cumulative_average"),
        cumulative_class_rank=latest_rank.get("class_rank") or "",
        cumulative_dept_rank=latest_rank.get("dept_rank") or "",
        previous_year=academic_year(today) - 1,
    )


# ============================================================
# 比對
# ============================================================
def _grade_name(grade: int) -> str:
    return _GRADE_NAMES[grade - 1] if 1 <= grade <= len(_GRADE_NAMES) else str(grade)


def describe_group(group: dict) -> str:
    degree, low, high = group["degree"], group["min_grade"], group["max_grade"]
    if low and high:
        return f"{degree}{_grade_name(low)}年級" if low == high else f"{degree}{_grade_name(low)}到{_grade_name(high)}年級"
    if low:
        return f"{degree}{_grade_name(low)}年級以上"
    if high:
        return f"{degree}{_grade_name(high)}年級以下"
    return degree


def _check_groups(groups: list[dict], profile: StudentProfile) -> Optional[dict]:
    if not groups:
        return None
    label = "限" + "、".join(dict.fromkeys(describe_group(g) for g in groups))
    if not profile.degree:
        return {"label": f"{label}（查不到你的學制）", "ok": None}
    label += f"（你：{profile.degree}{_grade_name(profile.grade) + '年級' if profile.grade else ''}）"
    same = [g for g in groups if g["degree"] == profile.degree]
    if not same:
        return {"label": label, "ok": False}
    if not profile.grade:
        return {"label": label, "ok": True if any(not g["min_grade"] and not g["max_grade"] for g in same) else None}
    ok = any(
        (not g["min_grade"] or profile.grade >= g["min_grade"]) and (not g["max_grade"] or profile.grade <= g["max_grade"])
        for g in same
    )
    return {"label": label, "ok": ok}


def _check_units(units: list[str], profile: StudentProfile) -> Optional[dict]:
    units = [u.strip() for u in units if u.strip()]
    if not units:
        return None
    resolved = [resolve_unit(u)[0] for u in units]
    label = "限" + "、".join(dict.fromkeys(name or raw for name, raw in zip(resolved, units)))
    label += f"（你：{profile.department or '查不到系所'}）"
    if profile.college and (profile.department in resolved or profile.college in resolved):
        return {"label": label, "ok": True}
    if not profile.college or not all(resolved):
        return {"label": label, "ok": None}
    return {"label": label, "ok": False}


def _semesters_for(profile: StudentProfile, basis: str) -> list[Semester]:
    if basis in ("前一學年", "前一學年每學期"):
        return [s for s in profile.semesters if s.term[:-1] == str(profile.previous_year)]
    if basis == "歷年":
        return list(profile.semesters)
    return profile.semesters[-1:]  # 前一學期、未說明：最近一個有成績的學期


def year_average(semesters: list[Semester]) -> float:
    """學年平均：各學期平均依修習學分加權。"""
    credits = sum(s.credits for s in semesters)
    if credits:
        return sum(s.average * s.credits for s in semesters) / credits
    return sum(s.average for s in semesters) / len(semesters)


def _check_grades(rule: dict, profile: StudentProfile) -> Optional[dict]:
    minimum, basis = rule["min_average"], rule["basis"]
    if not minimum:
        return None
    year = f"{profile.previous_year} 學年"
    if basis == "歷年":
        label = f"歷年平均 {minimum:g} 分以上"
        if profile.cumulative_average is None:
            return {"label": f"{label}（查不到你的歷年平均）", "ok": None}
        return {"label": f"{label}（你：{profile.cumulative_average:.2f}）", "ok": profile.cumulative_average >= minimum}
    semesters = _semesters_for(profile, basis)
    if basis == "前一學年":
        label = f"{year}平均 {minimum:g} 分以上"
        if not semesters:
            return {"label": f"{label}（查不到你 {year}的成績）", "ok": None}
        value = year_average(semesters)
        return {"label": f"{label}（你：{value:.2f}）", "ok": value >= minimum}
    if basis == "前一學年每學期":
        label = f"{year}上下學期平均都 {minimum:g} 分以上"
        if not semesters:
            return {"label": f"{label}（查不到你 {year}的成績）", "ok": None}
        you = "、".join(f"{s.label} {s.average:.2f}" for s in semesters)
        if any(s.average < minimum for s in semesters):
            return {"label": f"{label}（你：{you}）", "ok": False}
        return {"label": f"{label}（你：{you}）", "ok": True if len(semesters) >= 2 else None}
    label = f"前一學期平均 {minimum:g} 分以上"
    if not semesters:
        return {"label": f"{label}（查不到你的學期成績）", "ok": None}
    latest = semesters[0]
    return {"label": f"{label}（你：{latest.label} {latest.average:.2f}）", "ok": latest.average >= minimum}


def parse_rank(text: str) -> Optional[tuple[int, int]]:
    """「5/52」→ (5, 52)，格式不對回傳 None。"""
    match = re.match(r"\s*(\d+)\s*/\s*(\d+)", text or "")
    if not match or int(match.group(2)) == 0:
        return None
    return int(match.group(1)), int(match.group(2))


def _rank_ok(text: str, percent: float, top: int) -> Optional[bool]:
    parsed = parse_rank(text)
    if parsed is None:
        return None
    position, total = parsed
    return (not top or position <= top) and (not percent or position * 100 <= percent * total)


def _rank_text(text: str, percent: float) -> str:
    parsed = parse_rank(text)
    if parsed is None:
        return text or "查不到"
    position, total = parsed
    return f"{text}，前 {position * 100 / total:.1f}%" if percent else text


def _check_rank(rule: dict, profile: StudentProfile) -> Optional[dict]:
    percent, top, basis = rule["percent"], rule["top"], rule["basis"]
    if not percent and not top:
        return None
    scope = "系" if rule["scope"] == "系" else "班"
    target = "、".join(part for part in (f"前 {percent:g}%" if percent else "", f"前 {top} 名" if top else "") if part)
    if basis == "歷年":
        text = profile.cumulative_dept_rank if scope == "系" else profile.cumulative_class_rank
        return {"label": f"累計{scope}排名{target}（你：{_rank_text(text, percent)}）", "ok": _rank_ok(text, percent, top)}

    semesters = _semesters_for(profile, basis)
    if basis == "前一學年每學期":
        label = f"{profile.previous_year} 學年上下學期{scope}排名都在{target}"
    elif basis == "前一學年":
        label = f"{profile.previous_year} 學年{scope}排名{target}"
    else:
        label = f"前一學期{scope}排名{target}"
    ranks = [(s.label, s.dept_rank if scope == "系" else s.class_rank) for s in semesters]
    results = [_rank_ok(text, percent, top) for _, text in ranks]
    you = "、".join(f"{term} {_rank_text(text, percent)}" for term, text in ranks) or "查不到"

    if basis == "前一學年":
        # 成績單只有學期排名、沒有學年排名：上下學期都在門檻內就算符合，都不在就不符合，其他判斷不了
        if results and all(r is True for r in results):
            ok = True
        elif results and all(r is False for r in results):
            ok = False
        else:
            ok = None
    elif False in results:
        ok = False
    elif not results or None in results or (basis == "前一學年每學期" and len(results) < 2):
        ok = None
    else:
        ok = True
    return {"label": f"{label}（你：{you}）", "ok": ok}


def _check_no_failing(item: dict, profile: StudentProfile) -> Optional[dict]:
    if not item["no_failing"]:
        return None
    semesters = _semesters_for(profile, item["grades"]["basis"])
    if not semesters:
        return {"label": "各科都及格（查不到你的學期成績）", "ok": None}
    terms = "、".join(s.label for s in semesters) if len(semesters) <= 2 else "歷年"
    failed = [name for s in semesters for name in s.failed]
    if failed:
        return {"label": f"各科都及格（你：{terms}有不及格：{'、'.join(failed)}）", "ok": False}
    return {"label": f"各科都及格（你：{terms}都及格）", "ok": True}


def evaluate(item: dict, profile: StudentProfile, statuses: tuple[str, ...] = ()) -> tuple[str, list[dict], list[dict]]:
    """回傳 (結果, 判斷過的條件, 要使用者確認的條件)。結果是 eligible（條件都符合）、maybe（成績單判斷得了的
    都符合，但還有要確認的身分條件，或資料不夠判斷不了）、ineligible（有條件不符合）。"""
    checks: list[dict] = []
    nationality = item["nationality"]
    if nationality in FOREIGN_STATUSES:
        if nationality not in statuses:
            return "ineligible", [{"label": f"限{nationality}", "ok": False}], []
        checks.append({"label": f"限{nationality}（你說明過）", "ok": True})
    elif nationality == "本國籍" and set(statuses) & set(FOREIGN_STATUSES):
        return "ineligible", [{"label": "限本國籍", "ok": False}], []

    for check in (
        _check_groups(item["groups"], profile),
        _check_units(item["units"], profile),
        _check_grades(item["grades"], profile),
        _check_rank(item["rank"], profile),
        _check_no_failing(item, profile),
    ):
        if check is not None:
            checks.append(check)

    pending = []
    for condition in item["conditions"]:
        if condition["kind"] in statuses:
            checks.append({"label": f"{condition['text']}（你說明過是{condition['kind']}）", "ok": True})
        else:
            pending.append(condition)
    if not checks and not pending:
        # 一個條件都沒整理出來（一覽表只寫「依公告」的基金會獎學金），不能當成人人都符合
        pending.append({"kind": "其他", "text": "文件沒有寫明申請資格，要看辦法或公告"})

    if any(c["ok"] is False for c in checks):
        return "ineligible", checks, pending
    if pending or any(c["ok"] is None for c in checks):
        return "maybe", checks, pending
    return "eligible", checks, pending


def amount_value(text: str) -> int:
    """「10 萬」「100,000 元」「1年至多50萬」→ 金額（取最大的數字），排序用，看不出來是 0。"""
    best = 0
    for number, unit in re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*(萬|千)?", text or ""):
        value = float(number.replace(",", "")) * {"萬": 10000, "千": 1000}.get(unit, 1)
        best = max(best, int(value))
    return best


def deadline_date(deadline: str, term: str) -> Optional[date]:
    """一覽表的截止日（「10/2」）配上一覽表的學期（「115-1」）換成日期。第 1 學期的 1 月跟第 2 學期是隔年。"""
    day = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{1,2})\s*", deadline or "")
    order = _term_order(term)
    if not day or not order[0]:
        return None
    month = int(day.group(1))
    year = order[0] + 1911 + (1 if month == 1 or (order[1] == 2 and month < 8) else 0)
    try:
        return date(year, month, int(day.group(2)))
    except ValueError:
        return None


def _present(item: dict, status: str, checks: list[dict], pending: list[dict], today: date) -> dict:
    due = deadline_date(item["deadline"], item["deadline_term"])
    return {
        "name": item["name"],
        "kind": item["kind"],
        "amount": item["amount"],
        "quota": item["quota"],
        "eligibility": item["eligibility"],
        "period": item["period"],
        "deadline": item["deadline"],
        "deadline_term": item["deadline_term"],
        "deadline_passed": None if due is None else due < today,
        "how_to_apply": item["how_to_apply"],
        "notes": item["notes"],
        "conduct_min": item["conduct_min"],
        "status": status,
        "checks": checks,
        "pending": pending,
        "sources": item["sources"],
    }


def _profile_summary(profile: StudentProfile) -> dict:
    latest = profile.semesters[-1] if profile.semesters else None
    year = _semesters_for(profile, "前一學年")
    return {
        "department": profile.department,
        "college": profile.college,
        "degree": profile.degree,
        "grade": profile.grade,
        "latest_term": latest.label if latest else "",
        "latest_average": latest.average if latest else None,
        "latest_class_rank": latest.class_rank if latest else "",
        "previous_year": profile.previous_year,
        "year_average": round(year_average(year), 2) if year else None,
        "cumulative_average": profile.cumulative_average,
    }


def _sub_award_text(name: str) -> str:
    match = _SUB_AWARD_RE.search(name)
    return match.group(1).strip() if match else ""


def combine_tiers(entries: list[dict]) -> list[dict]:
    """同一個獎學金分成好幾個獎項（多益／托福／雅思、前 3%／前 10%），比對結果一樣、要確認的條件也一樣的，
    合成一張卡片，tiers 列出各獎項跟金額，不然英檢獎勵這種一個辦法就有十幾張卡片。
    有分獎項的跟沒分的同時出現時（不同文件的寫法），沒分的那筆是總稱，只留有分的。"""
    families: dict[tuple, list[dict]] = {}
    for entry in entries:
        families.setdefault(entry["_family"], []).append(entry)
    combined = []
    for members in families.values():
        split = [m for m in members if _sub_award_text(m["name"])]
        if split and len(split) < len(members):
            members = split
        if len(members) == 1:
            combined.append(members[0])
            continue
        first = members[0]
        common_checks = set.intersection(*({c["label"] for c in m["checks"]} for m in members))
        # 每個獎項自己的條件（多益幾分、托福幾分）寫在獎項旁邊，大家都要的才留在 pending 最前面
        common_pending = set.intersection(*({p["text"] for p in m["pending"]} for m in members))
        pending: dict[str, dict] = {}
        sources: dict[str, dict] = {}
        for member in members:
            for condition in member["pending"]:
                pending.setdefault(condition["text"], condition)
            for source in member["sources"]:
                sources.setdefault(source["file"], source)
        combined.append({
            **first,
            "name": _SUB_AWARD_RE.sub("", first["name"]).strip() or first["name"],
            "amount": "",
            "quota": "",
            "tiers": [
                {
                    "name": _sub_award_text(m["name"]),
                    "amount": m["amount"],
                    "conditions": [p["text"] for p in m["pending"] if p["text"] not in common_pending],
                }
                for m in members
            ],
            "checks": [c for c in first["checks"] if c["label"] in common_checks],
            "pending": list(pending.values()),
            "conduct_min": max(m["conduct_min"] for m in members),
            "sources": list(sources.values()),
        })
    return combined


def _sort_amount(entry: dict) -> int:
    return max([amount_value(entry["amount"])] + [amount_value(t["amount"]) for t in entry.get("tiers", [])])


def _without_semicolons(value):
    """卡片上的文字是模型照辦法整理的，常常照抄辦法用分號隔開一條一條規定（「不含延修生；審查時須具
    在學身分；……」）。使用者不喜歡分號，每一段本來就是獨立的一句，換成句號。"""
    if isinstance(value, str):
        return value.replace("；", "。")
    if isinstance(value, list):
        return [_without_semicolons(v) for v in value]
    if isinstance(value, dict):
        return {k: _without_semicolons(v) for k, v in value.items()}
    return value


def match_scholarships(
    index: list[dict], profile: StudentProfile, statuses=(), today: Optional[date] = None,
) -> dict:
    """前端 ScholarshipList 元件吃的資料包。急難救助不推薦（遇到急難的人會直接問怎麼申請）。"""
    today = today or date.today()
    statuses = tuple(s for s in dict.fromkeys(statuses or ()) if s in DECLARABLE_STATUSES)
    eligible, maybe, excluded = [], [], 0
    for item in index:
        if item["kind"] == "急難救助":
            continue
        status, checks, pending = evaluate(item, profile, statuses)
        if status == "ineligible":
            excluded += 1
            continue
        entry = _present(item, status, checks, pending, today)
        entry["_family"] = (scholarship_key(item["name"], base=True), _units_key(item), tuple(sorted({p["kind"] for p in pending})))
        (eligible if status == "eligible" else maybe).append(entry)
    eligible, maybe = combine_tiers(eligible), combine_tiers(maybe)
    for entry in eligible + maybe:
        del entry["_family"]
    # 還沒截止（或看不出截止日）的在前，金額大的在前；要確認的條件少的在前
    eligible.sort(key=lambda e: (e["deadline_passed"] is True, -_sort_amount(e)))
    maybe.sort(key=lambda e: (len(e["pending"]), e["deadline_passed"] is True, -_sort_amount(e)))
    return {
        "kind": "scholarship_recommendations",
        "profile": _profile_summary(profile),
        "statuses": list(statuses),
        "eligible": _without_semicolons(eligible),
        "maybe": _without_semicolons(maybe),
        "excluded_count": excluded,
        "apply_url": APPLY_URL,
    }


def recommend_scholarships(transcript: dict, statuses=()) -> dict:
    """agent 工具用：成績單（academic_tools.fetch_academic_records）→ 推薦結果。"""
    return match_scholarships(get_scholarship_index(), build_profile(transcript), statuses)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="整理 data/ 裡獎學金文件的申請資格（有快取，只整理新增、改版的文件）")
    parser.add_argument("--list", action="store_true", help="列出整理好的每一項獎學金，檢查整理得對不對")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    index = build_index(academic_agent.get_catalog(), max_workers=8)
    if args.list:
        for item in sorted(index, key=lambda i: (i["kind"], -amount_value(i["amount"]))):
            groups = "、".join(describe_group(g) for g in item["groups"]) or "不限學制"
            units = "、".join(item["units"]) or "全校"
            conditions = "、".join(c["kind"] for c in item["conditions"])
            deadline = f"｜{item['deadline_term']} 截止 {item['deadline']}" if item["deadline"] else ""
            print(f"[{item['kind']}] {item['name']}｜{item['amount']}｜{groups}｜{units}｜{conditions}{deadline}")
    print(f"獎學金共 {len(index)} 項")
