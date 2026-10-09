"""學校網站文件爬蟲：依 crawler_sources.py 的設定，把中央大學各單位網站上的法規、表單、
說明文件抓到 data/<來源名稱>/，給校園法規問答（rag_documents.py）使用。

在專案根目錄執行：

    python -m backend.rag.crawler                       # 爬全部來源（抓過、內容沒變的檔案不會重抓）
    python -m backend.rag.crawler --source 語言中心      # 只爬某個來源，可以重複指定
    python -m backend.rag.crawler --dry-run             # 只列出會新增、更新、移除哪些檔案，不動到 data/
    python -m backend.rag.crawler --list                # 列出設定好的來源
    python -m backend.rag.crawler --legacy              # 檢查 data/ 裡不是爬蟲抓的舊檔，跟爬到的檔案有沒有重複

每個檔案是從哪個網址、哪個頁面抓來的，記在 data/.crawler_manifest.json。下次執行時：
- 先用 ETag / Last-Modified 問伺服器，沒變的檔案不重抓，內容變了就覆蓋同一個檔案。
- 網站上已經不見的檔案移到 storage/crawler/removed/ 備份。只有那個來源的頁面全部正常讀到
  時才會移，網站暫時掛掉不會把檔案清光。
- 不在 manifest 裡的檔案（手動放進 data/ 的）一律不碰，檔名撞到也會換一個名字存。

實際看過這些網站之後，處理了這些狀況（2026-10）：
- 連結文字常常不是文件名稱：「下載 PDF」「按我下載pdf」「查看法規」「PDF」，真正的名稱在
  title 屬性、同一行前面的文字、卡片標題、表格的標題欄或網址的檔名裡。語言中心甚至只把
  「」」一個字做成連結。所以每個連結都找出幾個候選名稱，再挑最像文件名稱的（link_names）。
- 副檔名不可信：資工系連結寫 .doc 實際是 .odt、生輔組寫 (.doc) 實際是 .docx，也有沒有副檔名的
  /static/file/.../681518947，教務處的 /s/reg-form1-04 是會轉址的短網址。一律看檔案內容判斷格式。
- 同一份文件在同一頁出現好幾次（名稱、網址都不同），或同時給 PDF、ODT、DOC 好幾種格式：
  內容相同只存一份，同名的不同格式只留一種（PDF 優先）。
- 網址裡有中文、空格、全形括號，甚至用反斜線（download\\在學役男出境申請須知.docx）。
- 很多資訊不是附檔而是網頁本身（獎學金一覽表、宿舍 Q&A），或是網頁裡的海報圖片（各學院
  英文畢業門檻）。這些頁面設定在 Page，存成 Markdown，海報另存成 PDF 交給 RAG 的圖片轉錄。
- 資工系網站改版，舊的 /downloads 變成 404，教務章則彙編每個學年度換一頁（Follow 的 latest）。
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import fitz  # PyMuPDF：把海報圖片包成 PDF、讀 PDF 標題
import requests
import urllib3
from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from requests.utils import requote_uri

from backend import paths
from backend.rag.crawler_sources import COHORT_YEARS_KEPT, GLOBAL_EXCLUDE, SOURCES, TERM_LATEST_ONLY, Follow, Page, Source
from backend.logging_config import make_print_logger
from backend.rag.rag_documents import normalize_text, rows_to_markdown

print = make_print_logger(__name__)

# 想先看看會抓到什麼、不想動到 data/ 的話，可以用 CRAWLER_DATA_DIR 指到別的資料夾
DATA_DIR = Path(os.getenv("CRAWLER_DATA_DIR") or paths.DATA_DIR)
MANIFEST_PATH = DATA_DIR / ".crawler_manifest.json"
REMOVED_DIR = paths.STORAGE_DIR / "crawler" / "removed"
LEGACY_BACKUP_DIR = paths.STORAGE_DIR / "crawler" / "legacy_backup"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 NCUXplore/1.0"
)
REQUEST_DELAY = 0.5  # 同一個網站兩次請求之間至少隔幾秒
TIMEOUT = (10, 60)
MAX_FILE_BYTES = 40 * 1024 * 1024
# 同一個網站連續這麼多個請求都連不上（每個請求已經重試過 3 次），這一輪就不再連它。網站掛掉時一個請求
# 要等 30 秒以上才放棄，有幾十個檔案的來源會讓每週的自動更新卡好幾個小時（2026-10 生輔組網站就這樣）
HOST_GIVE_UP_AFTER = 2

# RAG 讀得懂的格式。同一份文件有好幾種格式時，留排前面的
PREFERRED_TYPES = (".pdf", ".docx", ".odt", ".doc")
DOC_EXTENSIONS = {"pdf", "doc", "docx", "odt", "ods", "odp", "xls", "xlsx", "ppt", "pptx", "rtf", "zip", "rar", "7z"}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "ico"}
GOOGLE_HOSTS = {"drive.google.com", "docs.google.com"}
# 學校網站委外廠商放附檔的網域（工學院的規章表單在 assets.ppnet.tw）
FILE_HOSTS = {"assets.ppnet.tw"}
SKIP_REASONS = {
    ".html": "連到的是網頁，不是文件",
    ".ods": "試算表，RAG 讀不了", ".xls": "試算表，RAG 讀不了", ".xlsx": "試算表，RAG 讀不了",
    ".ppt": "簡報檔，RAG 讀不了", ".pptx": "簡報檔，RAG 讀不了", ".odp": "簡報檔，RAG 讀不了",
    ".rtf": "RTF 檔，RAG 讀不了", ".png": "圖片", ".jpg": "圖片",
    ".zip": "壓縮檔", ".rar": "壓縮檔", ".7z": "壓縮檔",
}


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    """robots.txt 不允許（例如 Google 雲端硬碟的下載網址），算略過不算錯誤。"""


# ============================================================
# 網址
# ============================================================
def absolute_url(href: Optional[str], base: str) -> Optional[str]:
    """把頁面上的 href 轉成可以直接請求的網址（中文、空格編碼好、反斜線改斜線），
    mailto、javascript 這類不是網頁的連結回傳 None。
    """
    href = (href or "").strip().replace("\\", "/")
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    try:
        parts = urlparse(urljoin(base, href))
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    netloc = parts.hostname.lower() + (f":{port}" if port and port not in (80, 443) else "")
    return requote_uri(urlunparse((parts.scheme, netloc, parts.path or "/", parts.params, parts.query, "")))


def url_key(url: str) -> str:
    """判斷是不是同一個網址用：不分 http/https、%XX 編碼與否，也不管頁面的語系參數。"""
    parts = urlparse(url)
    query = "&".join(q for q in parts.query.split("&") if q and q.lower() != "lang=zh-tw")
    return f"{parts.hostname}{unquote(parts.path)}?{unquote(query)}"


def allowed_host(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "ncu.edu.tw" or host.endswith(".ncu.edu.tw") or host in GOOGLE_HOSTS or host in FILE_HOSTS


def google_download_url(url: str) -> Optional[str]:
    """Google 雲端硬碟、文件的分享連結轉成直接下載的網址。"""
    parts = urlparse(url)
    if parts.hostname == "drive.google.com":
        match = re.search(r"/file/d/([\w-]{10,})", parts.path) or re.search(r"(?:^|&)id=([\w-]{10,})", parts.query)
        if match:
            return f"https://drive.google.com/uc?export=download&id={match.group(1)}"
    elif parts.hostname == "docs.google.com":
        match = re.search(r"/(document|presentation)/d/([\w-]{10,})", parts.path)
        if match:
            kind, doc_id = match.groups()
            if kind == "document":
                return f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"
            return f"https://docs.google.com/presentation/d/{doc_id}/export/pdf"
    return None


_FILE_URL_HINT = re.compile(r"/(static|var)/file/|/s/[\w-]+/?$|downloadfile|/download\.php|/readfile/|[?&](file|fname|filename)=|"
    # 生科系的 /readfile/index.html?p=laws&n=6239761195770.pdf：檔名寫在參數裡
    r"=[^&=]*\.(pdf|docx?|odt|ods|xlsx?|pptx?)(&|$)", re.I)


def link_kind(url: str) -> str:
    """回傳 "doc"（可能是文件）、"page"（一般網頁），或空字串（不處理，例如圖片、沒辦法下載的雲端連結）。"""
    parts = urlparse(url)
    if parts.hostname in GOOGLE_HOSTS:
        return "doc" if google_download_url(url) else ""
    extension = Path(unquote(parts.path)).suffix.lower().lstrip(".")
    if extension in DOC_EXTENSIONS:
        return "doc"
    if extension in IMAGE_EXTENSIONS:
        return ""
    if _FILE_URL_HINT.search(parts.path + (f"?{parts.query}" if parts.query else "")):
        return "doc"  # RPage 的 /static/file/... 有時候沒有副檔名、/s/... 是轉址短網址
    return "page"


def url_extension(url: str) -> str:
    extension = Path(unquote(urlparse(url).path)).suffix.lower()
    return extension if extension.lstrip(".") in DOC_EXTENSIONS else ""


# ============================================================
# 判斷檔案格式（不相信副檔名）
# ============================================================
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ODF_TYPES = {
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
}


def sniff_extension(data: bytes) -> str:
    """看檔案內容判斷格式，回傳副檔名（".pdf"…），認不出來回傳空字串。"""
    if b"%PDF-" in data[:1024]:
        return ".pdf"
    if data[:8] == _OLE_MAGIC:  # 舊版 Office 都是 OLE 容器，看裡面的資料流名稱分辨
        if "WordDocument".encode("utf-16-le") in data:
            return ".doc"
        if "Workbook".encode("utf-16-le") in data or "Book".encode("utf-16-le") in data:
            return ".xls"
        if "PowerPoint Document".encode("utf-16-le") in data:
            return ".ppt"
        return ""
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                if "mimetype" in names:
                    return _ODF_TYPES.get(archive.read("mimetype").decode("ascii", "ignore").strip(), ".zip")
                if "word/document.xml" in names:
                    return ".docx"
                if any(n.startswith("xl/") for n in names):
                    return ".xlsx"
                if any(n.startswith("ppt/") for n in names):
                    return ".pptx"
                return ".zip"
        except zipfile.BadZipFile:
            return ""
    if data[:5] == b"{\\rtf":
        return ".rtf"
    if data[:4] == b"Rar!":
        return ".rar"
    if data[:6] == b"7z\xbc\xaf\x27\x1c":
        return ".7z"
    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    sample = data[:4096].lstrip().lower()
    if sample.startswith((b"<!doctype", b"<html")) or b"<html" in sample:
        return ".html"
    return ""


# ============================================================
# 文件名稱
# ============================================================
# 候選名稱的可信度：同樣像樣的名稱，先採用可信度高的來源
P_DOWNLOAD_ATTR = 6   # <a download="...">
P_LINK_TEXT = 5
P_TITLE_ATTR = 5      # title / aria-label
P_QUOTE = 5           # 「…」引號裡的名稱
P_LINE = 4            # 同一行連結前面的文字
P_TABLE = 3
P_HEADING = 3         # 卡片標題
P_DISPOSITION = 3     # 伺服器回傳的 Content-Disposition 檔名
P_URL = 2
P_FALLBACK = 1        # 頁面標題、PDF 內文第一行

_BLOCK_TAGS = {
    "p", "div", "li", "td", "th", "tr", "table", "tbody", "thead", "tfoot", "ul", "ol", "dl", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "main", "aside", "header", "footer", "nav",
    "body", "html", "blockquote", "form", "fieldset", "pre", "figure", "figcaption", "details", "summary",
    "hr", "caption", "address",
}

_BOILERPLATE = [re.compile(p, re.I) for p in (
    # （另開新視窗）、另開視新視窗、(另開新視窗開啟PDF檔案)、，另開新網站
    r"[（(\[【]?\s*(另開|開啟|開新)新?(視窗|網頁|分頁|頁面|新視窗|視新視窗|網站|新網站)[^）)\]】]*[）)\]】]?",
    r"原頁面開啟",
    r"^\s*(下載|檢視|查看|開啟|掛失)\s*(pdf|odt|ods|docx?|xlsx?)?\s*[：:]",
    r"[（(]\s*(下載|download)\s*[）)]",
    r"[（(]\s*\.?(pdf|odt|ods|odp|docx?|xlsx?|pptx?)\s*檔?\s*[）)]",
    r"\.?(pdf|odt|ods|odp|docx?|xlsx?|pptx?)\s*檔案?",
    r"\.(pdf|odt|ods|odp|docx?|xlsx?|pptx?)\s*$",
    r"(?<=[\u3400-\u9fff])\s*(pdf|odt|ods|docx?|xlsx?)\s*$",
    r"(?<=[）)])\s*(pdf|odt|ods|docx?|xlsx?)\s*$",  # 太空系「…（審定結果）ODT」
    r"(按我|點我|點此|按此|請點選?)\s*(下載|看|觀看|查看|閱讀)?",
    r"^form\d+(-\d+)*",  # 教務處表單編號的英文前綴（form02-07-1國立中央大學…）
    r"^下載\s*(?=\S)",  # title 寫成「下載83年次(不含)以前申請文件 Word 格式」
    r"(?<=[－\s])下載\s*(?=[中英])",  # 「3-05 指導教授推薦書－下載中文版」
    r"\s*(Word|OpenDocument|ODF|PDF|ODT|DOCX?)\s*格式$",
    # 2026-10 加進全校各系所網站之後看到的
    r"[（(]?\s*opens? (in )?a new (tab|window)\s*[）)]?",  # WordPress 的無障礙說明文字
    r"在(新|本)視窗開啟",  # Orbit 系統（zh_tw 網址）把這幾個字放在連結文字前面
    # 「[]院務會議設置辦法」「[word 表格]升等個人資料表」：檔案類型圖示的替代文字（「[寒暑期營隊]」這種分類要留著）
    r"^\s*\[\s*((word|pdf|odt|docx?|xlsx?|excel)\s*(表格|檔案?)?)?\s*\]\s*",
    r"^\s*(PDF|DOCX?|ODT|XLSX?)\s+(?=\S)",  # 「PDF 114國立中央大學環境工程研究所博士班修業辦法.pdf 更新日期：…」
    r"(\s+(PDF|DOCX?|ODT))?\s*更新日期\s*[:：]?.*$",  # 「…修業辦法 PDF 更新日期 115.04.23」
    r"[（(]\s*(pdf|docx?|odt|xlsx?|pptx?)\s*[,，]\s*[\d.]+\s*[KMG]?B?\s*[）)]?",  # 「(PDF, 102KB)」
    r"^\d{1,2}\)\s*",  # 光電系的「05) 光電系免修學分申請表」
    # 總務處（Nuxt 做的網站）：「2018-06-08-事務組 檔案下載-國立中央大學機車通行證申請表（此為PDF檔案，請參閱檔案摘要或說明頁）」
    r"[（(]\s*此為\S*?(檔案)?\s*[，,]\s*請參閱檔案摘要或說明頁\s*[）)]",
    r"^\s*\d{4}-\d{2}-\d{2}-\S{1,8}?(組|處|隊|室|中心)\s*",
    r"^\s*檔案下載\s*[-－:：]\s*",
    r"^\s*\[\s*檔案下載\s*\]\s*",  # 同一個連結的 title 寫成「[檔案下載]國立中央大學機車通行證申請表.pdf」
    r"^\s*[•●・‧]\s*",  # 清單的項目符號被寫進連結文字（「• 學生郵件包裹領取須知」）
    r"^(請參閱|請參考|詳見)\s*",
    # 生科系附件的 title 寫成「(國立中央大學生命科學系助學工讀辦法.pdf)(pdf檔下載」
    r"[（(]\s*(pdf|odt|docx?)?\s*檔?\s*下載\s*[)）]?\s*$",
    r"\.(pdf|odt|ods|odp|docx?|xlsx?|pptx?)(?=[)）]\s*$)",
)]
# 檔案類型圖示、裝飾用的符號（化材系的「📄 碩士班修業辦法」、「★ 113 學年度課程地圖」）
_SYMBOLS = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u25A0-\u25FF\u2190-\u21FF\uFE0F]")
# 名稱後面整串的英文翻譯（住宿組的「國立中央大學學生宿舍管理辦法The Regulation of Student
# Dormitories in National Central University」），中文名稱夠長時拿掉，檔名才不會長到被截斷
_ENGLISH_TRANSLATION = re.compile(r"\s*[A-Z][A-Za-z’'&,.\-]*(\s+[A-Za-z’'&,.\-]+){2,}\s*")
_GENERIC_PHRASE = re.compile(r"(查看|檢視|下載|開啟)?(法規|檔案|文件|表單|附件|內容|全文|資料|連結|網頁|簡章|公告|系統)")
_INSTRUCTION_PREFIX = re.compile(r"^.*?(請填妥|請填寫|請下載|請參閱|請參考|請使用|詳見|參閱)(以下|下列|如下)?")
_SEPARATORS = " \t\n》>»→：:，,、。；;|｜-–—_"
_GENERIC_WORDS = re.compile(
    r"下載|檔案|附件|連結|點我|點此|點選|按我|按此|請點|這裡|此處|查看|檢視|開啟|瀏覽|觀看|閱讀|全文|詳見|詳細|更多|"
    r"pdf|odt|ods|docx?|xlsx?|pptx?|click|here|download|file|link|view|more|open|進入|enter|image|img|icon|圖片|縮圖|圖示",
    re.I,
)
_DOC_WORDS = re.compile(
    r"辦法|要點|細則|準則|規則|學則|守則|原則|規定|法規|標準|條例|章程|須知|注意事項|說明|流程|指引|手冊|申請|表|書|單|"
    r"證明|切結|委託|計畫|簡章|公告|問答|Q&A|FAQ|一覽|清冊|存根|文件|校曆|彙編|Calendar|Form|Regulation|Guideline|"
    r"Rule|Manual",
    re.I,
)
# 網站管理者留在名稱裡的雜訊：表單代碼、長串日期流水號、「移除連結-改信箱」這種修改註記
_JUNK = re.compile(r"\d{7,}|移除連結|改信箱|_v\d{6,}|[(（]\d[)）]$|複本|copy|final|FIN$|_pdf$|_odt$", re.I)
_QUALIFIER = re.compile(
    r"(中文|英文|中英文)版?(說明)?|English( version)?|說明|使用說明|委託書|範例|填寫範例|附件[一二三四五六七八九十\d]*|"
    # 物理系「王立文女士獎學金辦法　申請表」：連結文字只有「申請表」，名稱要接在同一行的辦法後面
    r"申請(表|書|單)|表格|切結書|同意書|範本|檢核表",
    re.I,
)
# 只有文件類型的連結文字（通識中心的「辦法」、化材系的「獎勵辦法」）：真正的名稱在網址或同一行裡，
# 要借上下文的名稱，但不會像「中文版」那樣接在別的名稱後面
_BARE_TYPE = re.compile(r"(選修|修業|申請|實施|施行|作業|獎勵|補助)?(辦法|要點|規定|細則)|簡章|表單|檔案")
# 只有年度的連結文字（物理系「大學部課程地圖：108學年度｜109學年度」），名稱要接在上下文後面
_YEAR_ONLY = re.compile(r"(\d{2,4}\s*(學年度?|級|年度?|學期)?\s*(入學|適用|入學適用|入學新生適用|以後|以前|起)?[\s\-~～至、]*)+")


def tidy(text: str) -> str:
    """名稱的空白整理：中文字之間的空白拿掉（網站常常把「生醫科學與工程學 系」拆開）。"""
    text = re.sub(r"[\u00a0\u3000\u200b\ufeff\s]+", " ", text or "").strip()
    text = re.sub(r"(?<=[\u3400-\u9fff（「『【])\s+(?=[\u3400-\u9fff）」』】(（])", "", text)
    text = re.sub(r"(?<=[(（])\s+|\s+(?=[)）])", "", text)
    return text


def clean_name(text: str) -> str:
    """拿掉「（另開新視窗）」「下載 PDF：」「(.pdf)」這類跟名稱無關的字，認不出名稱回傳空字串。"""
    name = tidy(_SYMBOLS.sub(" ", normalize_text(text or "")))  # 網址、標題裡也有相容字（國立的「立」是 U+F9F7）
    if re.match(r"\s*https?://", name):
        return ""  # 連結文字直接寫網址
    for _ in range(2):
        for pattern in _BOILERPLATE:
            name = pattern.sub("", name)
        name = name.strip(_SEPARATORS)
    name = re.sub(r"\s*》\s*", "－", name).strip(_SEPARATORS + "－")
    name = re.sub(r"(－)+", "－", name)
    repeated = re.fullmatch(r"(.{4,}?)\1+", name)  # 「實施細則實施細則實施細則」
    if repeated:
        name = repeated.group(1)
    if len(re.findall(r"[\u3400-\u9fff]", name)) >= 4:
        name = _ENGLISH_TRANSLATION.sub(" ", name).strip(_SEPARATORS)
        name = re.sub(r"\s*_\s*", "_", name)
    if name.startswith("【") and name.endswith("】") and name.count("【") == 1:
        name = name[1:-1]
    for left, right in (("「", "」"), ("『", "』")):
        if name.count(left) != name.count(right):
            name = name.replace(left, "").replace(right, "")
    if re.fullmatch(r"[（(][^()（）]+[)）]", name):
        name = name[1:-1]  # 整個名稱被括號包起來
    name = re.sub(r"[\s:：]*[（(]\s*$", "", name)  # 「…相關修業辦法： (下載)」拿掉「下載)」之後剩下的半個括號
    name = re.sub(r"[\s_-]+(pdf|docx?|odt)\s*$", "", name, flags=re.I)  # 化學系的「博士班修業辦法-1070321 pdf」
    name = tidy(name)
    return "" if is_generic(name) else name


def is_generic(name: str) -> bool:
    if _GENERIC_PHRASE.fullmatch(name or ""):
        return True  # 「查看法規」「檔案」
    rest = re.sub(r"[\W\d_]", "", _GENERIC_WORDS.sub("", name or ""))
    return len(re.findall(r"[\u3400-\u9fff]", rest)) < 2 and len(re.findall(r"[A-Za-z]", rest)) < 4


def name_score(name: str, priority: int) -> float:
    if not name:
        return float("-inf")
    score = float(priority)
    if _DOC_WORDS.search(name):
        score += 2
    elif len(name) <= 12:
        score -= 2  # 「中文版」「83年次(不含)以前」這種只是名稱的一部分
    if len(name) < 4:
        score -= 3
    elif len(name) > 60:
        score -= 2
    score -= 2 * len(_JUNK.findall(name))
    return score


def choose_name(candidates: list[tuple[int, str]]) -> str:
    """從候選名稱挑最像文件名稱的。挑到的只是「中文版」這種修飾語時，接在上下文的名稱後面。"""
    cleaned = []
    for priority, text in candidates:
        name = clean_name(text)
        if name and (priority, name) not in cleaned:
            cleaned.append((priority, name))
    if not cleaned:
        return ""
    ranked = sorted(cleaned, key=lambda c: name_score(c[1], c[0]), reverse=True)
    # 頁面標題、PDF 第一行只在完全沒有別的名稱時才拿來當名稱，平常只當「中文版」的前半段
    best = next((n for p, n in ranked if p > P_FALLBACK), ranked[0][1])
    if _QUALIFIER.fullmatch(best) or _BARE_TYPE.fullmatch(best) or _YEAR_ONLY.fullmatch(best):
        context = next((n for _, n in ranked if n != best and not _QUALIFIER.fullmatch(n)
                        and not _BARE_TYPE.fullmatch(n) and not _YEAR_ONLY.fullmatch(n)), "")
        if context and best in context:
            return context  # 「辦法」接在「創意學分學程選修辦法」後面是多餘的
        return f"{context}（{best}）" if context else best
    # 「辦法：[中文版]、[英文版]」：名稱取自同一行，連結文字的版本別要接在後面，兩份才分得開
    qualifier = next((n for p, n in cleaned if p == P_LINK_TEXT and _QUALIFIER.fullmatch(n)), "")
    core = re.sub(r"版|說明|version", "", qualifier, flags=re.I).strip()  # 「英文版說明」→「英文」
    already_english = core == "英文" and not re.search(r"[\u3400-\u9fff]", best)
    if qualifier and qualifier not in best and not (core and core in best) and not already_english:
        return f"{best}（{qualifier}）"
    return best


def _text(node) -> str:
    return tidy(" ".join(node.stripped_strings)) if isinstance(node, Tag) else tidy(str(node))


def _line_around(link: Tag) -> tuple[str, str, int, int]:
    """連結所在的那一行（遇到 <br> 或區塊元素就斷行）：回傳（整行文字、扣掉其他連結文字的
    連結前文字、連結在整行裡的起訖位置）。
    """
    block = link.find_parent(lambda t: t.name in _BLOCK_TAGS) or link.parent
    pieces: list[tuple[str, bool]] = []  # (文字, 是不是其他連結的文字)
    span = [0, 0]
    position = 0

    def add(text: str, other_link: bool) -> None:
        nonlocal position
        pieces.append((text, other_link))
        position += len(text)

    def walk(node: Tag, inside_link: bool) -> None:
        for child in node.children:
            if child is link:
                span[0] = position
                add(_text(link) or "\ufffc", False)  # 只有圖示的連結也佔一個位置
                span[1] = position
            elif isinstance(child, Comment):
                continue
            elif isinstance(child, NavigableString):
                # 原始碼裡的換行只是排版用的空白，真正的換行是 <br> 跟區塊元素
                add(re.sub(r"\s+", " ", str(child)), inside_link)
            elif isinstance(child, Tag) and child.name not in ("script", "style"):
                if child.name == "br" or child.name in _BLOCK_TAGS:
                    add("\n", False)
                    if child.name != "br":
                        walk(child, inside_link)
                        add("\n", False)
                else:
                    walk(child, inside_link or child.name == "a")

    walk(block, False)
    full = "".join(text for text, _ in pieces)
    start = full.rfind("\n", 0, span[0]) + 1
    end = full.find("\n", span[1])
    end = len(full) if end < 0 else end

    before, offset = [], 0
    for text, other_link in pieces:
        if offset >= span[0]:
            break
        if offset + len(text) > start and not other_link:
            before.append(text[max(0, start - offset):])
        offset += len(text)
    return full[start:end], "".join(before), span[0] - start, span[1] - start


def _quote_name(line: str, start: int, end: int) -> str:
    """「…請填妥以下「理學院大學部外文能力鑑定審核申請表」」：連結只包住引號時，取引號裡的字。"""
    matches = list(re.finditer(r"[「『]([^「」『』]{2,80})[」』]?", line))
    for match in matches:
        if match.start() <= end and match.end() >= start:
            return match.group(1)
    for match in reversed(matches):
        if 0 <= start - match.end() <= 2:
            return match.group(1)
    return ""


def _heading_name(link: Tag) -> str:
    """卡片式版面（住宿組表單）：名稱是卡片裡在連結前面的第一行字。"""
    node = link
    for _ in range(4):
        node = node.parent
        if node is None or node.name in ("body", "html", "[document]", "table", "tbody", "ul", "ol"):
            return ""
        if len(node.find_all("a", href=True)) > 6:
            return ""  # 已經是整份清單，不是單一文件的卡片
        lines = []
        for element in node.descendants:
            if element is link:
                break
            if not isinstance(element, NavigableString) or isinstance(element, Comment):
                continue
            if element.find_parent("a") is not None or element.parent.name in ("script", "style"):
                continue
            stripped = tidy(str(element))
            if stripped and not re.match(r"(更新日期|發布日期|日期|作者|點閱|瀏覽)", stripped):
                lines.append(stripped)
        if lines and len(lines[0]) <= 80:
            return lines[0]
    return ""


_TITLE_HEADER = re.compile(r"標題|名稱|檔名|文件|表單|法規|項目|主旨|title|name", re.I)


def _cell_text_without_links(cell: Tag) -> str:
    return tidy(" ".join(t for t in cell.find_all(string=True) if t.find_parent("a") is None and not isinstance(t, Comment)))


def _table_name(link: Tag) -> str:
    """表格版面（學務處下載區）：取「標題」欄，沒有的話取連結自己那一格扣掉連結的文字。"""
    cell = link.find_parent(["td", "th"])
    row = cell.find_parent("tr") if cell else None
    table = row.find_parent("table") if row else None
    if table is None:
        return ""
    cells = row.find_all(["td", "th"], recursive=False)
    header = next((tr for tr in table.find_all("tr") if tr.find("th") and tr.find_parent("table") is table), None)
    if header is not None and header is not row:
        heads = [_text(h) for h in header.find_all(["td", "th"], recursive=False)]
        for index, head in enumerate(heads):
            if _TITLE_HEADER.search(head) and index < len(cells) and cells[index] is not cell:
                return _cell_text_without_links(cells[index])
    return _cell_text_without_links(cell)


def url_name(url: str) -> str:
    """網址裡的檔名（有中文或英文字才算，純數字流水號不算）。"""
    stem = Path(unquote(urlparse(url).path)).name
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", stem)
    stem = re.sub(r"(_(pdf|odt|ods|docx?))+$", "", stem, flags=re.I)
    if not stem or re.fullmatch(r"[\d_\-.]+|\W*[0-9a-fA-F\-]{16,}\W*|reg-form[\w-]*|download|view|uc|export", stem, re.I):
        return ""
    return stem


def disposition_name(header: str) -> str:
    """Content-Disposition 的檔名。Google 雲端常把 UTF-8 檔名當成 latin-1 送，要轉回來。"""
    if not header:
        return ""
    match = re.search(r"filename\*\s*=\s*([\w-]+)'[^']*'([^;]+)", header, re.I)
    if match:
        try:
            return unquote(match.group(2).strip().strip('"'), encoding=match.group(1))
        except LookupError:
            pass
    match = re.search(r'filename\s*=\s*"([^"]+)"', header, re.I) or re.search(r"filename\s*=\s*([^;]+)", header, re.I)
    if not match:
        return ""
    raw = match.group(1).strip()
    for encoding in ("utf-8", "cp950"):
        try:
            return raw.encode("latin-1").decode(encoding)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return raw


def pdf_title(data: bytes) -> str:
    """最後手段：PDF 的標題欄位或第一頁第一行。"""
    try:
        with fitz.open(stream=data, filetype="pdf") as pdf:
            title = re.sub(r"^Microsoft (Word|PowerPoint) - ", "", (pdf.metadata or {}).get("title") or "")
            if clean_name(title):
                return title
            text = pdf[0].get_text("text") if pdf.page_count else ""
    except Exception:
        return ""
    return next((line.strip() for line in text.splitlines() if len(line.strip()) >= 4), "")[:60]


def link_names(link: Tag, url: str) -> list[tuple[int, str]]:
    """一個連結的所有候選名稱（可信度, 名稱），由 choose_name 挑。"""
    candidates = [
        (P_DOWNLOAD_ATTR, link.get("download") or ""),
        (P_LINK_TEXT, _text(link)),
        (P_TITLE_ATTR, link.get("title") or ""),
        (P_TITLE_ATTR, link.get("aria-label") or ""),
        (P_URL, url_name(url)),
    ]
    for image in link.find_all("img"):
        candidates.append((P_LINK_TEXT, image.get("alt") or ""))
    line, before, start, end = _line_around(link)
    # 學務處下載中心一格裡先放中文標題跟檔案圖示、換行再放英文標題跟英文版的圖示：連結前面整行都是
    # 英文的，就是英文版（「英文版」是修飾語，會接在表格欄位的中文名稱後面，兩份才分得開）
    if len(re.findall(r"[A-Za-z]{2,}", before)) >= 3 and not re.search(r"[\u3400-\u9fff]", before):
        candidates.append((P_LINK_TEXT, "英文版"))
    candidates.append((P_QUOTE, _quote_name(line, start, end)))
    clause = next((c for c in reversed(re.split(r"[。；;！!？?：:，,]", before)) if c.strip()), "")
    candidates.append((P_LINE, _INSTRUCTION_PREFIX.sub("", clause)))
    candidates.append((P_HEADING, _heading_name(link)))
    candidates.append((P_TABLE, _table_name(link)))
    return [(priority, text) for priority, text in candidates if text and text.strip()]


def base_url(soup: BeautifulSoup, page_url: str) -> str:
    """相對連結要用的基準網址：頁面有 <base href> 就以它為準（圖書館的頁面放了 <base href="/">，
    「rule/xxx.pdf」其實是網站根目錄底下的 /rule/xxx.pdf，照頁面所在目錄算會全部 404）。"""
    tag = soup.find("base", href=True)
    return urljoin(page_url, tag["href"]) if tag else page_url


def page_title(soup: BeautifulSoup) -> str:
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    parts = [p.strip() for p in re.split(r"\s+[-–|｜]\s+|\s*[|｜]\s*", title) if p.strip()]
    return parts[0] if parts else ""


def find_document_links(soup: BeautifulSoup, page_url: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """頁面上所有文件連結（含側邊選單裡的，例如註冊組的學雜費收費標準）跟各自的候選名稱。
    頁面標題也放進候選，給「中文版說明」這種只有版本別的連結當名稱的前半段。
    """
    found = []
    title = page_title(soup)
    for link in soup.find_all("a", href=True):
        url = absolute_url(link["href"], page_url)
        if url and allowed_host(url) and link_kind(url) == "doc":
            found.append((url, link_names(link, url) + [(P_FALLBACK, title)]))
    for frame in soup.find_all(["iframe", "embed", "object"]):
        url = absolute_url(frame.get("src") or frame.get("data"), page_url)
        if url and allowed_host(url) and link_kind(url) == "doc":
            found.append((url, [(P_HEADING, title), (P_URL, url_name(url))]))
    found.extend(_json_attribute_links(soup, page_url))
    return found


def _json_attribute_links(soup: BeautifulSoup, page_url: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """數學系網站是 Vue 寫的，檔案清單以 JSON 放在元件的屬性裡（<college-card files="[{name, file, year}]">），
    網頁上沒有 <a> 連結。屬性值是含 file/url 欄位的 JSON 陣列時，把每一筆當成一個文件連結。"""
    def records(value):  # 碩博士班的是 {"deductions": [...], "rules": [...]}，一路往下找有 file 欄位的
        if isinstance(value, list):
            for item in value:
                yield from records(item)
        elif isinstance(value, dict):
            if isinstance(value.get("file") or value.get("url"), str):
                yield value
            for item in value.values():
                if isinstance(item, (list, dict)):
                    yield from records(item)

    found = []
    for tag in soup.find_all(True):
        for value in tag.attrs.values():
            if not isinstance(value, str) or not value.lstrip().startswith(("[", "{")) or '"file"' not in value and '"url"' not in value:
                continue
            try:
                parsed = json.loads(value)
            except ValueError:
                continue
            for item in records(parsed):
                path = item.get("file") or item.get("url") or ""
                url = absolute_url(path if "://" in path or path.startswith("/") else "/" + path, page_url) if path else None
                if url and allowed_host(url) and link_kind(url) == "doc":
                    name = str(item.get("name") or item.get("title") or "")
                    year = str(item.get("year") or "")
                    if name and year.isdigit() and year not in name:
                        name = f"{name}（{year}）"
                    found.append((url, [(P_LINK_TEXT, name), (P_URL, url_name(url))]))
    return found


def follow_targets(soup: BeautifulSoup, page_url: str, rules: tuple[Follow, ...]) -> list[str]:
    targets: list[str] = []
    for rule in rules:
        matches = []
        for link in soup.find_all("a", href=True):
            url = absolute_url(link["href"], page_url)
            if not url or not allowed_host(url) or link_kind(url) != "page":
                continue
            if rule.url and not re.search(rule.url, url):
                continue
            match = re.search(rule.text, _text(link)) if rule.text else None
            if rule.text and not match:
                continue
            matches.append((url, match))
        if rule.latest:
            numbered = [(int(m.group(1)), url) for url, m in matches if m and m.groups() and (m.group(1) or "").isdigit()]
            if numbered:
                targets.append(max(numbered)[1])
        else:
            targets.extend(url for url, _ in matches)
    return list(dict.fromkeys(targets))


# ============================================================
# 網頁本身存成 Markdown
# ============================================================
_LAYOUT_CLASS = re.compile(r"(^|[-_\s])(nav|navbar|menu|breadcrumbs?|footer|sidebar|skip|sitemap|pagination|share)([-_\s]|$)", re.I)
_INLINE_TAGS = {
    "a", "span", "strong", "b", "em", "i", "u", "small", "big", "font", "sup", "sub", "label", "abbr", "code",
    "time", "mark", "button", "img", "s", "strike", "del", "ins", "q", "cite",
}


def main_content(soup: BeautifulSoup, selector: str = "") -> Tag:
    """頁面的主要內容：有指定 selector 就用，RPage 的內容在 .mpgdetail，其他網站拿掉選單、頁首頁尾
    之後，從 body 一路往下走到「一個子元素就佔了八成以上文字」為止。會改動傳進來的 soup。
    """
    for css in filter(None, (selector, ".mpgdetail")):
        found = soup.select_one(css)
        if found is not None and found.get_text(strip=True):
            return found
    body = soup.body or soup
    # 表單欄位也拿掉：住宿證明申請頁有今天日期的輸入框、全校系所的下拉選單，留著的話內容每天都會「變」
    for tag in body.find_all(["script", "style", "noscript", "template", "header", "nav", "footer", "aside",
                              "select", "input", "textarea", "datalist"]):
        tag.decompose()
    total = len(body.get_text(strip=True)) or 1
    for tag in body.find_all(True):
        if tag.decomposed or tag.name in ("main", "article"):
            continue
        ident = " ".join(tag.get("class") or []) + " " + (tag.get("id") or "")
        # 佔了頁面一半以上文字的不會是選單（語言中心的內容外框叫 "footer_fix sidebar_bg"）
        if _LAYOUT_CLASS.search(ident) and len(tag.get_text(strip=True)) < 0.5 * total:
            tag.decompose()
    node = body
    while True:
        total = len(node.get_text(strip=True))
        children = node.find_all(True, recursive=False)
        best = max(children, key=lambda c: len(c.get_text(strip=True)), default=None)
        if best is None or total == 0 or len(best.get_text(strip=True)) < 0.8 * total:
            return node
        if best.name in ("table", "tbody", "thead", "tr", "ul", "ol", "dl"):
            return node  # 不要走進表格、清單裡面，不然表格會被拆成一格一行
        node = best


def _inline(node, base_url: str) -> str:
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node))
    if node.name in ("script", "style"):
        return ""
    if node.name == "br":
        return "\n"
    if node.name == "img":
        alt = tidy(node.get("alt") or "")
        return f"（圖片：{alt}）" if alt and not re.search(r"\.(png|jpe?g|gif)$|^icon|圖示", alt, re.I) else ""
    text = "".join(_inline(child, base_url) for child in node.children)
    if node.name == "a":
        url = absolute_url(node.get("href"), base_url)
        label = text.strip()
        # 系統、網頁的連結留網址（回答時可以告訴使用者去哪裡辦），文件本身另外存了，不重複貼網址
        if label and url and link_kind(url) == "page" and len(url) <= 120 and label != url:
            return f"[{label}]({url})"
    return text


def _render(node: Tag, base_url: str, out: list[str]) -> None:
    buffer: list[str] = []

    def flush() -> None:
        text = "".join(buffer).strip()
        buffer.clear()
        if text:
            out.append("\n".join(line.strip() for line in text.split("\n") if line.strip()))

    for child in node.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString) or child.name in _INLINE_TAGS or child.name == "br":
            buffer.append(_inline(child, base_url))
            continue
        if child.name in ("script", "style", "noscript", "template", "hr"):
            continue
        flush()
        if re.fullmatch(r"h[1-6]", child.name):
            text = tidy(_inline(child, base_url))
            if text:
                out.append("#" * min(int(child.name[1]) + 1, 6) + " " + text)  # 檔案標題用 #，頁面裡的標題往下一層
        elif child.name in ("ul", "ol"):
            out.append(_render_list(child, base_url))
        elif child.name == "table":
            out.append(_render_table(child, base_url))
        else:
            _render(child, base_url, out)
    flush()


def _render_list(node: Tag, base_url: str) -> str:
    lines = []
    for index, item in enumerate(node.find_all("li", recursive=False), 1):
        inner: list[str] = []
        _render(item, base_url, inner)
        text = "\n".join(inner).strip()
        if text:
            bullet = f"{index}." if node.name == "ol" else "-"
            lines.append(f"{bullet} " + text.replace("\n", "\n  "))
    return "\n".join(lines)


def _render_table(table: Tag, base_url: str) -> str:
    """合併儲存格展開：往下合併的用 None（交給 rows_to_markdown 補第一欄），橫向合併補空格。"""
    rows: list[list[Optional[str]]] = []
    pending: dict[int, int] = {}  # 欄位 → 還要往下延續幾列
    for tr in (tr for tr in table.find_all("tr") if tr.find_parent("table") is table):
        row: list[Optional[str]] = []
        cells = tr.find_all(["td", "th"], recursive=False)
        column = 0
        index = 0
        while index < len(cells) or any(c >= column for c in pending):
            if column in pending:
                row.append(None)
                pending[column] -= 1
                if pending[column] == 0:
                    del pending[column]
                column += 1
                continue
            if index >= len(cells):  # 這一列比較短，補到往下合併的那一欄
                row.append("")
                column += 1
                continue
            cell = cells[index]
            index += 1
            inner: list[str] = []
            _render(cell, base_url, inner)
            colspan = min(max(int(re.sub(r"\D", "", cell.get("colspan") or "") or 1), 1), 20)
            rowspan = min(max(int(re.sub(r"\D", "", cell.get("rowspan") or "") or 1), 1), 200)
            row.append("\n".join(inner))
            row.extend([""] * (colspan - 1))
            if rowspan > 1:
                for c in range(column, column + colspan):
                    pending[c] = rowspan - 1
            column += colspan
        rows.append(row)
    return rows_to_markdown(rows)


_VIEW_COUNTER_CLASS = re.compile(r"(?<![a-z])(view|hit|visit)[-_]?(count|counter)s?(?![a-z])", re.I)
_VIEW_COUNTER_TEXT = re.compile(r"^\s*(瀏覽人次|瀏覽次數|點閱次數|點閱率|點擊次數)\s*[:：]?\s*[\d,]+\s*$", re.M)


def _drop_view_counters(root: Tag) -> None:
    """拿掉瀏覽人次：Orbit 系統的 <div class="view_count">、衛保組網站「眼睛圖示 + 數字」的 <span>。
    只刪文字很短的，萬一哪個網站拿這種 class 包住內容，也不會整段被刪掉。"""
    for tag in root.find_all(True):
        if (not tag.decomposed and len(tag.get_text(strip=True)) <= 30
                and _VIEW_COUNTER_CLASS.search(" ".join(tag.get("class") or []) + " " + (tag.get("id") or ""))):
            tag.decompose()
    for icon in root.select('svg[data-icon="eye"], i.fa-eye'):
        parent = icon.parent
        if not icon.decomposed and parent is not None and re.fullmatch(r"[\d,\s]*", parent.get_text(strip=True)):
            parent.decompose()


def page_markdown(soup: BeautifulSoup, page: Page, url: str, base: str = "") -> tuple[str, Tag]:
    """base 是相對連結的基準網址（頁面有 <base href> 時跟 url 不一樣），url 是記在文件開頭的來源網頁。"""
    root = main_content(soup, page.selector)
    # 每次抓都不一樣的東西（瀏覽人次、今天的日期）要拿掉，不然 RAG 會以為文件改版、每次都重做卡片
    _drop_view_counters(root)
    blocks: list[str] = []
    _render(root, base or url, blocks)
    body = _VIEW_COUNTER_TEXT.sub("", normalize_text("\n\n".join(blocks)))
    # 有些頁面會印出「今天的日期」（住宿證明申請表的日期欄）
    today = datetime.now()
    for stamp in {today.strftime("%Y-%m-%d"), today.strftime("%Y/%m/%d"), f"{today.year}/{today.month}/{today.day}"}:
        body = body.replace(stamp, "")
    return f"# {page.name}\n\n來源網頁：{url}\n\n{body}\n", root


def content_images(root: Tag, base_url: str) -> list[str]:
    """內容裡可能是海報的圖片（寬高標得太小的是圖示，先排除）。"""
    urls = []
    for image in root.find_all("img"):
        url = absolute_url(image.get("src") or image.get("data-src"), base_url)
        sizes = [int(v) for v in (image.get("width"), image.get("height")) if v and str(v).isdigit()]
        if url and not any(size < 300 for size in sizes):
            urls.append(url)
    return list(dict.fromkeys(urls))


def images_to_pdf(images: list[bytes]) -> bytes:
    """海報圖片包成 PDF 給 RAG 的圖片轉錄讀。很長的海報切成幾頁（每頁不比寬度高、上下重疊一些），
    一次看一段字比較清楚。頁面寬度縮到 1000pt，RAG 放大兩倍轉成圖片時剛好約 2000px。
    """
    pdf = fitz.open()
    for data in images:
        pixel_width, pixel_height = image_size(data)
        # 用整數 pt 計算，裁切範圍才不會因為小數誤差超出頁面
        scale = min(1.0, 1000 / pixel_width)
        width, height = round(pixel_width * scale), round(pixel_height * scale)
        overlap = int(width * 0.08)
        xref = 0
        top = 0
        while True:
            bottom = min(height, top + width)
            page = pdf.new_page(width=width, height=height)
            # 直接放原本的 JPEG／PNG，PDF 不會比圖片大多少；同一張圖的每一段共用同一份圖片資料
            xref = page.insert_image(page.rect, stream=data) if not xref else page.insert_image(page.rect, xref=xref)
            page.set_cropbox(fitz.Rect(0, top, width, bottom) & page.mediabox)
            if bottom >= height:
                break
            top = bottom - overlap
    data = pdf.tobytes(garbage=3, deflate=True)
    pdf.close()
    return data


def image_size(data: bytes) -> tuple[int, int]:
    try:
        pixmap = fitz.Pixmap(data)
        return pixmap.width, pixmap.height
    except Exception:
        return 0, 0


# ============================================================
# 下載
# ============================================================
class Fetcher:
    """requests 的包裝：同一個網站的請求排隊、中間隔 REQUEST_DELAY 秒，暫時性錯誤重試，
    遵守 robots.txt，TLS 憑證驗證失敗的網站（cis.ncu.edu.tw 的憑證鏈有缺陷）才改成不驗證。
    """

    def __init__(self, delay: float = REQUEST_DELAY):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5"})
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._last_request: dict[str, float] = {}
        self._insecure_hosts: set[str] = set()
        self._robots: dict[str, Optional[RobotFileParser]] = {}
        self._failures: dict[str, int] = {}  # 每個網站連續連不上的請求數

    def _lock(self, host: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(host, threading.Lock())

    def allows(self, url: str) -> bool:
        """robots.txt 允不允許程式讀這個網址（Renderer 用瀏覽器開網頁之前也要問）。"""
        return self._robots_allows(url)

    def _robots_allows(self, url: str) -> bool:
        parts = urlparse(url)
        host = parts.hostname or ""
        if host not in self._robots:
            parser = None
            try:
                response = self._request(f"{parts.scheme}://{parts.netloc}/robots.txt", {}, stream=False)
                if response.status_code == 200 and "text/plain" in response.headers.get("Content-Type", ""):
                    parser = RobotFileParser()
                    parser.parse(response.text.splitlines())
            except requests.RequestException:
                parser = None
            self._robots[host] = parser
        parser = self._robots[host]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def _request(self, url: str, headers: dict, stream: bool = True) -> requests.Response:
        host = urlparse(url).hostname or ""
        insecure = host in self._insecure_hosts
        for attempt in range(3):
            try:
                response = self.session.get(url, headers=headers, timeout=TIMEOUT, stream=stream, verify=not insecure)
            except requests.exceptions.SSLError as e:
                # 憑證有問題的可能是轉址之後的網站（資工系的老師個人網頁轉到 web.ss.ncu.edu.tw），
                # 只對學校自己的網站放寬，外面的網站憑證有問題就當成讀不到
                failing = urlparse(getattr(e.request, "url", None) or url).hostname or host
                if insecure or not (failing == "ncu.edu.tw" or failing.endswith(".ncu.edu.tw")):
                    raise
                print(f"⚠️ {failing} 的 TLS 憑證驗證失敗（{str(e)[:120]}），這個網站改用不驗證憑證的方式連線。")
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                self._insecure_hosts.add(failing)
                insecure = True
                continue
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
                continue
            if response.status_code >= 500 and attempt < 2:
                response.close()
                time.sleep(2 * (attempt + 1))
                continue
            return response
        raise FetchError(f"{url} 連線失敗")

    def get(self, url: str, headers: Optional[dict] = None) -> requests.Response:
        host = urlparse(url).hostname or ""
        with self._lock(host):
            if self._failures.get(host, 0) >= HOST_GIVE_UP_AFTER:
                raise FetchError(f"{host} 連續 {HOST_GIVE_UP_AFTER} 次連不上，這一輪先跳過這個網站")
            if not self._robots_allows(url):
                raise RobotsDisallowed("網站的 robots.txt 不允許程式下載")
            wait = self.delay - (time.monotonic() - self._last_request.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            try:
                response = self._request(url, headers or {})
            except (requests.ConnectionError, requests.Timeout) as e:
                self._failures[host] = self._failures.get(host, 0) + 1
                raise FetchError(str(e)[:200]) from e
            except requests.RequestException as e:
                raise FetchError(str(e)[:200]) from e
            finally:
                self._last_request[host] = time.monotonic()
            self._failures[host] = 0
            return response

    @staticmethod
    def read(response: requests.Response) -> bytes:
        chunks, size = [], 0
        try:
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise FetchError(f"檔案超過 {MAX_FILE_BYTES // 1024 // 1024}MB，略過")
                chunks.append(chunk)
        except requests.RequestException as e:
            raise FetchError(str(e)[:200]) from e
        finally:
            response.close()
        return b"".join(chunks)

    def get_html(self, url: str) -> tuple[str, str]:
        response = self.get(url)
        if response.status_code != 200:
            response.close()
            raise FetchError(f"HTTP {response.status_code}")
        data = self.read(response)
        html = decode_html(data, response.headers.get("Content-Type", ""))
        # 藝術所的頁面開頭先多了一組 </body></html>，lxml 會把後面整頁當成文件結束之後的東西丟掉
        return re.sub(r"^\s*(</(body|html)>\s*)+", "", html, flags=re.I), response.url

    def get_bytes(self, url: str) -> bytes:
        response = self.get(url)
        if response.status_code != 200:
            response.close()
            raise FetchError(f"HTTP {response.status_code}")
        return self.read(response)


RENDER_DELAY = 1.0  # 用瀏覽器開的網頁每頁都會載入一堆 JavaScript、圖片，頁面之間多隔一點
RENDER_SETTLE_MS = 1500  # 網路靜下來之後再等一下，讓 JavaScript 把內容畫完


class Renderer:
    """網頁內容是 JavaScript 載入的網站（Source.render，例如總務處）：網頁本身只有空殼，內容是前端呼叫
    要授權的 API 畫出來的。這種網站用 Playwright 的 Chromium 像一般瀏覽器一樣打開、等內容畫好，
    再把畫好的 HTML 交給一般的解析，不去呼叫它的 API。

    跟 Fetcher 一樣遵守 robots.txt。第一次用到才啟動瀏覽器，整輪爬完要呼叫 close()。
    只在主執行緒用（Playwright 的同步 API 不能跨執行緒），collect 本來就是一頁一頁讀的。
    """

    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher
        self._playwright = None
        self._browser = None

    def get_html(self, url: str) -> tuple[str, str]:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        if not self.fetcher.allows(url):
            raise RobotsDisallowed("網站的 robots.txt 不允許程式讀取")
        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch()
        page = self._browser.new_page(user_agent=USER_AGENT, locale="zh-TW")
        try:
            response = page.goto(url, wait_until="networkidle", timeout=60_000)
            if response is not None and response.status >= 400:
                raise FetchError(f"HTTP {response.status}")
            page.wait_for_timeout(RENDER_SETTLE_MS)
            return page.content(), page.url
        except PlaywrightError as e:
            raise FetchError(f"瀏覽器開不了這個網頁：{str(e)[:150]}") from e
        finally:
            page.close()
            time.sleep(RENDER_DELAY)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._browser = self._playwright = None


def decode_html(data: bytes, content_type: str) -> str:
    """HTTP 標頭或 <meta> 宣告的編碼優先（舊網站還有 Big5），都沒有就先試 UTF-8。"""
    declared = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if not declared:
        declared = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", data[:4096], re.I)
    encoding = declared.group(1) if declared else None
    if isinstance(encoding, bytes):
        encoding = encoding.decode("ascii", "ignore")
    aliases = {"big5": "cp950", "big-5": "cp950", "gb2312": "gbk"}
    candidates = list(dict.fromkeys(filter(None, (aliases.get((encoding or "").lower(), encoding), "utf-8"))))
    for candidate in candidates:
        try:
            return data.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    # 只有零星幾個壞掉的位元組（WordPress 把文章摘要截在中文字的中間）時還是照宣告的編碼解，
    # 不然整頁會被當成 Big5 解成亂碼
    for candidate in candidates:
        try:
            text = data.decode(candidate, errors="replace")
        except LookupError:
            continue
        if text.count("\ufffd") <= max(3, len(text) // 5000):
            return text
    return data.decode("cp950", errors="replace")


def _google_confirm_url(html: str) -> Optional[str]:
    """Google 雲端大檔案會先回一頁「無法掃描病毒」的確認頁，照表單送出才拿得到檔案。"""
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form", id="download-form") or soup.find("form", action=re.compile("download"))
    if form is None:
        return None
    params = "&".join(f"{i['name']}={i.get('value', '')}" for i in form.find_all("input", attrs={"name": True}))
    return f"{form['action']}?{params}"


def _viewer_file_url(html: str, page_url: str) -> Optional[str]:
    """Orbit 系統（網址有 zh_tw 的系所網站）的 /xhr/archive/download 有時候回傳 PDF.js 線上檢視器，
    真正的檔案路徑寫在檢視器頁面裡（uploads/archive_file_multiple/file/…/檔名.pdf）。"""
    if "pdf.js" not in html.lower() and "PDFViewerApplication" not in html and "Mozilla Foundation" not in html:
        return None
    match = re.search(r"""["'(]?/?(uploads/[^"'\s<>()]+?\.(?:pdf|docx?|odt))""", html, re.I)
    return absolute_url("/" + match.group(1), page_url) if match else None


# ============================================================
# 紀錄檔（manifest）
# ============================================================
class Manifest:
    """data/.crawler_manifest.json：key 是 data/ 底下的相對路徑。"""

    def __init__(self, path: Path = MANIFEST_PATH):
        self.path = path
        try:
            self.files: dict[str, dict] = json.loads(path.read_text(encoding="utf-8")).get("files", {})
        except (OSError, ValueError):
            self.files = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "files": dict(sorted(self.files.items()))}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def of_source(self, source: str) -> dict[str, dict]:
        return {path: entry for path, entry in self.files.items() if entry.get("source") == source}

    def find_by_url(self, url: str, source: str) -> Optional[tuple[str, dict]]:
        key = url_key(url)
        for path, entry in self.of_source(source).items():
            if entry.get("kind") == "file" and key in {url_key(u) for u in [entry.get("url", "")] + entry.get("aliases", [])}:
                return path, entry
        return None


