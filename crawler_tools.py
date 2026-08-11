import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import urllib3
import re
import time
import io
import pypdf

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

GENERIC_NAMES = {"pdf", "doc", "docx", "下載", "點我下載", "檔案", "附件", "download", "file", "開啟檔案"}
# 不爬該關鍵字
PARTIAL_BLACKLIST = ["測試用檔案", "無效文件"]
# 不爬特定檔案
EXACT_BLACKLIST = ["LaTex套件下載", "圖書館表單下載","共授課程期末學生學習心得回饋", "共時授課教學計畫書", "檔案下載", "領域專長模組課程計畫書"]

def extract_pdf_title(pdf_bytes: bytes) -> str:
    """
    從 PDF 二進位內容中提取第一頁的第一行非空白文字作為標題
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        if len(reader.pages) > 0:
            text = reader.pages[0].extract_text()
            if text:
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                if lines:
                    candidate = lines[0][:50]
                    clean_title = re.sub(r'[\\/*?:"<>|\n\r\t]', "", candidate)
                    if len(clean_title) > 2:
                        return clean_title
    except Exception as e:
        print(f"  [PDF解析 Warning] 無法從 PDF 內文提取標題：{e}")
    return None

def download_ncu_pdfs(target_url: str, save_dir: str = "data"):
    """
    自動爬取指定網頁中的所有 PDF 檔案，並儲存到本地資料夾
    """
    print(f"\n[Crawler] 啟動自動化爬蟲，準備掃描網頁：{target_url}")
    os.makedirs(save_dir, exist_ok=True)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(target_url, headers=headers, verify=False)
        response.encoding = 'utf-8'
        response.raise_for_status()
    except Exception as e:
        print(f"[Crawler] 無法連線至網頁，錯誤訊息：{e}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.find_all("a")
    print(f"[Crawler] 網頁解析成功，共找到 {len(links)} 個超連結，開始篩選 PDF...\n")

    # debug
    """ print("\n[Debug Mode] 爬蟲實際看到的連結：")
    for link in links:
        href = link.get("href")
        if href and ("javascript" not in href) and ("#" != href):
            print(f"發現連結: {href}")
    print("=====================================\n") """
    
    download_count = 0
    for link in links:
        href = link.get("href")
        if not href or "javascript" in href or href == "#" or ".odt" in href.lower():
            continue
        
        if "download" in href.lower() or "file" in href.lower() or ".pdf" in href.lower() or ".doc" in href.lower():
            full_pdf_url = urljoin(target_url, href)
            raw_text = link.text.strip()
            file_name = raw_text.split('\n')[0].strip()
            file_name = re.sub(r'[\\/*?:"<>|\n\r\t]', "", file_name)

            clean_check_name = re.sub(r'^\d+[\.、\s]*', '', file_name).strip().lower()
            if not file_name or clean_check_name in GENERIC_NAMES or len(file_name) <= 3:
                parent_tr = link.find_parent("tr")
                if parent_tr:
                    tr_text = parent_tr.text.strip().split('\n')[0].strip()
                    tr_text = re.sub(r'[\\/*?:"<>|\n\r\t]', "", tr_text)
                    if len(tr_text) > 3:
                        file_name = tr_text

            is_partial_match = any(bad_word in file_name for bad_word in PARTIAL_BLACKLIST)
            is_exact_match = file_name in EXACT_BLACKLIST
            if is_partial_match or is_exact_match:
                print(f"[黑名單攔截] 跳過：{file_name}")
                continue

            if ".docx" in href.lower() or ".docx" in file_name.lower():
                ext = ".docx"
            elif ".doc" in href.lower() or ".doc" in file_name.lower():
                ext = ".doc"
            else:
                ext = ".pdf"

            file_name = re.sub(r'\.pdf$|\.docx?$|\.DOCX?$', '', file_name, flags=re.IGNORECASE)

            if file_name and file_name.lower() not in GENERIC_NAMES:
                initial_check_path = os.path.join(save_dir, file_name + ext)
                if os.path.exists(initial_check_path):
                    print(f"[已存在跳過] {file_name}{ext} 已經在資料庫中。")
                    continue

            try:
                pdf_res = requests.get(full_pdf_url, headers=headers, verify=False) 
                if pdf_res.status_code == 200:
                    if ext == ".pdf" and (not file_name or file_name.lower() in GENERIC_NAMES or len(file_name) <= 3):
                        extracted_title = extract_pdf_title(pdf_res.content)
                        if extracted_title:
                            print(f"  [內文解析成功] 檔名從 '{file_name}' 更新為內文標題：'{extracted_title}'")
                            file_name = extracted_title
                    
                    if not file_name or file_name.lower() in GENERIC_NAMES:
                        print(f"[捨棄檔案] 無法解析標題，疑似空檔案或掃描圖檔 (網址: {full_pdf_url})")
                        continue

                    file_name = file_name + ext
                    file_path = os.path.join(save_dir, file_name)

                    if os.path.exists(file_path):
                        print(f"[已存在跳過] 解析後發現 {file_name} 已存在。")
                        continue

                    with open(file_path, "wb") as f:
                        f.write(pdf_res.content)
                    download_count += 1
                    print(f"成功儲存：{file_name}")
                else:
                    print(f"下載失敗 (HTTP {pdf_res.status_code}): {full_pdf_url}")

            except Exception as e:
                print(f"下載發生例外錯誤：{e}")

    print(f"\n[Crawler] 任務完成！共成功下載 {download_count} 份檔案至 `{save_dir}/` 資料夾。")

# 單獨執行此腳本來更新資料庫
if __name__ == "__main__":
    test_urls = [
        "https://www.csie.ncu.edu.tw/downloads",
        "https://pdc.adm.ncu.edu.tw/p/412-1019-1765.php?Lang=zh-tw",
        "https://pdc.adm.ncu.edu.tw/p/412-1019-1706.php?Lang=zh-tw",
        "https://www.lc.ncu.edu.tw/zh-TW/article/2022-08-31%2016:42:00"
    ]

    for idx, url in enumerate(test_urls, 1):
        print(f"\n==========================================")
        print(f"[{idx}/{len(test_urls)}] 開始爬取：{url}")
        print(f"==========================================")

        try:
            download_ncu_pdfs(url)
        except Exception as e:
            print(f"[失敗] {url} 發生錯誤：{e}")

        time.sleep(1.5)