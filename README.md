# 開發注意事項

目前 GitHub 有三個主要 branch：

* `main`：穩定版本，**不要直接在這裡修改**
* `選課`：選課功能開發，之後要改選課相關程式請在這個 branch 修改
* `RAG`：RAG 相關功能

## 如果要做選課功能

第一次使用：

```powershell
git fetch origin
git switch 選課
```

之後每次開始開發前：

```powershell
git switch 選課
git pull origin 選課
```

修改完、測試沒問題後：

```powershell
git status
git add .
git commit -m "描述這次修改"
git push origin 選課
```

## 如果只是要執行專案

先進入虛擬環境：

```powershell
.\venv\Scripts\Activate.ps1
```

看到：

```text
(venv)
```

之後執行：

```powershell
python run.py
```

## 簡單記

```text
不要直接改 main ❌

要做選課 → 切到「選課」branch
要做 RAG  → 切到「RAG」branch

選課開發：
git switch 選課
git pull --ff-only origin 選課
    ↓
修改 / 測試
    ↓
git add .
git commit -m "描述修改"
git push origin 選課
```
