"""crawler.py 的測試，不連網路（HTML 片段都是從實際網站複製下來的）：
    python -m pytest tests/test_crawler.py
"""

import io
import zipfile

import fitz
import pytest
import requests
from bs4 import BeautifulSoup

from backend.rag import crawler
from backend.rag.crawler import (
    Crawler,
    Fetcher,
    Manifest,
    absolute_url,
    choose_name,
    clean_name,
    disposition_name,
    find_document_links,
    follow_targets,
    google_download_url,
    keep_latest,
    page_markdown,
    sniff_extension,
    url_key,
)
from backend.rag.crawler_sources import Follow, Page, Source


def names_on(html: str, page_url: str = "https://pdc.adm.ncu.edu.tw/p/412-1019-1706.php") -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    return [choose_name(candidates) for _, candidates in find_document_links(soup, page_url)]


# ------------------------------------------------------------
# 文件名稱
# ------------------------------------------------------------
def test_clean_name_strips_site_boilerplate():
    assert clean_name("下載 PDF：1-01 學籍與成績業務申辦委託書－委託書（另開新視窗）") == "1-01 學籍與成績業務申辦委託書－委託書"
    assert clean_name("國立中央大學全英語授課課程獎勵辦法中文版.pdf檔，另開新視窗") == "國立中央大學全英語授課課程獎勵辦法中文版"
    assert clean_name("國立中央大學學生宿舍門禁刷卡施行要點_1091218.pdf(另開新視窗開啟PDF檔案)") == "國立中央大學學生宿舍門禁刷卡施行要點_1091218"
    assert clean_name("國立中央大學學則（PDF）") == "國立中央大學學則"
    assert clean_name("論文指導變更研究領域申請表.doc") == "論文指導變更研究領域申請表"
    assert clean_name("form01-11國立中央大學學生更改個人身份資料.odt(下載)") == "國立中央大學學生更改個人身份資料"
    # 網站把標題重複貼了三次
    assert clean_name("教學傑出暨優良獎評審實施細則教學傑出暨優良獎評審實施細則教學傑出暨優良獎評審實施細則.pdf檔") == "教學傑出暨優良獎評審實施細則"
    # 中文字中間被拆開的空白
    assert clean_name("生醫科學與工程學 系") == "生醫科學與工程學系"


def test_clean_name_rejects_generic_link_text():
    for text in ("下載 PDF", "下載 ODT", "PDF", "ODT", ".odt", "PDF檔", "按我下載pdf", "查看", "」", "點我下載", "原頁面開啟"):
        assert clean_name(text) == "", text


def test_registration_forms_take_the_name_from_the_title_attribute():
    # 教務處註冊組表格下載：連結文字只有「下載 PDF」「下載 ODT」
    html = """<ul>
    <li>1-01 學籍與成績業務申辦委託書 》委託書<a href="/static/file/19/1019/img/229/775388033.pdf"
        title="下載 PDF：1-01 學籍與成績業務申辦委託書－委託書（另開新視窗）">下載 PDF</a></li>
    <li>1-02 保留入學資格 》<a href="/static/file/19/1019/img/229/134785693.odt"
        title="下載 ODT：1-02 保留入學資格（另開新視窗）">下載 ODT</a></li>
    <li>1-10 學生證遺失補發 》<a href="https://portal.ncu.edu.tw/login" title="掛失：1-10 學生證遺失補發（另開新視窗）">掛失</a>
        》說明<a href="/static/file/19/1019/img/229/178065164.pdf" title="下載 PDF：1-10 學生證遺失補發－說明（另開新視窗）">下載 PDF</a></li>
    </ul>"""
    assert names_on(html) == ["1-01 學籍與成績業務申辦委託書－委託書", "1-02 保留入學資格", "1-10 學生證遺失補發－說明"]


def test_same_line_text_is_used_when_the_link_has_no_title():
    # 離校生專區：連結文字是「.odt」，title 又是一串表單代碼，用同一行前面的文字
    html = """<p>1-11 學生更改個人身份資料 》<a href="/s/reg-form1-11">.odt</a></p>"""
    assert names_on(html) == ["1-11 學生更改個人身份資料"]


def test_language_center_links_that_only_wrap_a_quote_mark():
    html = """<ul>
    <li>理院生：辦理請洽各院辦，請填妥以下「理學院大學部外文能力鑑定審核申請表<a href="/files/system/files/理學院學生達到外文畢業門檻及獎勵金申請表.pdf">」</a>。</li>
    <li>地科學院生：辦理請洽各系，請填妥以下<a href="/files/system/files/03地科.pdf">「</a>地科學院大學部外文能力鑑定審核申請表<a href="/files/system/files/03地科.pdf">」</a>。</li>
    </ul>"""
    names = names_on(html, "https://www.lc.ncu.edu.tw/zh-TW/article/x")
    assert names == ["理學院大學部外文能力鑑定審核申請表"] + ["地科學院大學部外文能力鑑定審核申請表"] * 2


