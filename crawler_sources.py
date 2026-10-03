"""爬蟲要抓哪些網站（給 crawler.py 用）。每個 Source 對應 data/ 底下的一個資料夾。

新增來源的步驟：
1. 先用瀏覽器看過那個網站：文件是直接列在頁面上，還是要再點進子頁面？有沒有「歷年版本」？
2. 加一筆 Source，跑 `python crawler.py --source 名稱 --dry-run` 看抓到的檔名對不對、有沒有
   抓到不需要的東西（教師、行政人員用的表單可以用 exclude 排除）。
3. 資訊直接寫在網頁上（不是附檔）的頁面放進 pages，會存成 Markdown。
4. 跑完正式的 `python crawler.py --source 名稱`，再跑 `python rag_eval/run_eval.py` 確認法規
   問答沒有變差（文件變多，挑文件那一步要看的目錄也會變長）。

各網站的狀況（2026-10 實際看過）：
- 教務處、資工系是同一套 RPage 架站系統：頁面網址是 /p/412-xxxx-yyyy.php，附檔在
  /static/file/...，教務處還有 /s/reg-form1-04 這種會轉址到檔案的短網址。
- 語言中心、學務處生活輔導組、住宿服務組各自是不同的系統，命名方式都不一樣，
  檔名的處理都在 crawler.py 的 link_names()。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Follow:
    """從種子頁面往下再跟進一層的連結規則（只跟一層，不會整個網站亂爬）。"""
    url: str = ""          # 連結網址要符合的 regex，空字串表示不限
    text: str = ""         # 連結文字要符合的 regex，空字串表示不限
    latest: bool = False   # True：符合的連結裡只跟 text 第一個括號群組數字最大的（例如最新學年度）


@dataclass(frozen=True)
class Page:
    """資訊寫在網頁本身（不是附檔）的頁面，主要內容會存成 Markdown 文件。"""
    url: str
    name: str              # 存檔的檔名，也是給 RAG 看的標題
    selector: str = ""     # 主要內容的 CSS selector，留空自動判斷
    images: bool = False   # 內容裡的大張圖片（海報）另存成 PDF，讓 RAG 用圖片轉錄讀內容


@dataclass(frozen=True)
class Source:
    name: str                          # data/ 底下的資料夾名稱
    seeds: tuple[str, ...] = ()        # 從這些頁面找文件連結
    pages: tuple[Page, ...] = ()       # 要把網頁本身存下來的頁面（也會從裡面找文件連結）
    follow: tuple[Follow, ...] = ()
    exclude: tuple[str, ...] = ()      # 文件名稱符合這些 regex 的不抓（例如教師、行政人員用的表單）
    latest_only: tuple[str, ...] = ()  # 名稱符合的文件只留年度最新的一份（regex 第一個括號群組是年度）


PDC = "https://pdc.adm.ncu.edu.tw/p/412-1019-{}.php?Lang=zh-tw"
CSIE = "https://www.csie.ncu.edu.tw/p/412-1013-{}.php?Lang=zh-tw"
LC = "https://www.lc.ncu.edu.tw/zh-TW/article/{}"
MILITARY = "https://military.ncu.edu.tw/{}"
SHSD = "https://shsd.ncu.edu.tw/{}"

# 順序有意義：同一個檔案出現在好幾個來源時，存在排前面的來源的資料夾裡
# （例如教務章則彙編裡的辦法，課務組的法規頁也會再列一次）。
SOURCES: tuple[Source, ...] = (
    Source(
        name="教務處",
        seeds=(
            PDC.format(2070),  # 教務章則彙編：每個學年度一頁，只跟最新的那一頁
            PDC.format(1725),  # 校曆
        ),
        follow=(Follow(text=r"(\d{3})\s*教務章則彙編", latest=True),),
        latest_only=(r"(\d{3})\s*學年度校曆", r"(\d{4})-\d{4}\s*NCU Academic Calendar"),
    ),
    Source(
        name="教務處註冊組",
        seeds=(
            PDC.format(1706),  # 表格下載（左側選單還有學雜費收費標準）
            PDC.format(1709),  # 其他法規
            PDC.format(1933),  # 學生證專區
            PDC.format(1941),  # 申請成績單暨學位證明書
            PDC.format(1942),  # 離校生專區
            PDC.format(1934),  # 新生專區
            PDC.format(1938),  # 數位學位證書
            PDC.format(1939),  # 就學期間服役彈性修業
            PDC.format(1940),  # 學位考試系統使用說明
        ),
        exclude=(
            r"行政人員", r"系所經辦", r"二代電子表單", r"教師提更改學生成績", r"統計表",
            r"論文指導費|口試費|交通費支給", r"學雜費收入收支",
        ),
    ),
    Source(
        name="教務處課務組",
        seeds=(PDC.format(1765), PDC.format(1766)),  # 相關法規、表格下載（左側選單有選課常見問題）
        exclude=(
            # 表格下載頁大部分是教師、系所開課用的表單
            r"配當表", r"新開課程", r"代課鐘點|聘代課", r"兼課申請", r"遠距教學(計畫書|課程開設)", r"學分抵免學生名冊",
            r"教師台灣聯大", r"經費分配", r"調課補課", r"全英語\(EMI\)授課一覽", r"系統使用權限", r"教學諮詢",
            r"推薦表", r"開課申請", r"第17、18週授課",
            # 舊爬蟲就排除的教師用表單
            r"共時授課教學計畫書", r"共授課程期末學生學習心得回饋", r"領域專長模組課程計畫書",
        ),
    ),
    Source(
        name="資工系",
        seeds=(CSIE.format(1096),),  # 檔案下載（2026 改版後舊的 /downloads 已經 404）
        pages=(
            Page(CSIE.format(1154), "資工系學士班修業資訊與課程地圖"),
            Page(CSIE.format(1155), "資工系碩士班修業資訊"),
            Page(CSIE.format(1157), "資工系博士班修業資訊"),
        ),
    ),
    Source(
        name="語言中心",
        seeds=("https://www.lc.ncu.edu.tw/zh-TW/category/common-problem",),
        pages=(
            # 各學院英文門檻的分數表只有海報圖片，所以 images=True
            Page(LC.format("2022-08-31%2016:42:00"), "大學部英外文畢業門檻", images=True),
            Page(LC.format("2022-08-31%2017:01:08"), "大一英文分級、選課與免修"),
            Page(LC.format("2022-08-31%2017:19:16"), "進修英文修課規定"),
        ),
    ),
    Source(
        name="學務處生活輔導組",
        seeds=(MILITARY.format("regulation.php"), MILITARY.format("forms.php"), MILITARY.format("leaving.php")),
        pages=(
            Page(MILITARY.format("scholarship.php"), "校內獎學金一覽"),
            Page(MILITARY.format("Q&A.php"), "生活輔導組常見問題（獎助學金、請假、兵役、就學貸款）"),
            Page(MILITARY.format("tuition_and_fee_exemption.php"), "學雜費減免申請說明"),
            Page(MILITARY.format("financial_help.php"), "不利處境學生助學金申請說明"),
            Page(MILITARY.format("financial_help-2.php"), "生活助學金申請說明"),
            Page(MILITARY.format("military_service.php"), "學生兵役（緩徵、儘召、出境）說明"),
            Page(MILITARY.format("derate.php"), "軍訓課程折抵役期說明"),
        ),
        exclude=(r"行政人員", r"師長版", r"操作手冊", r"英文版", r"^[^\u3400-\u9fff]+$"),
    ),
    Source(
        name="學務處住宿服務組",
        seeds=(SHSD.format("Student/Rules"), SHSD.format("Student/FormItems")),
        pages=(
            Page(SHSD.format("Home/QA"), "宿舍常見問題"),
            Page(SHSD.format("Application/Refund"), "退宿退費申請說明"),
            Page(SHSD.format("Application/Certificate"), "住宿證明申請說明"),
        ),
        # 表單下載頁還有營隊借宿舍、教職員住會館用的表單，跟學生無關，英文版跟中文版內容一樣
        exclude=(r"監視錄影", r"投影設備", r"系統畫面", r"寒暑期營隊", r"中大會館", r"教職員", r"宿舍導師", r"英文版"),
    ),
)