# ============================================================
# 爬一個來源
# ============================================================
@dataclass
class LinkInfo:
    url: str
    names: list[tuple[int, str]] = field(default_factory=list)
    pages: list[tuple[str, str]] = field(default_factory=list)  # (網址, 頁面標題)


@dataclass
class Download:
    links: list[LinkInfo]
    data: Optional[bytes]  # None：伺服器說沒變（304）
    extension: str
    sha256: str
    final_url: str
    etag: str = ""
    last_modified: str = ""
    old_path: str = ""
    extra_names: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class Report:
    source: str
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    renamed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pages_ok: bool = True


def excluded(source: Source, names: list[str]) -> bool:
    return any(re.search(pattern, name) for pattern in source.exclude for name in names if name)


def current_academic_year(now: Optional[datetime] = None) -> int:
    """民國學年度，8 月開始新的學年。"""
    now = now or datetime.now()
    return now.year - 1911 - (0 if now.month >= 8 else 1)


# 「110學年度入學」「114級」「111-113 學年度」；後面接「起」「以後」的是「從那年開始適用」，不算舊
_COHORT_YEAR = re.compile(
    r"(?<!\d)(\d{2,3})\s*(?:[-~～至]\s*(\d{2,3})\s*)?(?:學年度?|級)(?!度)"
    # 後面接「起」「以後」（可能先有「入學生」）是「從那年開始適用」，接「修訂」「通過」是修法日期，都不是舊的入學年度
    r"(?!\s*[(（]?含?[)）]?\s*(?:入學(?:新?生)?)?\s*(?:起|以後|之後|後|以上))"
    r"(?!\s*(?:第\s*\d+\s*次)?\s*(?:修訂|修正|通過|核備|訂定|制定|系務|院務|所務|教務|會議))"
)