def test_download_attribute_wins_over_a_misleading_url():
    html = """<a class="attach_pdf" download="國立中央大學工學院大學部外文畢業門檻審核申請表(114學年後新生適用).pdf"
        href="/files/system/files/國立中央大學工學院(109學年起適用)「外文能力鑑定」暨「外文能力獎勵」審核申請表_v20241226(2).pdf">
        國立中央大學工學院大學部外文畢業門檻審核申請表(114學年後新生適用).pdf</a>"""
    assert names_on(html, "https://www.lc.ncu.edu.tw/zh-TW/article/x") == ["國立中央大學工學院大學部外文畢業門檻審核申請表(114學年後新生適用)"]


def test_icon_links_in_a_table_use_the_text_before_them():
    # 學務處下載區：連結是 PDF 圖示，一格裡有中、英文兩份文件
    html = """<table><tr><th>種類</th><th>類別</th><th>標題</th><th>業務單位</th></tr>
    <tr><td>法規</td><td>性別平等</td><td>
      國立中央大學校園性別事件防治要點(民國113年11月12日修訂) <a href="thumbs/downloads/20250211143650.pdf"><img src="images/icon-PDF.png"/></a><br/>
      Regulations on the Prevention of Sexual Harassment on Campus <a href="thumbs/downloads/20150312154255.pdf"><img src="images/icon-PDF.png"/></a><br/>
    </td><td>學務處</td></tr></table>"""
    names = names_on(html, "https://osa.ncu.edu.tw/downloads.php")
    assert names == ["國立中央大學校園性別事件防治要點(民國113年11月12日修訂)", "Regulations on the Prevention of Sexual Harassment on Campus"]


def test_card_layout_uses_the_card_title():
    # 住宿服務組表單下載：每張卡片的 PDF、ODT 按鈕
    html = """<div class="card"><div class="card-body">
      <div class="card-title">01_住宿申請表Application Form for Student Dormitory_1091028</div>
      <p class="card-text"><small>更新日期: 2026/02/24</small></p><p class="card-text"><small>作者: grace566</small></p>
      <div class="d-flex"><a href="/uploads/documents/01_x_pdf.pdf"><i class="bi"></i> PDF</a>
      <a href="/uploads/documents/01_x_odt.odt"><i class="bi"></i> ODT</a></div>
    </div></div>"""
    # 中文名稱後面整串英文翻譯拿掉，檔名才不會長到被截斷
    assert names_on(html, "https://shsd.ncu.edu.tw/Student/FormItems") == ["01_住宿申請表_1091028"] * 2


def test_qualifier_links_get_the_document_name_in_front():
    html = """<p>國立中央大學全英語授課課程獎勵辦法：<a href="/static/file/19/1019/img/241/105618175.pdf">中文版</a>、
    <a href="/static/file/19/1019/img/241/688332940.pdf">英文版</a></p>"""
    assert names_on(html) == ["國立中央大學全英語授課課程獎勵辦法（中文版）", "國立中央大學全英語授課課程獎勵辦法（英文版）"]


def test_wrong_extension_in_link_text_is_dropped():
    html = """<a href="/static/file/13/1013/img/202478975.odt" title="原頁面開啟">論文指導變更研究領域申請表.doc</a>"""
    assert names_on(html, "https://www.csie.ncu.edu.tw/p/412-1013-1096.php") == ["論文指導變更研究領域申請表"]


def test_disposition_name_fixes_google_drive_mojibake():
    garbled = "紙本成績單申請流程.pdf".encode("utf-8").decode("latin-1")
    assert disposition_name(f'attachment; filename="{garbled}"') == "紙本成績單申請流程.pdf"
    assert disposition_name("attachment; filename*=UTF-8''%E5%AD%B8%E5%89%87.pdf") == "學則.pdf"


# ------------------------------------------------------------
# 網址、格式
# ------------------------------------------------------------
def test_absolute_url_handles_backslashes_spaces_and_chinese():
    assert absolute_url("download\\在學役男出境申請須知.docx", "https://military.ncu.edu.tw/Q&A.php") == (
        "https://military.ncu.edu.tw/download/%E5%9C%A8%E5%AD%B8%E5%BD%B9%E7%94%B7%E5%87%BA%E5%A2%83%E7%94%B3%E8%AB%8B%E9%A0%88%E7%9F%A5.docx"
    )
    assert absolute_url("a b.pdf", "https://x.ncu.edu.tw/dir/") == "https://x.ncu.edu.tw/dir/a%20b.pdf"
    for href in ("javascript:void(0);", "#top", "mailto:a@b.c", "", None):
        assert absolute_url(href, "https://x.ncu.edu.tw/") is None


def test_url_key_treats_encoded_and_raw_urls_as_the_same():
    raw = "http://military.ncu.edu.tw/download/學生報告.docx"
    encoded = absolute_url("https://military.ncu.edu.tw/download/%E5%AD%B8%E7%94%9F%E5%A0%B1%E5%91%8A.docx", "https://x/")
    assert url_key(raw) == url_key(encoded)
    assert url_key("https://pdc.adm.ncu.edu.tw/p/412-1019-1706.php?Lang=zh-tw") == url_key("https://pdc.adm.ncu.edu.tw/p/412-1019-1706.php")


