import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import urllib3
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def download_ncu_pdfs(target_url: str, save_dir: str = "data"):
    """
    自動爬取指定網頁中的所有 PDF 檔案，並儲存到本地資料夾
    """
    print(f"\n[Crawler] 啟動自動化爬蟲，準備掃描網頁：{target_url}")
    
    # 確保存放 PDF 的資料夾存在
    os.makedirs(save_dir, exist_ok=True)
    
    # 偽裝成真人瀏覽器發送請求
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(target_url, headers=headers, verify=False)
        response.encoding = 'utf-8'
        response.raise_for_status() # 如果網頁掛了會直接報錯
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
        if not href or "javascript" in href or href == "#":
            continue

        if "download" in href.lower() or "file" in href.lower() or ".pdf" in href.lower() or ".doc" in href.lower():
            full_pdf_url = urljoin(target_url, href)
            raw_text = link.text.strip()

            # 遇到有 \n 的資料只取第一行
            file_name = raw_text.split('\n')[0].strip()

            if not file_name:
                file_name = f"未命名法規_{download_count}"
            file_name = re.sub(r'[\\/*?:"<>|\n\r\t]', "", file_name)

            if ".docx" in href.lower() or ".docx" in file_name.lower():
                ext = ".docx"
            elif ".doc" in href.lower() or ".doc" in file_name.lower():
                ext = ".doc"
            else:
                ext = ".pdf"

            file_name = re.sub(r'\.pdf$|\.docx?$|\.DOCX?$', '', file_name, flags=re.IGNORECASE)

            file_name = file_name + ext
                
            file_path = os.path.join(save_dir, file_name)
            
            print(f"發現疑似法規：{file_name}，正在下載... (網址: {full_pdf_url})")
            try:
                pdf_res = requests.get(full_pdf_url, headers=headers, verify=False) 
                if pdf_res.status_code == 200:
                    with open(file_path, "wb") as f:
                        f.write(pdf_res.content)
                    download_count += 1
                    print(f"下載成功！")
                else:
                    print(f"下載失敗，狀態碼：{pdf_res.status_code}")

            except Exception as e:
                print(f"下載失敗：{e}")
                
    print(f"\n[Crawler] 任務完成！共成功下載 {download_count} 份 PDF 至 `{save_dir}/` 資料夾。")

# 單獨執行此腳本來更新資料庫
if __name__ == "__main__":

    test_url = "https://cis.ncu.edu.tw/Course/main/news/stdExplanation" 
    download_ncu_pdfs(test_url)