def too_old(name: str, now: Optional[datetime] = None) -> bool:
    years = [int(y) for match in _COHORT_YEAR.finditer(name) for y in match.groups() if y]
    return bool(years) and max(years) < current_academic_year(now) - COHORT_YEARS_KEPT


def global_skip_reason(source: Source, name: str) -> str:
    """全校共用的規則（crawler_sources.GLOBAL_EXCLUDE、入學年度太舊）要不要略過，回傳原因或空字串。"""
    if not name or any(re.search(pattern, name) for pattern in source.include):
        return ""
    if any(re.search(pattern, name) for pattern in GLOBAL_EXCLUDE):
        return "不是學生用的文件"
    if too_old(name):
        return "入學年度太舊"
    return ""


def safe_filename(name: str, max_length: int = 90) -> str:
    table = str.maketrans({"\\": "＼", "/": "／", ":": "：", "*": "＊", "?": "？", '"': "＂", "<": "＜", ">": "＞", "|": "｜"})
    name = re.sub(r"[\x00-\x1f]", "", name.translate(table)).strip(" .")
    return name[:max_length].rstrip(" .") or "未命名文件"


def _series_key(name: str, match: re.Match) -> str:
    return (name[:match.start()] + name[match.end():]).strip()


_TERM_NUMBER = {"1": 1, "一": 1, "上": 1, "2": 2, "二": 2, "下": 2}


