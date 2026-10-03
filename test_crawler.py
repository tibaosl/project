"""crawler.py 的測試，不連網路（HTML 片段都是從實際網站複製下來的）：
    python -m pytest test_crawler.py
"""

import io
import zipfile

import fitz
import pytest
from bs4 import BeautifulSoup

import crawler
from crawler import (
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
from crawler_sources import Follow, Page, Source


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
