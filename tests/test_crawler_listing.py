"""總務處那種「分類下拉選單 + 下一頁」的圖文清單（crawler_sources.Page.categories）。
瀏覽器的測試開一個本機的假清單頁，用真的無頭 Chromium 操作（CI 有裝），不連學校網站：
    python -m pytest tests/test_crawler_listing.py
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.rag import crawler
from backend.rag.crawler_sources import Page

# 13 筆：第一區 5 筆、第二區 2 筆、沒有分類 6 筆，一頁 10 筆，不分類時要按一次下一頁
ITEMS = [(f"/detail/{i}", f"店家{i}", "c1" if i < 5 else "c2" if i < 7 else "") for i in range(13)]

LIST_PAGE = """<!doctype html><html><body>
<select><option value="-1">請選擇分類</option><option value="c1"> 第一區</option><option value="c2"> 第二區</option></select>
<button type="button" id="search">搜尋</button>
<table><tbody id="list"></tbody></table>
<select aria-label="每頁顯示筆數"><option>10</option></select>
<ul class="pagination">
  <li class="page-item"><button type="button" aria-label="上一頁">上</button></li>
  <li class="page-item" id="next"><button type="button" aria-label="下一頁">下</button></li>
</ul>
<script>
const items = ITEMS_JSON;
let category = null, page = 0;
function render() {
  const rows = items.filter((item) => !category || item[2] === category);
  document.getElementById("list").innerHTML = rows.slice(page * 10, page * 10 + 10)
    .map((item) => `<tr><td><a class="news-table-list" href="${item[0]}" title="${item[1]}">2025-12-01 ${item[1]}</a></td></tr>`)
    .join("");
  document.getElementById("next").className = "page-item" + ((page + 1) * 10 >= rows.length ? " disabled" : "");
}
document.querySelector("#next button").onclick = () => { page += 1; render(); };
document.getElementById("search").onclick = () => {
  const value = document.querySelector("select").value;
  category = value === "-1" ? null : value;
  page = 0;
  render();
};
render();
</script></body></html>"""

DETAIL_PAGE = """<html><body><header>選單</header><main><div class="content">
<h1>店家1</h1><p>發布單位 : 資產組</p><p>2025-12-01</p><p>早午餐</p><p>營業時間：周一至周五07:30-13:30</p>
</div></main><footer>頁尾</footer></body></html>"""


class AllowAll:
    def allows(self, url):
        return True


@pytest.fixture
def site():
    import json

    pages = {"/list": LIST_PAGE.replace("ITEMS_JSON", json.dumps(ITEMS, ensure_ascii=False))}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = pages.get(self.path, DETAIL_PAGE).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_category_listing_reads_every_category_and_page(site, monkeypatch):
    monkeypatch.setattr(crawler, "RENDER_SETTLE_MS", 50)
    monkeypatch.setattr(crawler, "RENDER_DELAY", 0)
    renderer = crawler.Renderer(AllowAll())
    try:
        url, items = renderer.category_listing(f"{site}/list")
    finally:
        renderer.close()

    assert url == f"{site}/list"
    assert list(items) == [f"{site}{href}" for href, _, _ in ITEMS]  # 照分類順序，沒分類的放最後
    assert items[f"{site}/detail/0"] == ("店家0", "第一區")
    assert items[f"{site}/detail/6"] == ("店家6", "第二區")
    assert items[f"{site}/detail/12"] == ("店家12", "")  # 在第二頁，要按下一頁才讀得到


def test_detail_text_drops_publisher_date_and_repeated_title():
    text = crawler.detail_text(DETAIL_PAGE, "https://www.oga.ncu.edu.tw/x", "店家1")
    assert text == "早午餐\n營業時間：周一至周五07:30-13:30"


def test_listing_markdown_groups_by_category_with_uncategorized_last():
    page = Page("https://www.oga.ncu.edu.tw/list", "校園餐廳介紹", categories=True)
    markdown = crawler.listing_markdown(page, page.url, [
        ("", "影印店", "影印"), ("松果餐廳", "自助餐", "營業時間：11:00-13:30"), ("第九餐廳", "餐廳整修中", ""),
    ])
    assert markdown == (
        "# 校園餐廳介紹\n\n來源網頁：https://www.oga.ncu.edu.tw/list\n\n"
        "## 松果餐廳\n\n### 自助餐\n\n營業時間：11:00-13:30\n\n"
        "## 第九餐廳\n\n### 餐廳整修中\n\n"
        "## 其他\n\n### 影印店\n\n影印\n"
    )


def test_listing_pages_are_written_from_the_detail_pages(tmp_path, monkeypatch):
    # _write_snapshot 遇到 Page.categories 改用清單跟詳細頁組內容，一筆都沒讀到就不更新原本的檔案
    class FakeRenderer:
        def __init__(self, items):
            self.items = items

        def category_listing(self, url):
            return url, self.items

        def get_html(self, url):
            return DETAIL_PAGE.replace("店家1", "蛋餅酥酥"), url

    page = Page("https://www.oga.ncu.edu.tw/list", "校園餐廳介紹", categories=True)
    c = crawler.Crawler.__new__(crawler.Crawler)
    written = {}
    monkeypatch.setattr(c, "_write_generated", lambda source, path, url, title, kind, data, *rest, **kw: written.update({path: data}),
                        raising=False)
    c.renderer = FakeRenderer({"https://www.oga.ncu.edu.tw/d/1": ("蛋餅酥酥", "14舍B1商場")})
    source = type("S", (), {"name": "總務處"})()
    c._write_snapshot(source, page, page.url, "<html></html>", crawler.Report("總務處"))
    assert "## 14舍B1商場\n\n### 蛋餅酥酥\n\n早午餐\n營業時間" in written["總務處/校園餐廳介紹.md"].decode("utf-8")

    c.renderer = FakeRenderer({})
    with pytest.raises(crawler.FetchError):
        c._write_snapshot(source, page, page.url, "<html></html>", crawler.Report("總務處"))