def _version_number(match: re.Match) -> Optional[int]:
    """第一個群組是年度；有第二個群組的話是學期（1、一、上…），合成 年度*10+學期 來比大小。"""
    if not (match.group(1) or "").isdigit():
        return None
    term = _TERM_NUMBER.get(match.group(2) or "", 0) if match.re.groups >= 2 else 0
    return int(match.group(1)) * 10 + term


def keep_latest(items: list, name_of, patterns: tuple[str, ...]) -> tuple[list, list]:
    """名稱符合 pattern 的，同一系列（年度以外都一樣）只留年度（學期）最新的。回傳（保留, 捨棄）。"""
    keep, drop = list(items), []
    for pattern in patterns:
        series: dict[str, list[tuple[int, object]]] = {}
        for item in keep:
            match = re.search(pattern, name_of(item))
            number = _version_number(match) if match else None
            if number is not None:
                series.setdefault(_series_key(name_of(item), match), []).append((number, item))
        for members in series.values():
            newest = max(year for year, _ in members)
            drop.extend(item for year, item in members if year != newest)
        dropped = {id(item) for item in drop}
        keep = [item for item in keep if id(item) not in dropped]
    return keep, drop


def disambiguate(planned: list[tuple[str, "Download"]]) -> list[tuple[str, "Download"]]:
    """不同文件取到同一個名字（碩士班、博士班頁面都叫「英文版修業辦法」）時，如果它們來自不同
    頁面，在名字後面補上頁面標題，同一頁上真的重複的就交給 _unique_path 編號。
    """
    groups: dict[str, list[tuple[str, Download]]] = {}
    for name, item in planned:
        groups.setdefault(name, []).append((name, item))
    result = []
    for name, members in groups.items():
        titles = [next((t for info in item.links for _, t in info.pages if t and t not in name), "") for _, item in members]
        if len(members) > 1 and all(titles) and len(set(titles)) == len(titles):
            members = [(f"{name}（{title}）", item) for (_, item), title in zip(members, titles)]
        result.extend(members)
    return result


