"""爬蟲要抓哪些網站（給 crawler.py 用）。每個 Source 對應 data/ 底下的一個資料夾。

新增來源的步驟：
1. 先用瀏覽器看過那個網站：文件是直接列在頁面上，還是要再點進子頁面？有沒有「歷年版本」？
2. 加一筆 Source，在專案根目錄跑 `python -m backend.rag.crawler --source 名稱 --dry-run` 看抓到的
   檔名對不對、有沒有抓到不需要的東西（教師、行政人員用的表單可以用 exclude 排除）。
3. 資訊直接寫在網頁上（不是附檔）的頁面放進 pages，會存成 Markdown。
4. 跑完正式的 `python -m backend.rag.crawler --source 名稱`，再跑 `python rag_eval/run_eval.py` 確認法規
   問答沒有變差（文件變多，挑文件那一步要看的目錄也會變長）。

各網站的狀況（2026-10 實際看過）：
- 教務處、資工系是同一套 RPage 架站系統：頁面網址是 /p/412-xxxx-yyyy.php，附檔在
  /static/file/...，教務處還有 /s/reg-form1-04 這種會轉址到檔案的短網址。
- 語言中心、學務處生活輔導組、住宿服務組各自是不同的系統，命名方式都不一樣，
  檔名的處理都在 crawler.py 的 link_names()。
- 擴充到全校之後（從 www.ncu.edu.tw 的「教學」「行政」頁列出約 80 個單位網站，逐一看過）：
  大多數是 RPage（/p/412-、/p/404-、/p/405-、/p/426- 開頭的頁面都可能放文件）、WordPress、
  Orbit（網址有 /zh_tw/，附檔是 /xhr/archive/download?file=…，有時候回傳 PDF 線上檢視器），
  其餘是各單位自己寫的。特殊的：電機系的表格辦法要呼叫 API（Source.api）、數學系的檔案清單
  是 Vue 元件屬性裡的 JSON、工學院的附檔放在 assets.ppnet.tw、網學所首頁是 meta refresh 轉址。
- 抓不到的：資管系、經濟系、產經所、IMBA、天文所的網站從校外連不上；總務處（停車證、出納）
  還沒看過網站，沒有收；統計所、工學院學士班、資電學院學士班的辦法放在 Google 雲端硬碟，
  Google 的 robots.txt 不允許程式下載。需要的話手動下載放進 data/（爬蟲不會動手動放的檔案）。
- 教師升等、委員會設置、招生、報帳這類不是學生會問的文件，由 GLOBAL_EXCLUDE 統一排除；
  各系所依入學年度分的修業規定、應修科目表，太舊（COHORT_YEARS_KEPT）的不抓。
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
    include: tuple[str, ...] = ()      # 名稱符合的不套用 GLOBAL_EXCLUDE 跟年度太舊的規則
    api: str = ""                      # 文件清單要呼叫網站 API 才拿得到時，crawler.API_PROVIDERS 裡的名稱


# 所有來源都套用的排除規則（比對挑出來的文件名稱）。各單位網站的「法規」「下載」頁常常把教師、
# 行政、會議用的文件跟學生的放在一起，這些不是學生會問的，收進來只會讓挑文件更難。
# 某個來源真的需要其中一類，用那個 Source 的 include 蓋過去。
GLOBAL_EXCLUDE: tuple[str, ...] = (
    # 教師、研究人員的聘任、升等、評鑑、獎勵
    r"教師(評審|評鑑|升等|聘任|新聘|續聘|延長服務|休假研究|兼課|授課鐘點|著作|資格審查|申訴|成長社群|開授|專區)",
    r"升等|著作外審|教評會|新聘|聘任|延聘|續聘|徵聘|誠徵|應徵|專任助理",
    r"教學(傑出|優良)|優良(教師|教研|.{0,4}導師)|教研人員|研究獎勵|研究傑出|學術(研究)?(論文)?(貢獻)?獎勵|彈性薪資|玉山|拔尖|教師擔任導師",
    # 委員會、單位本身的設置辦法、會議、主管遴選（學程、獎學金的「設置辦法」是學生要看的，不排除）
    r"(委員會|委會|會議|中心|辦公室|研究室|學系|學院|研究所|學士班|學位學程|[系所院室])設置(辦法|要點|規則|準則|章程)",
    r"組織(章程|規程|規則)|(院|系|所|班|室|中心|學程)務會議(設置|組織|規則|議事|紀錄)|主任(新任|續任|遴選|推選|任免|去職|產生)|"
    r"(院|所)長(新任|續任|遴選|任免|去職)",
    r"會議紀錄|提案(用紙|單|範例|格式)|工作報告|績效(評估|報告)|自我評鑑|評鑑(結果|實施計畫|手冊)",
    # 經費、採購、報帳
    r"經費(使用|動支|編列|分配|核撥|結報)|核銷|報帳|差旅|出差|採購|印領清冊|領據|薪資|代墊|預算|決算|財務規劃|校務基金|收入收支|酬勞|支給標準",
    # 招生（給考生看的）
    r"招生|考生須知|甄試|面試|複試|榜單|錄取名單|放棄(入學|錄取)|入學考試|考古題|推廣教育",
    # 系友、捐款、刊物、統計、名單、每學期的課表
    r"(系友|所友|校友)(?!.*(獎學金|助學金))|捐款|募款|捐贈(?!.*(獎學金|助學金))",
    r"系刊|會刊|所刊|學刊|專刊|電子報|通訊第|校訊|年報|摺頁|新聞稿|記者會|期刊目錄|徵稿",
    r"統計表|人數統計|統計資料|(得獎|獲獎|錄取|入選|委員)名單",
    r"(?<!雙聯)課表|課程時間表|開課(紀錄|資訊|一覽)|授課(一覽|時間|教師)|教學大綱|週會規劃",
    r"隱私權|著作權聲明|網站安全政策|個人資料保護政策",
    # 空白的簽到表、滿意度問卷、訪視紀錄：沒有可以回答問題的內容
    r"簽到(表|單|退)|滿意度調查|問卷|訪視(記錄|紀錄)|工作(日誌|記錄簿)|週誌|輔導(記錄|紀錄)表|指導紀錄表",
    # 各系所轉貼的校曆常常是舊的，校曆只從教務處抓
    r"校曆|行事曆",
    # 沒有中文、也不像文件名稱的字（「Image」「Lab info」）。英文的表單、辦法（外籍生用的指導教授確認表）要留著
    r"^(?!.*(?i:form|regulation|guideline|rule|requirement|procedure|manual|handbook|calendar|application|policy|instruction))"
    r"[^\u3400-\u9fff]+$",
)

# 某一學期的公告（「114學年度第2學期頒發學位證書注意事項」「113-2學位考試須知」）只留最新一學期的。
# 兩個括號群組是學年度跟學期。
TERM_LATEST_ONLY: tuple[str, ...] = (
    r"(\d{3})\s*學年度?\s*第?\s*([12一二上下])\s*學期",
    r"(?<!\d)(\d{3})\s*-\s*([12])(?![\d.])",
)

# 修業規定、課程地圖這類依入學年度分的文件，比這麼多年前入學的學生應該都畢業了（學士班延畢、
# 博士班最多讀七年），名稱裡的入學學年度更早就不抓
COHORT_YEARS_KEPT = 8


PDC = "https://pdc.adm.ncu.edu.tw/p/412-1019-{}.php?Lang=zh-tw"
CSIE = "https://www.csie.ncu.edu.tw/p/412-1013-{}.php?Lang=zh-tw"
LC = "https://www.lc.ncu.edu.tw/zh-TW/article/{}"
MILITARY = "https://military.ncu.edu.tw/{}"
SHSD = "https://shsd.ncu.edu.tw/{}"
OIA = "https://oia.ncu.edu.tw/p/412-1026-{}.php?Lang=zh-tw"
LIB = "https://www.lib.ncu.edu.tw/{}"
CC = "https://www.cc.ncu.edu.tw/p/412-1033-{}.php"
RD = "https://ncu.edu.tw/rd/tw/page/index.php?{}"
CTE = "https://cte.ncu.edu.tw/zh-TW/{}"
MANDARIN = "https://mandarin.lc.ncu.edu.tw/{}/"

# 順序有意義：同一個檔案出現在好幾個來源時，存在排前面的來源的資料夾裡
# （例如教務章則彙編裡的辦法，課務組的法規頁也會再列一次）。
SOURCES: tuple[Source, ...] = (
    Source(
        name="教務處",
        seeds=(
            PDC.format(2070),  # 教務章則彙編：每個學年度一頁，只跟最新的那一頁
            PDC.format(1725),  # 校曆
            PDC.format(1981),  # 「聯絡我們」頁上放了學生使用 ChatGPT 基本原則、學生四大基本素養
        ),
        follow=(Follow(text=r"(\d{3})\s*教務章則彙編", latest=True),),
        latest_only=(r"(\d{3})\s*學年度校曆", r"(\d{4})-\d{4}\s*NCU Academic Calendar"),
        include=(r"校曆|Academic Calendar",),
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
        # 相關法規、表格下載（左側選單有選課常見問題）、職掌頁的 Hybrid Class 機制、業務項目頁的校際選課流程
        seeds=(PDC.format(1765), PDC.format(1766), PDC.format(1763), PDC.format(1764)),
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
    # ---------------- 2026-10 擴充：全校性的單位 ----------------
    Source(
        # 學務處下載中心把各組（生輔、住宿、課外、衛保、諮商、職涯、服務學習）的法規表單放在一起，
        # 一頁十筆，s 是從第幾筆開始，超過最後一頁是空的（多列幾頁，之後文件變多也抓得到）
        name="學生事務處",
        seeds=("https://osa.ncu.edu.tw/downloads.php",) + tuple(
            f"https://osa.ncu.edu.tw/downloads.php?&s={start}&np=13" for start in range(10, 200, 10)
        ),
        exclude=(r"英文版", r"^[^\u3400-\u9fff]+$", r"導生活動費", r"職涯導師", r"合作申請表",
                 r"導師(辦法|獎勵|推薦)|優良.*(導師|單位)推薦|輔導老師管理|管理者帳號|教師輔導學生|教師擔任導師"),
    ),
    Source(
        name="教務處教學發展中心",
        seeds=(PDC.format(1793), PDC.format(1944)),  # 相關法規（學習預警、教學助理、斐陶斐）、智慧財產權
        exclude=(r"深耕計畫", r"創意創業學院", r"AI輔助教學", r"業界專家", r"教學諮詢", r"-老師$", r"教師授課著作權"),
    ),
    Source(
        name="通識教育中心",
        seeds=tuple(f"https://cge.ncu.edu.tw/{page}/" for page in ("課程相關規定", "學分學程", "相關法規", "表格下載")),
        exclude=(r"開課申請", r"教師", r"教學助理", r"助教"),
    ),
    Source(
        name="體育室",
        seeds=tuple(f"https://pe.ncu.edu.tw/{page}.html" for page in (
            "category/35", "category/47", "category/34", "category/32", "category/22", "Index/category/cid/22/m/Home/p/2",
            "about/20", "about/28",
        )),
        pages=(
            Page("https://pe.ncu.edu.tw/about/65.html", "體育室常見問題"),
            Page("https://pe.ncu.edu.tw/about/29.html", "運動場館開放時間"),
            Page("https://pe.ncu.edu.tw/about/64.html", "運動中心售票方式"),
            Page("https://pe.ncu.edu.tw/about/class_id/70.html", "運動中心注意事項"),
            Page("https://pe.ncu.edu.tw/about/class_id/73.html", "重訓室注意事項"),
            Page("https://pe.ncu.edu.tw/about/class_id/72.html", "桌球室注意事項"),
            Page("https://pe.ncu.edu.tw/about/class_id/71.html", "SPA 注意事項"),
            Page("https://pe.ncu.edu.tw/about/69.html", "運動場館身心障礙者設施使用規範"),
        ),
        exclude=(r"教職員", r"公文範本", r"問卷", r"\(校外\)|（校外）", r"Off-campus"),
    ),
    Source(
        name="國際事務處",
        seeds=tuple(OIA.format(page) for page in (1853, 1857, 1866, 1872, 1873, 1883, 1884, 1885, 1889, 1893)),
        pages=(
            Page(OIA.format(1866), "出國交換、交流申請注意事項"),
            Page(OIA.format(1872), "出國交換生計畫"),
            Page(OIA.format(1874), "海外寒暑假課程與實習"),
            Page(OIA.format(1857), "雙聯學制學生申請"),
            Page(OIA.format(1858), "外籍交換生申請"),
            Page(OIA.format(1863), "外籍生錄取後辦理事項"),
            Page(OIA.format(1864), "外籍生註冊報到"),
            Page(OIA.format(1884), "僑生保險"),
            Page(OIA.format(1885), "外籍生居留證申請與延期、出入境"),
            Page(OIA.format(1887), "外籍生工作證"),
            Page(OIA.format(1891), "陸生入出境許可證及親屬探親申請"),
            Page(OIA.format(1892), "外籍生在校保險及全民健保須知"),
            Page(OIA.format(1893), "國際學生獎學金申請"),
            Page(OIA.format(1894), "國際學生在學期間注意事項"),
        ),
        exclude=(r"招生|放棄錄取|報名資格|華裔身分", r"姐妹校列表|Partner Universities", r"歷年數據"),
    ),
    Source(
        name="圖書館",
        seeds=(LIB.format("about/rulen.php"), LIB.format("others/faq.php"), LIB.format("service/formdownload.php")),
        pages=(
            Page(LIB.format("others/faq.php"), "圖書館常見問題"),
            Page(LIB.format("open/hours_se.php"), "圖書館開放時間"),
            Page(LIB.format("reader/ReaderService.php"), "圖書館讀者服務（借閱、預約、續借）"),
            Page(LIB.format("service/interlib_loan.php"), "圖書館館際合作"),
        ),
        exclude=(
            r"公務|核銷|驗收|列管|非消耗|單位自購", r"外籍人士|Foreign", r"英文版",
            r"委員會議|裝訂費|受贈|淘汰|陳列保存|支票|簽約|^application_form|推薦書單",
        ),
    ),
    Source(
        name="電子計算機中心",
        seeds=(CC.format(147), CC.format(148), CC.format(150)),
        pages=(
            Page(CC.format(114), "計算機中心帳號說明（Portal、Email）"),
            Page(CC.format(119), "Google Workspace 帳號"),
            Page(CC.format(126), "學生宿舍網路"),
            Page(CC.format(103), "計算機中心終端機室使用"),
            Page(CC.format(156), "校園電腦網路使用遵守規範"),
        ),
        exclude=(r"公務", r"Domain Name|DNS", r"虛擬主機|資源異動|機房", r"英文版"),
    ),
    Source(
        name="研究發展處",
        seeds=(RD.format("num=168&root=14"), RD.format("num=83&root=7")),  # 國科會博士生獎學金、研究獎助生
    ),
    Source(
        name="師資培育中心",
        seeds=tuple(CTE.format(page) for page in ("article/rule", "article/formdownload", "article/education1",
                                                  "category-chart/specialsubject")),
        pages=(
            Page(CTE.format("article/education"), "教育學程修課規定"),
            Page(CTE.format("article/lessonmap"), "教育學程課程地圖"),
            Page(CTE.format("article/selectionMethod"), "教育學程新生甄選"),
            Page(CTE.format("article/intern1"), "教育學程實地學習"),
        ),
    ),
    Source(
        name="語言中心華語組",
        seeds=(MANDARIN.format("ChineseLanguageLearning/115年外國學生通過華語文能力測驗獎勵金"),),
        pages=(
            Page(MANDARIN.format("ChineseLanguageCenter/修課要求"), "國際學位生華語修課要求"),
            Page(MANDARIN.format("ChineseLanguageCenter/國際學位生課程"), "國際學位生華語學分課程"),
            Page(MANDARIN.format("ChineseLanguageCenter/常見問題"), "華語課程常見問題"),
        ),
    ),
    Source(
        name="松濤全人學院",  # 原本的總教學中心，負責共同必修、核心通識（規章辦法頁都是教師的，不抓）
        seeds=("https://tc.ncu.edu.tw/?page_id=814",),
        pages=(Page("https://tc.ncu.edu.tw/?page_id=814", "共同必修與核心通識課程資訊"),),
    ),
    # ---------------- 2026-10 擴充：文學院 ----------------
    Source(
        name="文學院",
        seeds=tuple(f"https://liberal.ncu.edu.tw/zh_tw/Affairs/{page}" for page in (
            "rule/slaw", "forms/Student01", "rule/olaw", "forms/other",  # 學生規章、學生表單、場地設備規章與表單
        )),
        exclude=(r"跑馬燈|網路檢測|電視牆|特藏室|管理委員會",),
    ),
    Source(
        name="文學院學士班",
        seeds=tuple(f"https://ipla.ncu.edu.tw/zh_tw/{page}" for page in (
            "Link1/Page6", "Regulations", "Link1/prgrams", "Download",  # 應修科目表、規章、學程選修辦法、表單
        )),
        exclude=(r"合聘|新開課程",),
    ),
    Source(
        name="中國文學系",
        seeds=tuple(f"https://www.chinese.ncu.edu.tw/zh_tw/{page}" for page in (
            "About2/Details_Regulations/Rules_Students", "About2/Details_Regulations/1090805",
            "About2/Details_Regulations/internshipregulations",
            "About2/Freshman_Chinese3/Freshman_course", "About2/Freshman_Chinese3/Description",  # 大一國文（全校大一必修）
            "Curriculum2/Course_related/University_Course", "Curriculum2/Course_related/Institute_Course",
            "Curriculum2/Course_related/Course_credit",
            "Curriculum2/Rehabilitation/10908053", "Curriculum2/Rehabilitation/10908055",  # 應修科目表、研究所規章
            "Curriculum2/Rehabilitation/10907281", "Curriculum2/Rehabilitation/Doublemajor",  # 研究所表單、雙主修輔系
        )),
        latest_only=(r"(\d{3})\s*大一國文選課說明",),
        exclude=(r"學分抵免地圖.*png|徵稿",),
    ),
    Source(
        # WordPress。修業規定頁本身就寫了畢業學分、必選修，所以也存成文件
        name="英美語文學系",
        seeds=tuple(f"https://english.ncu.edu.tw/{page}/" for page in (
            "undergraduate/graduation-requirements", "undergraduate/scholarships",
            "graduate/graduation-requirements", "graduate/scholarships",
        )),
        pages=(
            Page("https://english.ncu.edu.tw/undergraduate/graduation-requirements/", "英美語文學系大學部修業規定"),
            Page("https://english.ncu.edu.tw/graduate/graduation-requirements/", "英美語文學系碩士班修業規定"),
        ),
        exclude=(r"說明會投影片",),
    ),
    Source(
        name="法國語文學系",
        seeds=tuple(f"https://french.ncu.edu.tw/?page_id={page}" for page in (
            308, 249, 5974, 4655, 422, 585, 599,  # 大學部、研究所的規定與表單、五年學碩、專業實習、選課須知、課程地圖
        )),
        exclude=(r"交換經驗|分享會|研討會|桌遊",),
    ),
    Source(
        name="哲學研究所",
        seeds=tuple(f"https://phi.ncu.edu.tw/{page}" for page in (
            "course/PhD/way_of_study", "course/master/way_of_study", "course/career/way_of_study",
            "course/specialty/course_table", "forms", "course/PhD/qualification_exam",
        )),
        pages=(Page("https://phi.ncu.edu.tw/course/PhD/qualification_exam", "哲學研究所博士班資格考"),),
        latest_only=(r"(\d{3})\s*學年度校曆",),
    ),
    Source(
        # 頁面開頭多了一組 </body></html>（crawler 的 get_html 會拿掉），選單是 JavaScript 載入的
        name="藝術學研究所",
        seeds=("https://art.ncu.edu.tw/regulation.asp", "https://art.ncu.edu.tw/form.asp"),
    ),
    Source(
        name="歷史研究所",
        seeds=tuple(f"https://his.ncu.edu.tw/p/{page}.php?Lang=zh-tw" for page in (
            "405-1018-6171,c835", "405-1018-6172,c835", "405-1018-6170,c835",  # 學生規章、表格、學位考試
            "405-1018-6139,c831", "404-1018-9468", "404-1018-9471",  # 大學部專區、獎學金辦法與表單
        )),
        pages=(
            Page("https://his.ncu.edu.tw/p/404-1018-6174.php", "歷史研究所常見問題"),
            Page("https://his.ncu.edu.tw/p/405-1018-6170,c835.php?Lang=zh-tw", "歷史研究所學位考試流程"),
            Page("https://his.ncu.edu.tw/p/404-1018-9468.php", "歷史研究所獎學金申請與獎勵辦法"),
        ),
        exclude=(r"通行證|學分班",),
    ),
    Source(
        name="學習與教學研究所",
        seeds=tuple(f"https://lrn.ncu.edu.tw/{page}/" for page in ("規章與辦法", "學生表格下載", "修業辦法", "課程地圖與開課紀錄")),
        pages=(Page("https://lrn.ncu.edu.tw/修業辦法/", "學習與教學研究所碩博士班修業規定"),),
        exclude=(r"教師|所長任免|管理費|儀器|交誼廳|教學精進|延聘|出國開會|會議申請",),
    ),
    Source(
        name="亞際文化研究國際碩士學位學程",
        seeds=tuple(f"https://iacs.ncu.edu.tw/{page}/" for page in (
            "修業規章", "人工加退選-校際選課單-停修課程申請", "修習學程外課程計入畢業學分申請書", "指導教授", "論文提案",
        )),
        pages=(Page("https://iacs.ncu.edu.tw/修業規章/", "亞際文化研究國際碩士學位學程修業規章"),),
    ),
    # ---------------- 2026-10 擴充：理學院 ----------------
    Source(
        name="理學院",
        seeds=tuple(f"https://www.science.ncu.edu.tw/p/412-1028-{page}.php?Lang=zh-tw" for page in (766, 807, 808, 2368)),
        exclude=(r"EMI教師|教師開授|全英語課程獎勵|助教英語能力", r"永烜|網站刊登|執政人員|研究中心|學術活動獎勵|教師代表|會議室"),
    ),
    Source(
        name="理學院學士班",
        seeds=tuple(f"https://ips.ncu.edu.tw/{page}" for page in ("規章辦法", "課程資訊")),
        pages=(
            Page("https://ips.ncu.edu.tw/課程資訊", "理學院學士班課程資訊（應修學分、領域專長）"),
            Page("https://ips.ncu.edu.tw/課程介紹", "理學院學士班課程介紹"),
        ),
    ),
    Source(
        # WordPress。研究所課程頁本身寫了碩博士班的修業規定，所以也存成文件
        name="物理學系",
        seeds=tuple(f"https://www.phy.ncu.edu.tw/{page}/" for page in ("系所規章", "大學部課程", "研究所課程", "emi課程")),
        pages=(
            Page("https://www.phy.ncu.edu.tw/大學部課程/", "物理學系大學部課程"),
            Page("https://www.phy.ncu.edu.tw/研究所課程/", "物理學系研究所課程與修業規定"),
        ),
        exclude=(r"門禁|機械工廠|核心設施|Entrance|空間管理|英語授課一覽", r"教師|EMI助教", r"派赴國外|外國籍航空|國外學者專家"),
    ),
    Source(
        # Vue 寫的網站，檔案清單是元件屬性裡的 JSON（crawler 的 _json_attribute_links）
        name="數學系",
        seeds=("https://w2.math.ncu.edu.tw/course/rule",),
        # 同一個辦法列了好幾個年度的版本（「碩博士生修課資格考辦法」92、94、96、100），只留最新的
        latest_only=(r"[（(](\d{2,3})[）)]$",),
    ),
    Source(
        # Orbit 系統，大學部、碩博士班的修課規定寫在課程頁上
        name="化學學系",
        seeds=("https://www.chem.ncu.edu.tw/zh_tw/archive",),
        pages=(
            Page("https://www.chem.ncu.edu.tw/zh_tw/course/UD1", "化學系大學部課程與修業規定"),
            Page("https://www.chem.ncu.edu.tw/zh_tw/course/course2", "化學系碩士班課程與修業規定"),
            Page("https://www.chem.ncu.edu.tw/zh_tw/course/course3", "化學系博士班課程與修業規定"),
            Page("https://www.chem.ncu.edu.tw/zh_tw/course/course4", "化學系博碩同修選修課程"),
        ),
    ),
    Source(
        name="光電科學與工程學系",
        seeds=tuple(f"https://www.dop.ncu.edu.tw/{page}" for page in (
            "about.php?key=Nk5DVQ--", "class.php?key=M05DVQ--", "class.php?key=Nk5DVQ--", "student.php",
            "information_ii.php?key=NE5DVQ--",  # 規章辦法、大學部修業、研究生修業、新生、獎學金及出國
        )),
        exclude=(r"國際會議廳|鑑識系統|安全須知|網路及軟體|報帳",),
    ),
    Source(
        name="統計研究所",
        seeds=(
            "https://www.stat.ncu.edu.tw/%E9%97%9C%E6%96%BC%E6%9C%AC%E6%89%80/%E7%AB%A0%E7%A8%8B%E8%BE%A6%E6%B3%95/",
            "https://www.stat.ncu.edu.tw/%E6%9C%80%E6%96%B0%E6%B6%88%E6%81%AF/%E5%AD%B8%E7%94%9F%E4%BA%8B%E5%8B%99%E5%85%AC%E5%91%8A/",
        ),
    ),
    Source(
        name="天文研究所",
        seeds=tuple(f"https://www.astro.ncu.edu.tw/{page}.php" for page in (
            "study_here/credit_program", "study_here/grad_process", "regulation/regulation", "regulation/form",
        )),
        exclude=(r"兼任助理|差旅|領據",),
    ),
    # ---------------- 2026-10 擴充：地球科學學院 ----------------
    Source(
        name="地球科學學院",
        seeds=tuple(f"https://escollege.ncu.edu.tw/p/{page}.php" for page in (
            "404-1008-10504", "404-1008-7627", "404-1008-10505", "412-1008-2337",  # 學生相關、TIGP 章則、其他、相關資源
        )),
        exclude=(r"會議室|門禁|院刊",),
    ),
    Source(
        # Orbit 系統：規章列表的每一筆是一個內頁，檔案在內頁裡，所以往下跟一層
        name="地球科學學院學士班",
        seeds=tuple(f"https://ipess.ncu.edu.tw/zh_tw/{page}" for page in ("dept703", "ES_College_BS_Program_Documents", "Rule")),
        follow=(Follow(url=r"/zh_tw/Rule/."),),
    ),
    Source(
        name="地球科學學系",
        seeds=tuple(f"https://www.gep.ncu.edu.tw/student?f1={a}&f2={b}" for a, b in ((4, 0), (6, 0), (2, 4), (2, 2), (8, 0))),
        exclude=(r"歷年.*開設課程|課程基資|親師座談|暑期課程計劃書", r"SWOT|發展計畫|收費標準|委員會辦法|核心能力評量|環境教育機構"),
    ),
    Source(
        # 畢業規定頁本身就是完整的修業規定（中英對照），也存成文件
        name="大氣科學學系",
        seeds=("http://www.atm.ncu.edu.tw/course_1.php", "http://www.atm.ncu.edu.tw/course_2.php"),
        pages=(
            Page("http://www.atm.ncu.edu.tw/course_1.php", "大氣科學學系畢業規定"),
            Page("http://www.atm.ncu.edu.tw/course.php", "大氣科學學系大學部課程"),
            Page("http://www.atm.ncu.edu.tw/internationalstudent.php", "大氣科學學系國際學生資訊"),
        ),
    ),
    Source(
        name="太空科學與工程學系",
        seeds=tuple(f"https://www.ss.ncu.edu.tw/p/412-1020-{page}.php?Lang=zh-tw" for page in (2109, 2107)),
        pages=(Page("https://www.ss.ncu.edu.tw/p/412-1020-2029.php?Lang=zh-tw", "太空科學與工程學系常見問題"),),
    ),
    Source(
        name="應用地質研究所",
        seeds=("https://geo.ncu.edu.tw/p/412-1005-679.php?Lang=zh-tw", "https://geo.ncu.edu.tw/p/404-1005-11224.php?Lang=zh-tw"),
        pages=(Page("https://geo.ncu.edu.tw/p/412-1005-679.php?Lang=zh-tw", "應用地質研究所課程資訊"),),
    ),
    Source(
        name="水文與海洋科學研究所",
        seeds=("https://www.ihos.ncu.edu.tw/p/412-1004-459.php",),
        pages=(
            Page("https://www.ihos.ncu.edu.tw/p/412-1004-189.php", "水文與海洋科學研究所課程架構與修業規定"),
            Page("https://www.ihos.ncu.edu.tw/p/404-1004-10475.php", "水文與海洋科學研究所課程地圖"),
            Page("https://www.ihos.ncu.edu.tw/p/412-1004-186.php", "水文與海洋科學研究所入學獎學金"),
        ),
    ),
    # ---------------- 2026-10 擴充：工學院 ----------------
    Source(
        # 檔案放在委外廠商的 assets.ppnet.tw（crawler.FILE_HOSTS）
        name="工學院",
        seeds=("https://www.ec.ncu.edu.tw/pages/52-%E8%A6%8F%E7%AB%A0%E8%A1%A8%E5%96%AE",),
        exclude=(r"研究中心|館舍|修繕|助教評鑑|研究傑出|校務會議代表|教師",),
    ),
    Source(
        name="工學院學士班",
        seeds=tuple(f"https://ipe.ec.ncu.edu.tw/p/412-1009-{page}.php?Lang=zh-tw" for page in (381, 374, 377, 2048)),
        pages=(
            Page("https://ipe.ec.ncu.edu.tw/p/412-1009-362.php?Lang=zh-tw", "工學院學士班常見問題"),
            Page("https://ipe.ec.ncu.edu.tw/p/412-1009-364.php?Lang=zh-tw", "工學院學士班課程規劃"),
            Page("https://ipe.ec.ncu.edu.tw/p/412-1009-2048.php?Lang=zh-tw", "工學院學士班專題製作與畢業專題"),
        ),
    ),
    Source(
        name="化學工程與材料工程學系",
        seeds=tuple(f"https://www.cme.ncu.edu.tw/p/412-1015-{page}.php?Lang=zh-tw" for page in (887, 890, 891, 1905)),
        pages=(
            Page("https://www.cme.ncu.edu.tw/p/412-1015-888.php?Lang=zh-tw", "化材系課程資訊"),
            Page("https://www.cme.ncu.edu.tw/p/412-1015-1905.php?Lang=zh-tw", "化材系獎學金"),
        ),
        exclude=(r"教師|助教",),
    ),
    Source(
        name="土木工程學系",
        seeds=tuple(f"https://www.cv.ncu.edu.tw/{page}/" for page in (
            "關於土木系/系所簡介/系所規章", "升學與課程/課程地圖", "升學與課程/五年雙學位",
            "學生專區/學生相關規章", "學生專區/碩士班", "學生專區/博士班",
        )),
        pages=(
            Page("https://www.cv.ncu.edu.tw/學生專區/碩士班/", "土木系碩士班學位考試與離校須知"),
            Page("https://www.cv.ncu.edu.tw/學生專區/博士班/", "土木系博士班資格考、學位考試與離校須知"),
        ),
        exclude=(r"端終機室|終端機室|入會申請",),
    ),
    Source(
        # WordPress。表格下載頁大多是教職員報帳、出差用的
        name="機械工程學系",
        seeds=tuple(f"https://www.me.ncu.edu.tw/{page}/" for page in (
            "網路資源/章程辦法", "教學與研究/課程資訊-2/大學部", "教學與研究/課程資訊-2/碩博士班", "網路資源/表格下載", "畢業檢核表",
        )),
        exclude=(
            r"保險|外交部|生活費|保密同意書|專班經費|海外差旅|國科會|研究計畫|計畫經費|大陸地區|兼任助理|臨時工",
            r"日支數額|購買單|產學合作|3D列印|筆記型電腦|單槍|暫墊|停電|共同類表單|出國報告|出國旅費|退休|非獨立所|授課辦法|平面圖|"
            r"教學規畫表|課程問卷",
        ),
    ),
    Source(
        # 修業辦法一筆一個內頁（course_more），檔案在內頁裡，所以往下跟一層
        name="環境工程研究所",
        seeds=tuple(f"https://ev.in.ncu.edu.tw/index.php/index/{page}" for page in (
            "Course/index.html?id=8a511247124492ab063e4b74df5d9ab229a9",  # 博士班
            "Course/index.html?id=e016da701841b293d5393e248ac5f0e9a4fd",  # 碩士班
            "Course/index.html?id=284c33f61d2fe2a2b73510f43743746e35a2",
            "Course/index.html?id=176ebd1211f742449a35d54493819d00c40f",  # 課程地圖
            "Student/index.html?id=f46c8fc71a9b12b780319e24a34c5be42f43",  # 獎學金
            "Student/index.html?id=8e74a3811a5cc29a8a383db480fb8b294340",  # 學生表單下載
        )),
        follow=(Follow(url=r"course_more|student_more"),),
        exclude=(r"技術員|所刊|專刊",),
    ),
    Source(
        name="材料科學與工程研究所",
        seeds=("https://in.ncu.edu.tw/mse/regular.php",),
        pages=(
            Page("https://in.ncu.edu.tw/mse/course.php", "材料科學與工程研究所課程介紹"),
            Page("https://in.ncu.edu.tw/mse/auth5.php", "材料科學與工程研究所常見問題"),
        ),
    ),
    # ---------------- 2026-10 擴充：管理學院（資管系、經濟系、產經所、IMBA 的網站從校外連不上，沒有收） ----------------
    Source(
        name="管理學院",
        seeds=tuple(f"https://www.mgt.ncu.edu.tw/zh-TW/article/{page}" for page in (
            "2019-03-27%2016:40:18", "2024-01-12%2014:39:48", "%E8%A1%A8%E6%A0%BC%E4%B8%8B%E8%BC%89",  # 學生規章、實習課程、表格
        )),
        exclude=(r"會議室|門禁|清松|許願", r"^form_\d+$|Access Control|Assess Control"),
    ),
    Source(
        name="企業管理學系",
        seeds=tuple(f"https://ba.mgt.ncu.edu.tw/{page}" for page in (
            "coursea.php?tid=2", "coursea.php?tid=2&tobj=C", "coursea.php?tid=4&tobj=A", "coursea.php?tid=4&tobj=B",
            "coursea.php?tid=4&tobj=C", "coursea.php?tid=4&tobj=D", "coursea.php?tid=5", "coursea.php?tid=6&tobj=A",
            "coursea.php?tid=6&tobj=B", "coursea.php?tid=7", "file.php",
        )),
        exclude=(r"視訊會議|校外委員|印領清冊|導生聚",),
    ),
    Source(
        name="財務金融學系",
        seeds=("https://fm.mgt.ncu.edu.tw/zh-TW/category/Download",),
        exclude=(r"座位表|教室照片",),
    ),
    Source(
        name="會計研究所",
        seeds=tuple(f"https://acc.mgt.ncu.edu.tw/zh-TW/category/{page}" for page in (
            "ca_20190129_020805", "ca_20190129_020812", "ca_20190129_020724",  # 修業辦法、相關表格、在職專班
        )),
        exclude=(r"環保產品|代墊|勞僱型態|保密同意書",),
    ),
    Source(
        name="人力資源管理研究所",
        seeds=("https://hr.mgt.ncu.edu.tw/zh-TW/category/method", "https://hr.mgt.ncu.edu.tw/zh-TW/category/Downloads"),
    ),
    Source(
        name="工業管理研究所",
        seeds=tuple(f"https://ia.mgt.ncu.edu.tw/Course?section={page}" for page in (
            "doctoral", "master", "credit-waiver", "five-year", "executive-master",
        )),
        pages=(Page("https://ia.mgt.ncu.edu.tw/Course", "工業管理研究所課程與修業規定"),),
    ),
    Source(
        name="高階主管企管碩士班（EMBA）",
        seeds=("https://emba.ncu.edu.tw/CentralUniversityEMBA-requirements.html",),
        pages=(Page("https://emba.ncu.edu.tw/CentralUniversityEMBA-Curriculum.html", "EMBA 課程規劃"),),
    ),
    # ---------------- 2026-10 擴充：資訊電機學院 ----------------
    Source(
        name="資訊電機學院",
        seeds=("https://www.ceecs.ncu.edu.tw/S_reward.aspx",),
    ),
    Source(
        name="資訊電機學院學士班",
        seeds=(
            "https://www.ipeecs.ncu.edu.tw/basic-info/download/",
            "https://www.ipeecs.ncu.edu.tw/basic-info/rule/%E3%80%8C%E8%B3%87%E9%9B%BB%E5%B0%88%E9%A1%8C%E5%AF%A6%E4%BD%9C%E3%80%8D"
            "%E8%AA%B2%E7%A8%8B%E5%AF%A6%E6%96%BD%E8%BE%A6%E6%B3%95/",
        ),
        pages=(Page("https://www.ipeecs.ncu.edu.tw/basic-info/intro/intro/", "資訊電機學院學士班簡介與修業"),),
        exclude=(r"招生宣傳|新生座談簡報",),
    ),
    Source(
        # 表格辦法是前端用 API 載入的，網頁上沒有連結（crawler.ee_documents）
        name="電機工程學系",
        api="ee",
    ),
    Source(
        name="通訊工程學系",
        seeds=tuple(f"https://www.ce.ncu.edu.tw/{page}" for page in ("course?ty=1", "criteria", "student", "download")),
        exclude=(
            r"門禁|教師授課時間|助教|課程資料繳交|成績報告單|修繕|保密同意書|訪談記錄|勞僱型態|畢業流向|課程綱要|開課規劃",
            r"_(96-99|100-102|103-107|104-106)$",  # 早就畢業的入學年度適用的修業辦法
        ),
    ),
    Source(
        # 首頁是 meta refresh 轉到 /NLT_new/，爬蟲不會跟 meta refresh，直接寫新網址
        name="網路學習科技研究所",
        seeds=tuple(f"http://www.lst.ncu.edu.tw/NLT_new/page.php?slug={slug}&lang=zh" for slug in (
            "method", "form", "student-form", "curriculum-map", "course",
        )),
        exclude=(r"簡介表|自傳表",),  # 招生用的
        pages=(
            Page("http://www.lst.ncu.edu.tw/NLT_new/page.php?slug=method&lang=zh", "網路學習科技研究所修業辦法"),
            Page("http://www.lst.ncu.edu.tw/NLT_new/page.php?slug=course&lang=zh", "網路學習科技研究所課程資訊"),
        ),
    ),
    Source(
        name="人工智慧國際碩士學位學程",
        seeds=("https://igpai.ncu.edu.tw/Constitution.aspx", "https://igpai.ncu.edu.tw/Download.aspx"),
        pages=(Page("https://igpai.ncu.edu.tw/S_reward.aspx", "人工智慧國際碩士學位學程獎學金"),),
    ),
    # ---------------- 2026-10 擴充：客家學院 ----------------
    Source(
        name="客家學院",
        seeds=("http://hakka.ncu.edu.tw/S_reward.aspx",),
    ),
    Source(
        name="客家語文暨社會科學學系",
        seeds=tuple(f"https://ncu.edu.tw/hakkadepartment/web/{page}" for page in (
            "course/course.jsp?dm_id=DM1660110710518", "course/course.jsp?dm_id=DM1660226283347",  # 學士班、碩士班
            "course/course.jsp?dm_id=DM1660226318647", "course/course.jsp?dm_id=DM1660226351638",  # 博士班、在職專班
            "download/download.jsp?dm_id=DM1664246230838", "download/download.jsp?dm_id=DM1670310448214",  # 大學部表單、修業辦法
            "download/download.jsp?dm_id=DM1670310455945", "download/download.jsp?dm_id=DM1670310466678",  # 學分抵免、其他辦法
            "download/download.jsp?dm_id=DM1671693514305", "download/download.jsp?dm_id=DM1671693507665",  # 碩博學分、學籍
            "download/download.jsp?dm_id=DM1671693580711", "download/download.jsp?dm_id=DM1671693589776",  # 資格考、計畫考試
            "download/download.jsp?dm_id=DM1671693596798", "about/about.jsp?cp_id=CP1759993086617",  # 學位考試、五年雙學位
            "student/scholarship_in.jsp?dm_id=DM2659961577666", "student/scholarship_in.jsp?dm_id=DM2659961577667",
            "student/scholarship_in.jsp?dm_id=DM2659961577668",  # 獎學金（碩士班、在職專班、博士班）
        )),
    ),
    Source(
        name="法律與政府學系",
        seeds=tuple(f"https://www.lawgov.ncu.edu.tw/p/426-1002-{page}.php?Lang=zh-tw" for page in (25, 12, 13, 18, 20)),
        pages=(Page("https://www.lawgov.ncu.edu.tw/p/426-1002-18.php?Lang=zh-tw", "法律學分學程"),),
    ),
    # ---------------- 2026-10 擴充：生醫理工學院（學院網站是 JavaScript 產生的，沒有收） ----------------
    Source(
        # 自己寫的網站，首頁網址在 /index.php/ch/Index/ 底下，其他頁不在同一層
        name="生命科學系",
        # 辦法、表格的列表頁每一筆是一個內頁（more_law、more_files），檔案在內頁裡，所以往下跟一層
        seeds=tuple(f"https://nculs.in.ncu.edu.tw/index.php/ch/{page}" for page in (
            "about/list_law.html", "about/list_law.html?p=2", "about/list_files.html?cid=21", "about/list_files.html?cid=9",
            "about/list_files.html?cid=37", "course/index.html?cid=55", "course/more.html?id=167",
        )),
        follow=(Follow(url=r"more_(law|files)\.html"),),
        exclude=(r"生物安全|實驗動物|環安|輻射|管制藥品|門禁|防護|Grant|Ministry|儀器|會議廳|繪圖機|動物房|教師|空間借用|場地借用",),
    ),
    Source(
        name="認知神經科學研究所",
        seeds=tuple(f"https://icn.ncu.edu.tw/5-detail.php?id={page}" for page in (1, 2, 3)),
        pages=(Page("https://icn.ncu.edu.tw/6-detail.php?id=2", "跨領域神經科學博士學位學程（台灣聯合大學系統）"),),
        exclude=(r"印表機|門禁|^\d{3}學年度[上下]學期$",),
    ),
    Source(
        # 每個辦法一個頁面（show.php?top=2&num=…），沒有列表頁
        name="生醫科學與工程學系",
        seeds=tuple(f"https://ncu.edu.tw/dbse/tw/pages/show.php?top={top}&num={num}" for top, num in (
            (2, 82), (2, 368), (2, 282), (2, 376), (2, 285), (2, 377), (2, 286), (2, 378), (2, 358), (2, 379), (1, 354),
        )),
    ),
    # ---------------- 2026-10 擴充：永續與綠能科技研究學院、太空及遙測研究中心 ----------------
    Source(
        name="永續與綠能科技研究學院",
        seeds=("https://sage.ncu.edu.tw/p/412-1011-2212.php?Lang=zh-tw", "https://sage.ncu.edu.tw/p/412-1011-2287.php?Lang=zh-tw"),
        pages=(
            Page("https://sage.ncu.edu.tw/p/412-1011-2212.php?Lang=zh-tw", "永續與綠能科技研究學院修業辦法及資訊"),
            Page("https://sage.ncu.edu.tw/p/412-1011-2288.php?Lang=zh-tw", "永續與綠能科技研究學院常見問題"),
        ),
        exclude=(r"推薦信|成績名次證明|在職證明|同等學歷|報名費|報考|錄取|複查成績|資格不符|學歷證件|退費申請|視訊口試",),  # 表單下載頁一半是報考用的
    ),
    Source(
        name="遙測科技碩士學位學程",
        seeds=("https://www.csrsr.ncu.edu.tw/about_degree.php",),
        pages=(Page("https://www.csrsr.ncu.edu.tw/degree/degree_intro.php", "遙測科技碩士學位學程簡介"),),
        exclude=(r"門禁|核心能力",),
    ),
    # ---------------- 2026-10 擴充：學務處各組自己的網站 ----------------
    Source(
        name="學務處諮商輔導中心",
        seeds=("https://love.adm.ncu.edu.tw/NCU_counsel/resource-60", "https://love.adm.ncu.edu.tw/NCU_counsel/resource-31"),
        pages=tuple(Page(f"https://love.adm.ncu.edu.tw/NCU_counsel/{slug}", name) for slug, name in (
            ("consultation-appointment", "諮商預約方式"), ("consultation-qa", "諮商常見問題"),
            ("psychological-resources-help-zone", "心理資源與求助專區"), ("resource-31", "資源教室管理辦法"),
        )),
        exclude=(r"導師|活動手札",),
    ),
    Source(
        name="學務處課外活動組",
        seeds=("https://club.adm.ncu.edu.tw/regulations", "https://club.adm.ncu.edu.tw/download", "https://club.adm.ncu.edu.tw/evaluation/"),
        pages=(Page("https://club.adm.ncu.edu.tw/qa", "課外活動組常見問題（社團、場地借用、活動申請）"),),
        # 系統操作指引、期初大會簡報是幾十頁的截圖，圖片轉錄很貴、對問答幫助不大
        exclude=(r"操作指引|期初大會|財產管理",),
    ),
    Source(
        # Next.js 網站，法規、下載專區的清單是前端載入的（辦法在學務處下載中心已經有），只存說明頁
        name="學務處衛生保健組",
        pages=tuple(Page(f"https://health.ncu.edu.tw/zh/page/{slug}", name) for slug, name in (
            ("student_group_insurance", "學生團體保險說明"), ("freshman_health_check", "新生健康檢查"),
            ("campus_injury_treatment", "校園傷病處理"), ("medical_equipment_loan", "醫療器材借用"),
            ("freshman_cpr", "大一 CPR 訓練"), ("service_hours", "衛生保健組服務時間"),
        )),
    ),
    Source(
        name="學務處職涯發展中心",
        seeds=tuple(f"https://careercenter.ncu.edu.tw/{page}" for page in (
            "article/%E8%A1%A8%E5%96%AE%E4%B8%8B%E8%BC%89",  # 表單下載
            "article/%E8%81%B7%E6%B6%AF%E6%B4%BB%E5%8B%95%E5%8A%A9%E5%AD%B8%E9%87%91%E6%9A%A8%E5%AF%A6%E7%BF%92%E7%8D%8E%E5%8B%B5%E9%87%91",
            "article/%E5%82%91%E5%87%BA%E9%A0%98%E5%B0%8E%E7%8D%8E%E5%AD%B8%E9%87%91",  # 傑出領導獎學金
        )),
        pages=(
            Page("https://careercenter.ncu.edu.tw/page/%E5%AD%B8%E7%94%9F%E5%AF%A6%E7%BF%92%E8%88%87%E6%B1%82%E8%81%B7", "學生實習與求職"),
            Page("https://careercenter.ncu.edu.tw/article/%E8%81%B7%E6%B6%AF%E8%AA%B2%E7%A8%8B", "職涯課程"),
            Page("https://careercenter.ncu.edu.tw/article/%E5%83%91%E7%94%9F%E7%95%A2%E6%A5%AD%E5%9C%A8%E5%8F%B0%E6%B1%82%E8%81%B7", "僑生畢業在台求職"),
            Page("https://careercenter.ncu.edu.tw/article/%E8%81%B7%E6%B6%AF%E6%B4%BB%E5%8B%95%E5%8A%A9%E5%AD%B8%E9%87%91%E6%9A%A8%E5%AF%A6%E7%BF%92%E7%8D%8E%E5%8B%B5%E9%87%91", "職涯活動助學金暨實習獎勵金"),
        ),
        exclude=(r"企業|廠商|合作備忘",),
    ),
    Source(
        name="環境保護暨安全衛生中心",
        pages=(
            Page("https://ncu.edu.tw/epsc/index.php/index/Educate.html", "實驗室安全衛生教育訓練課程"),
            Page("https://ncu.edu.tw/epsc/index.php/index/educate/index.html?id=7f3d68091061a298ec3e7b442ccb7ca13305", "職業安全衛生教育訓練"),
        ),
    ),
)