def test_google_links_become_direct_downloads():
    assert google_download_url("https://drive.google.com/file/d/1-LevKh6SNRQwwRWd2dauiXH2IwbZybgs/view") == (
        "https://drive.google.com/uc?export=download&id=1-LevKh6SNRQwwRWd2dauiXH2IwbZybgs"
    )
    assert google_download_url("https://docs.google.com/document/d/abcdefghijklmn/edit").endswith("/export?format=pdf")
    assert google_download_url("https://drive.google.com/drive/folders/1SKiWOUw3Yb68kiC5zF14Zfp-A-CWRB9G") is None


def _zip(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _pdf(text: str = "Hello") -> bytes:
    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    return document.tobytes()


def test_sniff_extension_looks_at_content_not_the_name():
    assert sniff_extension(_pdf()) == ".pdf"
    assert sniff_extension(_zip({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": "x"})) == ".odt"
    assert sniff_extension(_zip({"[Content_Types].xml": "x", "word/document.xml": "x"})) == ".docx"
    assert sniff_extension(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100 + "WordDocument".encode("utf-16-le")) == ".doc"
    assert sniff_extension(b"<!DOCTYPE html><html><body>login</body></html>") == ".html"
    assert sniff_extension(b"random bytes") == ""


# ------------------------------------------------------------
# 跟進連結、只留最新
# ------------------------------------------------------------
def test_follow_latest_year_only():
    html = """<a href="/p/412-1019-2330.php">115教務章則彙編</a><a href="/p/412-1019-1993.php">114教務章則彙編</a>
    <a href="/p/412-1019-1720.php">113教務章則彙編</a><a href="/p/412-1019-1725.php">校曆</a>"""
    soup = BeautifulSoup(html, "lxml")
    targets = follow_targets(soup, "https://pdc.adm.ncu.edu.tw/p/412-1019-2070.php", (Follow(text=r"(\d{3})\s*教務章則彙編", latest=True),))
    assert targets == ["https://pdc.adm.ncu.edu.tw/p/412-1019-2330.php"]


def test_keep_latest_only_drops_older_years_of_the_same_series():
    names = ["115 學年度校曆", "114 學年度校曆", "113 學年度校曆", "2026-2027 NCU Academic Calendar", "2025-2026 NCU Academic Calendar", "選課辦法"]
    keep, drop = keep_latest(names, lambda n: n, (r"(\d{3})\s*學年度校曆", r"(\d{4})-\d{4}\s*NCU Academic Calendar"))
    assert keep == ["115 學年度校曆", "2026-2027 NCU Academic Calendar", "選課辦法"]
    assert len(drop) == 3


# ------------------------------------------------------------
# 網頁存成 Markdown
# ------------------------------------------------------------
def test_page_markdown_keeps_tables_lists_and_drops_menus():
    html = """<html><body>
    <nav><a href="/a">首頁</a><a href="/b">最新消息</a></nav>
    <div class="sidebar_menu"><a href="/c">選單一</a></div>
    <div class="container"><h2>國立中央大學各項獎學金</h2>
      <table>
        <tr><th>獎學金名稱</th><th>金額</th><th>名額</th></tr>
        <tr><td rowspan="2">羅家倫校長紀念獎學金</td><td>100,000 元</td><td>3(大學部)</td></tr>
        <tr><td>100,000 元</td><td>3(研究所)</td></tr>
      </table>
      <ul><li>申請請至<a href="https://cis.ncu.edu.tw/Scholarship">獎助學金系統</a></li><li>逾期不受理</li></ul>
    </div>
    <footer>版權所有</footer></body></html>"""
    soup = BeautifulSoup(html, "lxml")
    markdown, _ = page_markdown(soup, Page("https://military.ncu.edu.tw/scholarship.php", "校內獎學金一覽"), "https://military.ncu.edu.tw/scholarship.php")
    assert markdown.startswith("# 校內獎學金一覽\n\n來源網頁：https://military.ncu.edu.tw/scholarship.php")
    assert "## 國立中央大學各項獎學金" in markdown
    # 往下合併的第一欄補回來，每一列都看得出是哪個獎學金
    assert "| 羅家倫校長紀念獎學金 | 100,000 元 | 3(研究所) |" in markdown
    assert "- 申請請至[獎助學金系統](https://cis.ncu.edu.tw/Scholarship)" in markdown
    assert "選單一" not in markdown and "最新消息" not in markdown and "版權所有" not in markdown


def test_page_markdown_drops_view_counters():
    # 瀏覽人次每次抓都不一樣，留著的話每次重抓都會被當成改版
    html = """<html><body><main>
    <div class="view_count pull-right"><i class="fa fa-eye">瀏覽人次:</i><span class="view-count">36080</span></div>
    <div><span class="inline-flex"><svg data-icon="eye"><path d="M0"></path></svg><span>26134</span></span>
      <span class="inline-flex"><svg data-icon="clock"></svg><span>2024-09-16</span></span></div>
    <div class="overview-countries"><h2>大學部修業規定</h2><p>畢業學分 128 學分。</p></div>
    <p>瀏覽次數：120</p>
    </main></body></html>"""
    markdown, _ = page_markdown(BeautifulSoup(html, "lxml"), Page("https://www.chem.ncu.edu.tw/zh_tw/course/UD1", "大學部"),
                                "https://www.chem.ncu.edu.tw/zh_tw/course/UD1")
    assert "36080" not in markdown and "26134" not in markdown and "瀏覽" not in markdown
    assert "2024-09-16" in markdown and "畢業學分 128 學分" in markdown


# ------------------------------------------------------------
# 整個流程（假的網站）
# ------------------------------------------------------------
class FakeResponse:
    def __init__(self, url: str, body: bytes, status: int = 200, headers: dict = None):
        self.url, self.status_code, self.headers, self._body = url, status, headers or {}, body

    def iter_content(self, size):
        yield self._body

    def close(self):
        pass


class FakeFetcher(Fetcher):
    def __init__(self, site: dict):
        super().__init__(delay=0)
        self.site = site
        self.requests: list[str] = []

    def get(self, url, headers=None):
        self.requests.append(url)
        if url not in self.site:
            return FakeResponse(url, b"not found", 404)
        body = self.site[url]
        etag = f'"{hash(body)}"'
        if (headers or {}).get("If-None-Match") == etag:
            return FakeResponse(url, b"", 304)
        content_type = "text/html; charset=utf-8" if body.lstrip().startswith(b"<") else "application/octet-stream"
        return FakeResponse(url, body, 200, {"ETag": etag, "Content-Type": content_type})


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(crawler, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(crawler, "REMOVED_DIR", tmp_path / "removed")
    return tmp_path / "data"


SITE = "https://pdc.adm.ncu.edu.tw"
FORMS_PAGE = f"{SITE}/p/forms.php"
SOURCE = Source(name="註冊組", seeds=(FORMS_PAGE,), exclude=(r"行政人員",))


def forms_page(*items: str) -> bytes:
    return f"<html><head><title>表格下載 - 教務處</title></head><body><ul>{''.join(items)}</ul></body></html>".encode()


def test_crawl_writes_renames_skips_and_removes(data_dir):
    leave_form = _pdf("leave")
    site = {
        FORMS_PAGE: forms_page(
            '<li>1-04 休學離校申請表 》<a href="/s/reg-form1-04" title="下載 PDF：1-04 休學離校申請表（另開新視窗）">下載 PDF</a></li>',
            # 同一份檔案又用另一個網址連一次
            '<li>休學申請 》<a href="/static/file/1/1.pdf">下載</a></li>',
            '<li>1-15 轉系 》<a href="/static/file/2/2.odt">ODT</a> <a href="/static/file/2/2.pdf">PDF</a></li>',
            '<li>1-16 行政人員系統申請表 》<a href="/static/file/3/3.odt">ODT</a></li>',
        ),
        f"{SITE}/s/reg-form1-04": leave_form,
        f"{SITE}/static/file/1/1.pdf": leave_form,
        f"{SITE}/static/file/2/2.odt": _zip({"mimetype": "application/vnd.oasis.opendocument.text"}),
        f"{SITE}/static/file/2/2.pdf": _pdf("transfer"),
    }
    manifest = Manifest(data_dir / ".crawler_manifest.json")
    (data_dir / "註冊組").mkdir(parents=True)
    (data_dir / "註冊組" / "手動放的檔案.pdf").write_bytes(b"manual")

    report = Crawler(FakeFetcher(site), manifest).crawl(SOURCE)
    manifest.save()
    assert sorted(report.added) == ["註冊組/1-04 休學離校申請表.pdf", "註冊組/1-15 轉系.pdf"]
    assert any("行政人員" in s for s in report.skipped)
    assert any("1-15 轉系.odt" in s for s in report.skipped)  # 同名的 ODT 不用再抓一份
    assert (data_dir / "註冊組" / "1-04 休學離校申請表.pdf").read_bytes() == site[f"{SITE}/s/reg-form1-04"]

    # 第二次：伺服器說沒變（304），什麼都不用寫
    fetcher = FakeFetcher(site)
    report = Crawler(fetcher, Manifest(data_dir / ".crawler_manifest.json")).crawl(SOURCE)
    assert not report.added and not report.updated and len(report.unchanged) == 2

    # 網站拿掉轉系表：移到 removed，手動放的檔案不受影響
    site[FORMS_PAGE] = forms_page(
        '<li>1-04 休學離校申請表 》<a href="/s/reg-form1-04" title="下載 PDF：1-04 休學離校申請表（另開新視窗）">下載 PDF</a></li>'
    )
    manifest = Manifest(data_dir / ".crawler_manifest.json")
    report = Crawler(FakeFetcher(site), manifest).crawl(SOURCE)
    assert report.removed == ["註冊組/1-15 轉系.pdf"]
    assert not (data_dir / "註冊組" / "1-15 轉系.pdf").exists()
    assert (data_dir / "註冊組" / "手動放的檔案.pdf").read_bytes() == b"manual"
    assert "註冊組/1-15 轉系.pdf" not in manifest.files


def test_renamed_link_moves_the_file_instead_of_downloading_a_copy(data_dir):
    site = {FORMS_PAGE: forms_page('<li>1-15 轉系 》<a href="/static/file/2/2.pdf">PDF</a></li>'), f"{SITE}/static/file/2/2.pdf": _pdf()}
    manifest = Manifest(data_dir / ".crawler_manifest.json")
    Crawler(FakeFetcher(site), manifest).crawl(SOURCE)

    # 網站把名稱改長了，檔案內容沒變
    site[FORMS_PAGE] = forms_page('<li>1-15 轉系(所、組、學位學程)申請表 》<a href="/static/file/2/2.pdf">PDF</a></li>')
    report = Crawler(FakeFetcher(site), manifest).crawl(SOURCE)
    assert report.renamed == ["註冊組/1-15 轉系.pdf → 註冊組/1-15 轉系(所、組、學位學程)申請表.pdf"]
    assert not report.removed and not report.added
    assert sorted(p.name for p in (data_dir / "註冊組").iterdir()) == ["1-15 轉系(所、組、學位學程)申請表.pdf"]
    assert list(manifest.files) == ["註冊組/1-15 轉系(所、組、學位學程)申請表.pdf"]


def test_crawl_keeps_files_when_the_site_is_down(data_dir):
    site = {FORMS_PAGE: forms_page('<li>1-15 轉系 》<a href="/static/file/2/2.pdf">PDF</a></li>'), f"{SITE}/static/file/2/2.pdf": _pdf()}
    manifest = Manifest(data_dir / ".crawler_manifest.json")
    Crawler(FakeFetcher(site), manifest).crawl(SOURCE)

    report = Crawler(FakeFetcher({}), manifest).crawl(SOURCE)  # 整個網站連不上
    assert not report.pages_ok and not report.removed
    assert (data_dir / "註冊組" / "1-15 轉系.pdf").exists()


def _flaky_fetcher(monkeypatch, down: set[str]):
    """_request 對 down 裡的網站一律連不上，其他網站回 200，並記下真的發出去的請求。"""
    fetcher, sent = Fetcher(delay=0), []

    def fake_request(url, headers, stream=True):
        sent.append(url)
        if any(host in url for host in down):
            raise requests.ConnectionError("timed out")
        return FakeResponse(url, b"ok")

    monkeypatch.setattr(fetcher, "_request", fake_request)
    monkeypatch.setattr(fetcher, "_robots_allows", lambda url: True)
    return fetcher, sent


def test_fetcher_skips_a_site_that_keeps_failing(monkeypatch):
    # 網站掛掉時每個請求都要等逾時，連續失敗幾次之後這一輪就不再連它，別的網站照常
    fetcher, sent = _flaky_fetcher(monkeypatch, {"military.ncu.edu.tw"})
    for i in range(crawler.HOST_GIVE_UP_AFTER):
        with pytest.raises(crawler.FetchError, match="timed out"):
            fetcher.get(f"https://military.ncu.edu.tw/{i}.pdf")
    with pytest.raises(crawler.FetchError, match="這一輪先跳過"):
        fetcher.get("https://military.ncu.edu.tw/forms.php")
    assert len(sent) == crawler.HOST_GIVE_UP_AFTER  # 放棄之後不會再真的連
    assert fetcher.get("https://pdc.adm.ncu.edu.tw/a.pdf").status_code == 200


def test_one_success_resets_the_failure_count(monkeypatch):
    down = {"military.ncu.edu.tw"}
    fetcher, sent = _flaky_fetcher(monkeypatch, down)
    for _ in range(3):
        with pytest.raises(crawler.FetchError):
            fetcher.get("https://military.ncu.edu.tw/a.pdf")  # 失敗一次
        down.clear()
        fetcher.get("https://military.ncu.edu.tw/b.pdf")  # 接著成功，重新計算
        down.add("military.ncu.edu.tw")
    assert len(sent) == 6


def test_crawl_never_overwrites_a_manual_file_with_the_same_name(data_dir):
    site = {FORMS_PAGE: forms_page('<li>1-15 轉系 》<a href="/static/file/2/2.pdf">PDF</a></li>'), f"{SITE}/static/file/2/2.pdf": _pdf()}
    (data_dir / "註冊組").mkdir(parents=True)
    (data_dir / "註冊組" / "1-15 轉系.pdf").write_bytes(b"manual")

    report = Crawler(FakeFetcher(site), Manifest(data_dir / ".crawler_manifest.json")).crawl(SOURCE)
    assert report.added == ["註冊組/1-15 轉系（2）.pdf"]
    assert (data_dir / "註冊組" / "1-15 轉系.pdf").read_bytes() == b"manual"


def test_file_left_by_an_interrupted_run_is_reused_not_duplicated(data_dir):
    # 上次寫完檔案、還沒存 manifest 就中斷：同內容的檔案收回來用，不會變成「（2）」
    form = _pdf()
    site = {FORMS_PAGE: forms_page('<li>1-15 轉系 》<a href="/static/file/2/2.pdf">PDF</a></li>'), f"{SITE}/static/file/2/2.pdf": form}
    (data_dir / "註冊組").mkdir(parents=True)
    (data_dir / "註冊組" / "1-15 轉系.pdf").write_bytes(form)

    manifest = Manifest(data_dir / ".crawler_manifest.json")
    Crawler(FakeFetcher(site), manifest).crawl(SOURCE)
    assert sorted(p.name for p in (data_dir / "註冊組").iterdir()) == ["1-15 轉系.pdf"]
    assert "註冊組/1-15 轉系.pdf" in manifest.files


def test_legacy_check_only_calls_identical_files_duplicates(data_dir):
    form = _pdf()
    site = {FORMS_PAGE: forms_page('<li>1-15 轉系 》<a href="/static/file/2/2.pdf">PDF</a></li>'), f"{SITE}/static/file/2/2.pdf": form}
    manifest = Manifest(data_dir / ".crawler_manifest.json")
    Crawler(FakeFetcher(site), manifest).crawl(SOURCE)
    (data_dir / "舊爬蟲抓的轉系表.pdf").write_bytes(form)       # 內容一樣
    (data_dir / "1-15 轉系.doc").write_bytes(b"older version")   # 同名但內容不同
    (data_dir / "自己放的筆記.pdf").write_bytes(b"notes")

    result = {path.name: (match, identical) for path, match, identical in crawler.legacy_files(manifest)}
    assert result["舊爬蟲抓的轉系表.pdf"] == ("註冊組/1-15 轉系.pdf", True)
    assert result["1-15 轉系.doc"] == ("註冊組/1-15 轉系.pdf", False)
    assert result["自己放的筆記.pdf"] == ("", False)


def test_dry_run_writes_nothing(data_dir):
    site = {FORMS_PAGE: forms_page('<li>1-15 轉系 》<a href="/static/file/2/2.pdf">PDF</a></li>'), f"{SITE}/static/file/2/2.pdf": _pdf()}
    report = Crawler(FakeFetcher(site), Manifest(data_dir / ".crawler_manifest.json"), dry_run=True).crawl(SOURCE)
    assert report.added == ["註冊組/1-15 轉系.pdf"]
    assert not data_dir.exists() or not any(data_dir.rglob("*.pdf"))


# ------------------------------------------------------------
# 2026-10 擴充到全校各系所之後
# ------------------------------------------------------------
def test_new_site_boilerplate_is_stripped():
    cases = {
        "📄 碩士班修業辦法": "碩士班修業辦法",  # 化材系的檔案圖示
        "在新視窗開啟 文學院學士班應修科目表": "文學院學士班應修科目表",  # Orbit
        "[word 表格]資訊電機學院申請升等個人資料表": "資訊電機學院申請升等個人資料表",
        "PDF 114國立中央大學環境工程研究所博士班修業辦法.pdf 更新日期：2025-09-08": "114國立中央大學環境工程研究所博士班修業辦法",
        "SA-1 永續與綠能科技研究學院研究生修業辦法 PDF 更新日期 115.04.23": "SA-1 永續與綠能科技研究學院研究生修業辦法",
        "碩士班（115學年度入學學生修業辦法） (PDF, 102KB)": "碩士班（115學年度入學學生修業辦法）",
        "05) 光電系免修學分申請表": "光電系免修學分申請表",
        "(國立中央大學生命科學系助學工讀辦法.pdf)(pdf檔下載": "國立中央大學生命科學系助學工讀辦法",
        "資格考（口試）委員書面報告表（審定結果）ODT": "資格考（口試）委員書面報告表（審定結果）",
        "大學生五年取得學碩士學位申請及相關修業辦法： (下載)": "大學生五年取得學碩士學位申請及相關修業辦法",
        "Regulations for the Master Program.pdf (Open a new window)": "Regulations for the Master Program",
    }
    for text, expected in cases.items():
        assert clean_name(text) == expected, text
    for junk in ("(opens in a new tab)", "Image", "https://pdc.adm.ncu.edu.tw/Register"):
        assert clean_name(junk) == "", junk


def test_bare_document_type_or_year_link_text_borrows_the_context():
    # 通識中心：連結文字只有「辦法」，真正的名稱在網址
    assert choose_name([(5, "辦法"), (2, "創意學分學程選修辦法"), (1, "學分學程")]) == "創意學分學程選修辦法"
    # 物理系：「大學部課程地圖：108學年度｜109學年度」
    assert choose_name([(5, "108學年度"), (4, "物理系大學部課程地圖")]) == "物理系大學部課程地圖（108學年度）"
    assert choose_name([(5, "申請表"), (4, "王立文女士獎學金辦法")]) == "王立文女士獎學金辦法（申請表）"


def test_english_version_in_the_same_table_cell_is_marked():
    # 學務處下載中心：同一格先放中文標題跟圖示，換行再放英文標題跟英文版的圖示
    html = """<table><tr><th>種類</th><th>類別</th><th>標題</th></tr><tr><td>表格</td><td>性別平等</td><td>
      國立中央大學性侵害、性騷擾或性霸凌事件調查申請書 <a href="thumbs/downloads/1.doc"><img src="images/icon-DOC.png"/></a><br/>
      Investigation Application Form--National Central University Sexual Assault, Sexual Harassment, and Sexual Bullying
      <a href="thumbs/downloads/2.doc"><img src="images/icon-DOC.png"/></a><br/></td></tr></table>"""
    chinese, english = names_on(html, "https://osa.ncu.edu.tw/downloads.php")
    assert chinese == "國立中央大學性侵害、性騷擾或性霸凌事件調查申請書"
    assert english.endswith("（英文版）")


def test_files_listed_as_json_in_vue_component_attributes():
    # 數學系：<college-card files="[...]">、<master-card files="{&quot;deductions&quot;: [...]}">
    html = """<college-card id="0" files='[{"name":"115 數學系數學科學組應修科目表","file":"drive/files/115_1_required.pdf","year":115}]'></college-card>
    <master-card id="1" files='{"deductions":[{"name":"碩士班學分抵免辦法","file":"drive/files/99_master_deduction.pdf","year":99}]}'></master-card>"""
    soup = BeautifulSoup(html, "lxml")
    found = {url: choose_name(names) for url, names in find_document_links(soup, "https://w2.math.ncu.edu.tw/course/rule")}
    assert found == {
        "https://w2.math.ncu.edu.tw/drive/files/115_1_required.pdf": "115 數學系數學科學組應修科目表",
        "https://w2.math.ncu.edu.tw/drive/files/99_master_deduction.pdf": "碩士班學分抵免辦法（99）",  # 年度接在後面才分得出版本
    }


def test_link_kinds_of_the_new_sites():
    from backend.rag.crawler import link_kind

    assert link_kind("https://nculs.in.ncu.edu.tw/index.php/ch/readfile/index.html?p=laws&n=6239761195770.pdf") == "doc"
    assert link_kind("https://assets.ppnet.tw/ncuec/files/規章表單/36-1150610.pdf") == "doc"  # 工學院委外廠商
    assert link_kind("https://ipla.ncu.edu.tw/xhr/archive/download?file=6603c70d1d41c8155a725a2c") == "doc"
    assert link_kind("https://www.phy.ncu.edu.tw/系所規章/") == "page"


def test_orbit_pdf_viewer_page_points_to_the_real_file():
    from backend.rag.crawler import _viewer_file_url

    viewer = '<!DOCTYPE html><!-- Copyright 2012 Mozilla Foundation --><script>var u = "/uploads/archive_file_multiple/file/66/附件4.pdf";</script>'
    assert _viewer_file_url(viewer, "https://ipla.ncu.edu.tw/xhr/archive/download?file=66") == \
        "https://ipla.ncu.edu.tw/uploads/archive_file_multiple/file/66/%E9%99%84%E4%BB%B64.pdf"
    assert _viewer_file_url("<html><a href='/uploads/x.pdf'>x</a></html>", "https://ipla.ncu.edu.tw/") is None


def test_utf8_pages_with_a_few_broken_bytes_are_not_decoded_as_big5():
    from backend.rag.crawler import decode_html

    body = "<meta charset='UTF-8'>土木工程學系 徵才公告".encode() + b"\xe7\xa7" + "…其餘內容".encode() * 200
    assert "土木工程學系" in decode_html(body, "text/html")


def test_global_rules_skip_staff_documents_and_old_cohorts():
    from backend.rag.crawler import global_skip_reason

    source = Source(name="某系")
    staff = ["國立中央大學文學院院務會議設置辦法(109.4.28通過", "地球科學學系教師升等標準細則(111.11.08)", "114年度國外日支數額表報帳說明",
             "115學年度法文系特殊選才初試榜單", "隱私權政策聲明", "Image"]
    for name in staff:
        assert global_skip_reason(source, name) == "不是學生用的文件", name
    keep = [
        "中華呂祖謙學術研究協會獎學金設置辦法",  # 獎學金的設置辦法是學生要看的
        "英美語文學系碩士班研究生修業辦法(113學年第2次系務會議修正通過_114.1.7教務會議核備)",  # 「系務會議」是通過的會議
        "研究生學分抵免辦法(100學年修訂)",  # 100 學年是修法的時間，不是入學年度
        "105學年度(含)以後入學適用修業規定", "碩士學位資格檢定辦法(105學年度入學生起適用)",
        "113 CSIE thesis advisor confirmation form（for foreign students)",  # 外籍生用的英文表單
    ]
    for name in keep:
        assert global_skip_reason(source, name) == "", name
    assert global_skip_reason(source, "103學年度入學碩士班必修基本科目") == "入學年度太舊"
    assert global_skip_reason(Source(name="教務處", include=(r"校曆",)), "115 學年度校曆") == ""
    assert global_skip_reason(source, "114學年度校曆") == "不是學生用的文件"  # 各系轉貼的校曆常常是舊的


def test_only_the_latest_term_of_a_term_notice_is_kept():
    from backend.rag.crawler_sources import TERM_LATEST_ONLY

    names = ["113學年度第2學期頒發學位證書相關注意事項", "114學年度第1學期頒發學位證書相關注意事項", "114-2頒發學位證書相關注意事項",
             "碩士班修業辦法"]
    keep, drop = keep_latest(names, lambda n: n, TERM_LATEST_ONLY)
    assert keep == ["114學年度第1學期頒發學位證書相關注意事項", "114-2頒發學位證書相關注意事項", "碩士班修業辦法"]


def test_api_provider_lists_ee_documents(data_dir):
    import json

    api = "https://data.ee.ncu.edu.tw"
    rows = {"current": 1, "rowCount": 20, "total": 1, "rows": [{"approach_table_id": 25, "approach_table_title": "電機系五年取得學、碩士學位推薦鼓勵辦法"}]}
    empty = {"current": 1, "rowCount": 20, "total": 0, "rows": []}
    site = {
        f"{api}/ApproachTable?approach_table_type=1&current=1&rowCount=20&searchPhrase=": json.dumps(rows).encode(),
        f"{api}/ApproachTable?approach_table_type=2&current=1&rowCount=20&searchPhrase=": json.dumps(empty).encode(),
        f"{api}/ApproachTableAllFile?id=25": json.dumps([{"att_id": 29, "att_file_name": "五年學碩(961129系務).pdf"}]).encode(),
        f"{api}/ApproachTableFile?id=29": _pdf("ee"),
    }
    report = Crawler(FakeFetcher(site), Manifest(data_dir / ".crawler_manifest.json")).crawl(Source(name="電機系", api="ee"))
    assert report.added == ["電機系/電機系五年取得學、碩士學位推薦鼓勵辦法.pdf"]


def test_same_document_in_two_formats_without_extensions_in_the_url(data_dir):
    # Orbit 的 /xhr/archive/download?file=… 看不出格式，下載之後才知道一份 .doc、一份 .pdf
    page = "https://www.chinese.ncu.edu.tw/zh_tw/forms"
    site = {
        page: ('<html><body><ul><li><a href="/xhr/archive/download?file=1">博士班資格考試申請表</a></li>'
               '<li><a href="/xhr/archive/download?file=2">博士班資格考試申請表</a></li></ul></body></html>').encode(),
        "https://www.chinese.ncu.edu.tw/xhr/archive/download?file=1": _pdf("form"),
        "https://www.chinese.ncu.edu.tw/xhr/archive/download?file=2": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "WordDocument".encode("utf-16-le"),
    }
    report = Crawler(FakeFetcher(site), Manifest(data_dir / ".crawler_manifest.json")).crawl(Source(name="中文系", seeds=(page,)))
    assert report.added == ["中文系/博士班資格考試申請表.pdf"]
    assert any(".doc（同一份文件已經有 .pdf 版）" in s for s in report.skipped)


def test_broken_pdf_link_falls_back_to_the_other_format(data_dir):
    page = f"{SITE}/p/rules.php"
    site = {
        page: forms_page('<li>學分抵免辦法 》<a href="/static/file/9/9.pdf">PDF</a> <a href="/static/file/9/9.odt">ODT</a></li>'),
        f"{SITE}/static/file/9/9.odt": _zip({"mimetype": "application/vnd.oasis.opendocument.text"}),  # PDF 那個連結 404
    }
    report = Crawler(FakeFetcher(site), Manifest(data_dir / ".crawler_manifest.json")).crawl(Source(name="某系", seeds=(page,)))
    assert report.added == ["某系/學分抵免辦法.odt"]
    assert any("改抓 .odt 版" in s for s in report.skipped)


def test_general_affairs_link_texts_are_cleaned():
    # 總務處（Nuxt 做的網站）連結文字前面有日期跟組別、後面有無障礙說明，title 前面有「[檔案下載]」
    from backend.rag.crawler import clean_name
    assert clean_name("2018-06-08-事務組 檔案下載-國立中央大學機車通行證申請表（此為PDF檔案，請參閱檔案摘要或說明頁）") == (
        "國立中央大學機車通行證申請表"
    )
    assert clean_name("[檔案下載]自行車識別證正確黏貼位置.pdf") == "自行車識別證正確黏貼位置"
    assert clean_name("• 學生郵件包裹領取須知") == "學生郵件包裹領取須知"
    assert clean_name("2026-10-01 公告") == "2026-10-01 公告"  # 只拿掉「日期-單位」這種前綴


def test_render_sources_are_read_with_the_browser(monkeypatch):
    # Source.render 的網頁用 Renderer（瀏覽器）讀，其他照舊用 requests
    from backend.rag import crawler as c
    from backend.rag.crawler_sources import Source

    calls = []

    class FakeRenderer:
        def get_html(self, url):
            calls.append(("render", url))
            return '<html><body><a href="/files/a.pdf">機車通行證申請表</a></body></html>', url

    class FakeFetcher:
        def get_html(self, url):
            calls.append(("requests", url))
            return "<html><body></body></html>", url

    crawler = c.Crawler.__new__(c.Crawler)
    crawler.fetcher, crawler.renderer = FakeFetcher(), FakeRenderer()
    report = c.Report("總務處")
    links, _ = crawler.collect(Source(name="總務處", render=True, seeds=("https://www.oga.ncu.edu.tw/x",)), report)
    assert calls == [("render", "https://www.oga.ncu.edu.tw/x")]
    assert "https://www.oga.ncu.edu.tw/files/a.pdf" in {info.url for info in links.values()}
    crawler.collect(Source(name="資工系", seeds=("https://www.csie.ncu.edu.tw/y",)), c.Report("資工系"))
    assert calls[-1] == ("requests", "https://www.csie.ncu.edu.tw/y")