def ee_documents(fetcher: Fetcher) -> list[tuple[str, list[tuple[int, str]], str]]:
    """電機系網站（www2.ee.ncu.edu.tw）的「表格辦法」頁是前端用 JavaScript 呼叫 data.ee.ncu.edu.tw 的 API
    畫出來的，網頁本身沒有任何連結。type 1、2 是網頁上的兩個分頁，一頁 20 筆，一筆可能有好幾個檔案。
    回傳（檔案網址, 候選名稱, 所在網頁）。
    """
    api = "https://data.ee.ncu.edu.tw"
    found = []
    for kind in (1, 2):
        page_url = f"https://www2.ee.ncu.edu.tw/approach_table.html?type={kind}"
        current = 1
        while True:
            data = json.loads(fetcher.get_bytes(
                f"{api}/ApproachTable?approach_table_type={kind}&current={current}&rowCount=20&searchPhrase="
            ))
            for row in data.get("rows", []):
                files = json.loads(fetcher.get_bytes(f"{api}/ApproachTableAllFile?id={row['approach_table_id']}"))
                title = row.get("approach_table_title") or ""
                for file in files:
                    stem = Path(file.get("att_file_name") or "").stem
                    # 一筆只有一個檔案時用那一筆的標題，好幾個檔案時用各自的檔名，名稱才不會撞在一起
                    names = [(P_LINK_TEXT, title), (P_URL, stem)] if len(files) == 1 else [(P_LINK_TEXT, stem), (P_HEADING, title)]
                    found.append((f"{api}/ApproachTableFile?id={file['att_id']}", names, page_url))
            if current * int(data.get("rowCount") or 20) >= int(data.get("total") or 0):
                break
            current += 1
    return found


