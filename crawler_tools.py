import os
import requests
from urllib.parse import urljoin
import urllib3
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def download_ncu_pdfs(api_url: str, base_url: str = "https://cis.ncu.edu.tw", save_dir: str = "data"):
    """
    直接呼叫後端 API 取得 PDF 列表，並儲存到本地資料夾
    """
    print(f"\n[Crawler] 啟動 API 爬蟲，準備呼叫：{api_url}")
    
    os.makedirs(save_dir, exist_ok=True)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        # 有些學校 API 會檢查你是不是從特定網頁點過來的，如果被擋可以解除這行註解
        # "Referer": "https://cis.ncu.edu.tw/Course/main/news/stdExplanation"
    }
    
    try:
        response = requests.get(api_url, headers=headers, verify=False)
        response.raise_for_status() 
        
        data = response.json() 
        print(f"[Crawler] API 呼叫成功！")
        
    except Exception as e:
        print(f"[Crawler] 無法連線至 API 或解析 JSON 失敗，錯誤訊息：{e}")
        return

    download_count = 0
    
    # ==========================================
    # 請根據你在 F12 (Preview) 看到的 JSON 結構修改
    # 如果 data 是一個列表 [ {...}, {...} ]，就直接用 for item in data:
    # 如果 data 是一個字典 { "files": [...] }，就要寫 for item in data["files"]:
    # ==========================================
    
    # 這裡先假設 API 回傳的是一個列表
    items_list = data if isinstance(data, list) else data.get("data", []) 

    for item in items_list:
        # 這裡的 "title" 和 "url" 請換成 API 真實回傳的英文 key
        raw_text = item.get("title", "")   # 可能是 "fileName", "subject" 等等
        href = item.get("url", "")         # 可能是 "downloadUrl", "link" 等等

        if not href:
            continue

        full_pdf_url = urljoin(base_url, href)

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
        
        print(f"發現法規：{file_name}，正在下載... (網址: {full_pdf_url})")
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
            
    print(f"\n[Crawler] 任務完成！共成功下載 {download_count} 份檔案 至 `{save_dir}/` 資料夾。")

# 單獨執行此腳本來更新資料庫
if __name__ == "__main__":
    api_url = "https://cis.ncu.edu.tw/Course/main/news/stdExplanation" 
    download_ncu_pdfs(api_url)