# Source.api 對應的函式：網頁沒有連結、文件清單要另外呼叫網站 API 取得的來源
API_PROVIDERS = {"ee": ee_documents}


class Crawler:
    def __init__(self, fetcher: Fetcher, manifest: Manifest, dry_run: bool = False):
        self.fetcher = fetcher
        self.manifest = manifest
        self.dry_run = dry_run
        self.run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.renderer = Renderer(fetcher)

    # ---------- 頁面 ----------
    def collect(self, source: Source, report: Report) -> tuple[dict[str, LinkInfo], list[tuple[Page, str, str]]]:
        links: dict[str, LinkInfo] = {}
        snapshots: list[tuple[Page, str, str]] = []
        pages = {url_key(p.url): p for p in source.pages}
        queue = [(url, 0) for url in source.seeds] + [(p.url, 0) for p in source.pages]
        seen: set[str] = set()
        if source.api:
            try:
                for doc_url, names, page_url in API_PROVIDERS[source.api](self.fetcher):
                    info = links.setdefault(url_key(doc_url), LinkInfo(doc_url))
                    info.names.extend(names)
                    info.pages.append((page_url, ""))
            except (FetchError, ValueError, KeyError, TypeError) as e:
                report.errors.append(f"讀不到 {source.name} 的文件清單（{source.api} API）：{e}")
                report.pages_ok = False
        while queue:
            url, depth = queue.pop(0)
            if url_key(url) in seen:
                continue
            seen.add(url_key(url))
            try:
                html, final_url = (self.renderer if source.render else self.fetcher).get_html(url)
            except FetchError as e:
                report.errors.append(f"讀不到頁面 {url}：{e}")
                report.pages_ok = False
                continue
            soup = BeautifulSoup(html, "lxml")
            title = page_title(soup)
            base = base_url(soup, final_url)
            for doc_url, names in find_document_links(soup, base):
                info = links.setdefault(url_key(doc_url), LinkInfo(doc_url))
                info.names.extend(names)
                info.pages.append((final_url, title))
            if depth == 0 and source.follow:
                queue.extend((target, 1) for target in follow_targets(soup, base, source.follow))
            page = pages.get(url_key(url))
            if page is not None:
                snapshots.append((page, final_url, html))
        return links, snapshots

    # ---------- 文件 ----------
    def download(self, info: LinkInfo, source: Source) -> Download:
        known = self.manifest.find_by_url(info.url, source.name)
        headers = {}
        if known and (DATA_DIR / known[0]).exists():
            if known[1].get("etag"):
                headers["If-None-Match"] = known[1]["etag"]
            if known[1].get("last_modified"):
                headers["If-Modified-Since"] = known[1]["last_modified"]
        response = self.fetcher.get(google_download_url(info.url) or info.url, headers)
        if response.status_code == 304 and known:
            response.close()
            # 檔案內容裡的名稱（Content-Disposition、PDF 標題）沿用上次記下來的，檔名才不會跳來跳去
            remembered = [(priority, name) for priority, name in known[1].get("file_names", [])]
            return Download([info], None, Path(known[0]).suffix, known[1]["sha256"], known[1].get("final_url", info.url),
                            known[1].get("etag", ""), known[1].get("last_modified", ""), known[0], remembered)
        if response.status_code != 200:
            response.close()
            raise FetchError(f"HTTP {response.status_code}")
        etag, last_modified = response.headers.get("ETag", ""), response.headers.get("Last-Modified", "")
        final_url, disposition = response.url, response.headers.get("Content-Disposition", "")
        data = self.fetcher.read(response)
        extension = sniff_extension(data)
        if extension == ".html" and urlparse(final_url).hostname and "google" in urlparse(final_url).hostname:
            confirm = _google_confirm_url(data.decode("utf-8", "ignore"))
            if confirm:
                data = self.fetcher.get_bytes(confirm)
                extension = sniff_extension(data)
        elif extension == ".html":
            embedded = _viewer_file_url(data.decode("utf-8", "ignore"), final_url)
            if embedded:
                data = self.fetcher.get_bytes(embedded)
                extension, final_url = sniff_extension(data), embedded
        extra = [(P_DISPOSITION, disposition_name(disposition)), (P_URL, url_name(final_url))]
        if extension == ".pdf":
            extra.append((P_FALLBACK, pdf_title(data)))
        return Download([info], data, extension, hashlib.sha256(data).hexdigest(), final_url, etag, last_modified,
                        known[0] if known else "", [e for e in extra if e[1]])

    def download_all(self, infos: list[LinkInfo], source: Source, report: Report) -> tuple[list[Download], set[str]]:
        """回傳（下載結果, 下載失敗的網址 key）。不同網站平行下載，同一個網站由 Fetcher 排隊。"""
        def task(info: LinkInfo):
            try:
                return self.download(info, source)
            except FetchError as e:
                return e

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(task, infos))
        downloads, failed = [], set()
        for info, result in zip(infos, results):
            name = choose_name(info.names) or info.url
            if isinstance(result, RobotsDisallowed):
                report.skipped.append(f"{name}（{result}）")
            elif isinstance(result, FetchError):
                report.errors.append(f"下載失敗 {name}：{result}（{info.url}）")
                failed.add(url_key(info.url))
            else:
                downloads.append(result)
        return downloads, failed

    def crawl(self, source: Source) -> Report:
        report = Report(source.name)
        folder = DATA_DIR / source.name
        links, snapshots = self.collect(source, report)

        # 1. 先用網頁上看得到的名稱排除不要的、同名不同格式的只留一種，省得全部下載。
        #    排除規則比對所有候選名稱：「自110學年度起適用」這種連結，口試費的字樣只在上一行。
        named = []
        for info in links.values():
            name = choose_name(info.names) or choose_name([(P_FALLBACK, f"{t}附件") for _, t in info.pages])
            context_names = [clean_name(text) for priority, text in info.names if priority > P_URL]
            if excluded(source, [name] + context_names):
                report.skipped.append(f"{name}（設定排除）")
            elif global_skip_reason(source, name):
                report.skipped.append(f"{name}（{global_skip_reason(source, name)}）")
            else:
                named.append((info, name))
        by_name: dict[str, list[tuple[LinkInfo, str]]] = {}
        for info, name in named:
            by_name.setdefault(re.sub(r"\s", "", name).lower(), []).append((info, name))
        wanted = []
        backups: list[tuple[list[LinkInfo], LinkInfo, str]] = []  # (挑中的格式, 備用的格式, 名稱)
        rank = {ext: i for i, ext in enumerate(PREFERRED_TYPES)}
        for group in by_name.values():
            extensions = [url_extension(info.url) for info, _ in group]
            if len(group) > 1 and all(extensions) and len(set(extensions)) > 1:
                best = min(extensions, key=lambda ext: rank.get(ext, 99))
                chosen = [info for (info, _), extension in zip(group, extensions) if extension == best]
                wanted.extend(chosen)
                others = sorted(((info, name, ext) for (info, name), ext in zip(group, extensions) if ext != best),
                                key=lambda other: rank.get(other[2], 99))
                for info, name, extension in others:
                    report.skipped.append(f"{name}{extension}（同一份文件已經有 {best} 版）")
                backups.append((chosen, others[0][0], others[0][1]))
            else:
                wanted.extend(info for info, _ in group)

        # 2. 下載，內容相同的合併成一份。挑中的格式連結壞掉（圖書館、土木系都有 PDF 404、ODT 正常的），
        #    改抓另一種格式
        merged: dict[str, Download] = {}
        downloads, failed = self.download_all(wanted, source, report)
        retry = [(backup, name) for chosen, backup, name in backups if all(url_key(info.url) in failed for info in chosen)]
        if retry:
            report.skipped.extend(f"{name}（偏好的格式下載失敗，改抓 {url_extension(backup.url)} 版）" for backup, name in retry)
            more, more_failed = self.download_all([backup for backup, _ in retry], source, report)
            downloads.extend(more)
            failed |= more_failed
        for item in downloads:
            if item.extension not in PREFERRED_TYPES:
                reason = SKIP_REASONS.get(item.extension, "格式認不出來")
                report.skipped.append(f"{choose_name(item.links[0].names) or item.final_url}（{reason}）")
                continue
            if item.sha256 in merged:
                merged[item.sha256].links.extend(item.links)
                merged[item.sha256].extra_names.extend(item.extra_names)
                merged[item.sha256].old_path = merged[item.sha256].old_path or item.old_path
            else:
                merged[item.sha256] = item

        # 3. 決定檔名：所有出現過的名稱一起比，挑最好的
        others = {e["sha256"]: p for p, e in self.manifest.files.items() if e.get("source") != source.name and e.get("sha256")}
        planned = []
        for item in merged.values():
            if item.sha256 in others:
                report.skipped.append(f"{choose_name(item.links[0].names)}（跟 {others[item.sha256]} 是同一個檔案）")
                continue
            name = choose_name([n for info in item.links for n in info.names] + item.extra_names) or "未命名文件"
            reason = "設定排除" if excluded(source, [name]) else global_skip_reason(source, name)
            if reason:
                report.skipped.append(f"{name}（{reason}）")
                continue
            planned.append((name, item))
        planned = self._prefer_format(planned, report)
        planned, older = keep_latest(planned, lambda pair: pair[0], source.latest_only + TERM_LATEST_ONLY)
        report.skipped.extend(f"{name}（只留最新年度）" for name, _ in older)
        planned = disambiguate(planned)

        # 4. 寫檔。手動放的檔案、其他文件目前的檔名都先保留給原主，不會被搶走蓋掉
        #    （被搶的那個名字等原主這次改名搬走之後，下次執行就會換回來）。
        managed = self.manifest.of_source(source.name)
        # 資料夾裡不在 manifest 的檔案當成手動放的，不能蓋掉。例外是內容跟這次要存的檔案一模一樣的：
        # 那是上次寫到一半中斷（檔案寫了、manifest 還沒存）留下的，收回來用，才不會多出「（2）」
        planned_shas = {item.sha256 for _, item in planned}
        taken = {
            p.relative_to(DATA_DIR).as_posix() for p in folder.glob("*")
            if p.is_file() and p.relative_to(DATA_DIR).as_posix() not in managed
            and hashlib.sha256(p.read_bytes()).hexdigest() not in planned_shas
        }
        owners = {item.old_path: item for _, item in planned if item.old_path}
        kept: set[str] = set()
        moved: set[str] = set()
        for name, item in sorted(planned, key=lambda pair: (not pair[1].old_path, pair[0], pair[1].sha256)):
            blocked = taken | {path for path, owner in owners.items() if owner is not item}
            path = self._unique_path(source.name, name, item.extension, blocked)
            taken.add(path)
            kept.add(path)
            moved.add(self._write_file(source, path, item, report))
        for page, url, html in snapshots:
            try:
                kept.update(self._write_snapshot(source, page, url, html, report))
            except Exception as e:  # 一頁網頁轉檔失敗不要拖垮整個來源，原本的檔案留著
                report.errors.append(f"{page.name} 存檔失敗：{e}")
                report.pages_ok = False

        # 5. 網站上已經不見的檔案移走（頁面都有讀到、那個網址也沒有下載失敗才移，避免誤刪）
        for path, entry in managed.items():
            if path in kept or path in moved:
                continue
            if not report.pages_ok or url_key(entry.get("url", "")) in failed:
                continue
            report.removed.append(path)
            if not self.dry_run:
                self._move_away(path, REMOVED_DIR / self.run_stamp)
                self.manifest.files.pop(path, None)
        return report

    @staticmethod
    def _prefer_format(planned: list[tuple[str, "Download"]], report: Report) -> list[tuple[str, "Download"]]:
        """網址看不出副檔名（Orbit 的 /xhr/archive/download?file=…）時，同一份文件的 .doc、.pdf 要下載之後
        才知道是兩種格式：同名、格式不同的只留排前面的格式。同名同格式的是不同文件，交給 disambiguate。"""
        rank = {ext: i for i, ext in enumerate(PREFERRED_TYPES)}
        by_name: dict[str, list[tuple[str, Download]]] = {}
        for name, item in planned:
            by_name.setdefault(re.sub(r"\s", "", name).lower(), []).append((name, item))
        result = []
        for group in by_name.values():
            best = min(rank.get(item.extension, 99) for _, item in group)
            for name, item in group:
                if rank.get(item.extension, 99) == best:
                    result.append((name, item))
                else:
                    report.skipped.append(f"{name}{item.extension}（同一份文件已經有 {PREFERRED_TYPES[best]} 版）")
        return result

    def _unique_path(self, source_name: str, name: str, extension: str, taken: set[str]) -> str:
        base = safe_filename(name)
        candidate = f"{source_name}/{base}{extension}"
        number = 2
        while candidate in taken:
            candidate = f"{source_name}/{base}（{number}）{extension}"
            number += 1
        return candidate

    def _move_away(self, relative: str, target_root: Path) -> None:
        source_path = DATA_DIR / relative
        if source_path.exists():
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(target))

    def _write_file(self, source: Source, path: str, item: Download, report: Report) -> str:
        """寫入（或改名、或確認沒變）一個檔案，回傳這次被搬走的舊檔名（沒有就空字串）。"""
        target = DATA_DIR / path
        existing = self.manifest.files.get(path)
        old_path = item.old_path if item.old_path and item.old_path != path else ""
        old_entry = self.manifest.files.get(old_path) if old_path else None
        same_content = existing is not None and existing.get("sha256") == item.sha256 and target.exists()
        # 內容沒變、只是名稱算出來不一樣（例如命名規則改進了）：直接把舊檔改名
        rename_only = bool(old_entry) and old_entry.get("sha256") == item.sha256 and (DATA_DIR / old_path).exists()
        if same_content:
            report.unchanged.append(path)
        elif rename_only:
            report.renamed.append(f"{old_path} → {path}")
        elif old_path:
            report.updated.append(f"{old_path} → {path}")
        elif target.exists():
            report.updated.append(path)
        else:
            report.added.append(path)

        links = item.links
        entry = {
            "source": source.name,
            "kind": "file",
            "url": links[0].url,
            "aliases": sorted({info.url for info in links[1:]} - {links[0].url}),
            "final_url": item.final_url,
            "page_url": links[0].pages[0][0] if links[0].pages else "",
            "page_title": links[0].pages[0][1] if links[0].pages else "",
            "sha256": item.sha256,
            "etag": item.etag,
            "last_modified": item.last_modified,
            "file_names": [[priority, name] for priority, name in dict.fromkeys(item.extra_names)],
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }
        if self.dry_run:
            return old_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if rename_only and not same_content:
            shutil.move(str(DATA_DIR / old_path), str(target))
        elif not same_content and item.data is not None:
            tmp = target.with_name(target.name + ".part")
            tmp.write_bytes(item.data)
            tmp.replace(target)
        if old_path:
            self._move_away(old_path, REMOVED_DIR / self.run_stamp)  # 已經改名搬走的話這裡什麼都不做
            self.manifest.files.pop(old_path, None)
        previous = existing if same_content else old_entry if rename_only else None
        entry["size"] = target.stat().st_size
        entry["fetched_at"] = (previous or {}).get("fetched_at") or datetime.now().isoformat(timespec="seconds")
        self.manifest.files[path] = entry
        return old_path

    def _write_snapshot(self, source: Source, page: Page, url: str, html: str, report: Report) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        base = base_url(soup, url)
        markdown, root = page_markdown(soup, page, url, base)
        written = []
        image_bytes: list[bytes] = []
        image_urls: list[str] = []
        if page.images:
            for image_url in content_images(root, base):
                try:
                    data = self.fetcher.get_bytes(image_url)
                except FetchError as e:
                    report.errors.append(f"讀不到 {page.name} 的圖片 {image_url}：{e}")
                    continue
                width, height = image_size(data)
                if min(width, height) >= 400:
                    image_bytes.append(data)
                    image_urls.append(image_url)
        images_name = f"{safe_filename(page.name)}（圖片）.pdf"
        if image_bytes:
            markdown = markdown.rstrip("\n") + f"\n\n（這個頁面的圖片內容另外存在「{images_name}」。）\n"

        path = f"{source.name}/{safe_filename(page.name)}.md"
        data = markdown.encode("utf-8")
        self._write_generated(source, path, url, page.name, "page", data, hashlib.sha256(data).hexdigest(), report)
        written.append(path)
        if image_bytes:
            # PDF 每次產生的位元組不一定一樣，改用「圖片本身的 hash」判斷有沒有變，沒變就不重做
            # （重做的話 RAG 會以為是新文件，又跑一次圖片轉錄）。
            image_hash = hashlib.sha256(b"".join(hashlib.sha256(d).digest() for d in image_bytes)).hexdigest()
            images_path = f"{source.name}/{images_name}"
            self._write_generated(source, images_path, url, page.name, "page_images", None, image_hash, report,
                                  build=lambda: images_to_pdf(image_bytes), image_urls=image_urls)
            written.append(images_path)
        return written

    def _write_generated(self, source: Source, path: str, url: str, title: str, kind: str, data: Optional[bytes],
                         content_hash: str, report: Report, build=None, image_urls=None) -> None:
        target = DATA_DIR / path
        existing = self.manifest.files.get(path)
        if existing and existing.get("content_hash") == content_hash and target.exists():
            report.unchanged.append(path)
            return
        if target.exists() and not existing:
            report.errors.append(f"{path} 已經有手動放的同名檔案，沒有覆蓋")
            return
        (report.updated if target.exists() else report.added).append(path)
        if self.dry_run:
            return
        data = data if data is not None else build()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
        self.manifest.files[path] = {
            "source": source.name,
            "kind": kind,
            "url": url,
            "page_title": title,
            "content_hash": content_hash,
            "sha256": hashlib.sha256(data).hexdigest(),
            "image_urls": image_urls or [],
            "size": len(data),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }


# ============================================================
# 舊檔檢查：data/ 裡不是爬蟲抓的檔案
# ============================================================
def legacy_files(manifest: Manifest) -> list[tuple[Path, str, bool]]:
    """不在 manifest 裡的檔案：回傳（路徑, 對應的爬蟲檔案, 內容是否一模一樣）。
    對應不到內容相同的，再用「去掉副檔名、標點後同名」找可能的新版（例如資工系把 .doc 換成 .odt）。
    """
    by_sha = {e.get("sha256"): p for p, e in manifest.files.items() if e.get("sha256")}
    by_stem: dict[str, str] = {}
    for path in manifest.files:
        by_stem.setdefault(_comparable(Path(path).stem), path)
    result = []
    for path in sorted(DATA_DIR.rglob("*")):
        relative = path.relative_to(DATA_DIR).as_posix()
        if not path.is_file() or relative in manifest.files or path.name.startswith(".") or path.suffix == ".part":
            continue
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha in by_sha:
            result.append((path, by_sha[sha], True))
        else:
            result.append((path, by_stem.get(_comparable(path.stem), ""), False))
    return result


def _comparable(stem: str) -> str:
    return re.sub(r"[\W_]|國立中央大學", "", clean_name(stem) or stem).lower()


# ============================================================
# 指令列
# ============================================================
def print_report(report: Report, verbose: bool) -> None:
    print(
        f"[{report.source}] 新增 {len(report.added)}、更新 {len(report.updated)}、改名 {len(report.renamed)}、"
        f"沒變 {len(report.unchanged)}、移除 {len(report.removed)}、略過 {len(report.skipped)}、錯誤 {len(report.errors)}"
    )
    sections = [("新增", report.added), ("更新", report.updated), ("改名", report.renamed), ("移除", report.removed),
                ("錯誤", report.errors)]
    if verbose:
        sections[1:1] = [("沒變", report.unchanged)]
        sections.append(("略過", report.skipped))
    for label, items in sections:
        for item in items:
            print(f"  {label}：{item}")


def main() -> None:
    parser = argparse.ArgumentParser(description="把學校網站的法規、表單抓到 data/，給校園法規問答用")
    parser.add_argument("--source", action="append", default=[], help="只爬這個來源（可重複指定）")
    parser.add_argument("--dry-run", action="store_true", help="只列出會做什麼，不寫入 data/")
    parser.add_argument("--list", action="store_true", help="列出設定好的來源")
    parser.add_argument("--legacy", action="store_true", help="檢查 data/ 裡不是爬蟲抓的檔案")
    parser.add_argument("--move-duplicate-legacy", action="store_true",
                        help="把跟爬到的檔案內容一模一樣的舊檔移到 storage/crawler/legacy_backup/")
    parser.add_argument("-v", "--verbose", action="store_true", help="連沒變、略過的檔案都列出來")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")  # logging 印在 stderr，Windows 主控台預設不是 UTF-8

    if args.list:
        for source in SOURCES:
            print(f"{source.name}：{len(source.seeds)} 個頁面、{len(source.pages)} 個存成文件的網頁")
        return

    manifest = Manifest()
    if args.legacy or args.move_duplicate_legacy:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for path, match, identical in legacy_files(manifest):
            relative = path.relative_to(DATA_DIR).as_posix()
            if identical and args.move_duplicate_legacy:
                # 只搬內容一模一樣的：同名但內容不同的，可能是手動放的另一份文件，要人看過再決定
                target = LEGACY_BACKUP_DIR / stamp / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(target))
                print(f"移到備份：{relative}（跟 {match} 內容相同）")
            elif identical:
                print(f"{relative}｜跟 {match} 內容相同")
            elif match:
                print(f"{relative}｜跟 {match} 同名但內容不同（可能是舊版，請自己確認）")
            else:
                print(f"{relative}｜爬到的檔案裡沒有對應的")
        return

    unknown = set(args.source) - {s.name for s in SOURCES}
    if unknown:
        parser.error(f"沒有這個來源：{'、'.join(sorted(unknown))}（用 --list 看有哪些）")
    sources = [s for s in SOURCES if not args.source or s.name in args.source]

    crawler = Crawler(Fetcher(), manifest, dry_run=args.dry_run)
    try:
        _crawl_sources(crawler, sources, manifest, args)
    finally:
        crawler.renderer.close()
    if args.dry_run:
        print("（--dry-run：沒有寫入任何檔案）")


def _crawl_sources(crawler: "Crawler", sources: list, manifest: "Manifest", args) -> None:
    for source in sources:
        print(f"[{source.name}] 開始")
        try:
            report = crawler.crawl(source)
        except Exception as e:  # 一個網站出問題（改版、程式沒料到的狀況），其他來源照樣抓
            print(f"[{source.name}] 發生錯誤，這個來源先跳過：{e!r}")
            continue
        finally:
            if not args.dry_run:
                manifest.save()  # 每個來源做完（或出錯、按 Ctrl+C）都存，已經寫進 data/ 的檔案才記得是爬蟲抓的
        print_report(report, args.verbose or args.dry_run)


if __name__ == "__main__":
    main()
