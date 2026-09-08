# 資料後端

這個資料夾負責的 MVP 有：

- 抓取官方福利與機構資料
- PDF 附件文字與欄位抽取
- 福利分類與受眾標籤
- SQLite 資料庫與異動偵測
- 福利搜尋、資格初步比對與 API
- 匯出前端可使用的 JSON

## 第一次執行

請在 PowerShell 執行：

```powershell
    # cd "C:\Users\prapr\Documents\ChatGPT\innoserve\mvp"
    python -m pip install -r requirements.txt
    .\run.ps1 init
```

如果 PowerShell 阻擋 `.ps1`，只需對目前使用者設定一次：

```powershell
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 測試程式

```powershell
    .\run.ps1 test
```

看到 `OK`，代表離線測試通過。

## 抓取官方資料

```powershell
    .\run.ps1 refresh
```

成功時會看到 `status: success`。目前資料來源包含中央政府與臺北市官方福利頁面、
臺北市長照 JSON，以及老人福利機構與護理之家 CSV；抓取時需要網路。

目前可查詢的受眾與主題：

- 學生：獎學金、就學貸款、弱勢學生補助、住宿補助、就學補助
- 上班族：勞工補助、失業給付、職業訓練、育兒與就業支持
- 長者：老人津貼、居家服務、交通接送、醫療照護、機構與失智照顧
- 一般民眾：急難救助、身心障礙福利、住宅補助、育兒福利

新增的官方申辦頁面清單放在 `config/sources.json` 的 `official_benefit_pages.items`。
每一筆都保留官方網址、主管機關、適用地區、受眾與主題標籤。

## 產生 JSON

```powershell
    .\run.ps1 export
```

輸出位置：

- `data/export/benefits.json`
- `data/export/institutions.json`

## 測試搜尋與資格比對

```powershell
.\run.ps1 search 交通
.\run.ps1 search 長照 --audience 長者
.\run.ps1 search 獎學金 --audience 學生
.\run.ps1 search 失業 --audience 上班族
.\run.ps1 search 急難 --audience 一般民眾
.\run.ps1 match '{"age":72,"city":"臺北市","long_term_care_level":3,"query":"交通"}'
```

## 啟動 API

```powershell
    .\run.ps1 serve --port 8000
```

看到類似 `Serving HTTP on 127.0.0.1 port 8000` 後，在瀏覽器開啟：

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/api/v1/benefits?q=交通`
- `http://127.0.0.1:8000/api/v1/institutions?district=士林區`

按 `Ctrl+C` 停止 API。

## 執行後產生的資料

`data/` 會由程式自動建立，包含 SQLite 與 JSON。這些不是程式碼，不需要手動編輯。

## 專案結構

```text
mvp/
├── welfare_backend/          核心後端程式
│   ├── api.py                HTTP API，提供福利與機構查詢
│   ├── cli.py                指令列入口，例如 refresh、search、match
│   ├── config.py             設定檔與預設資料路徑
│   ├── db.py                 SQLite 資料庫與異動紀錄
│   ├── fetch.py              下載官方網頁、JSON、CSV 與 PDF
│   ├── matcher.py             個人條件與福利資格初步比對
│   ├── parse.py              網頁清理、欄位抽取與福利分類
│   ├── pdf_extract.py         PDF 文字與附加欄位抽取
│   ├── pipeline.py            整合抓取、解析、更新資料庫的流程
│   ├── __main__.py            支援以 Python 模組方式啟動
│   └── __init__.py            Python 套件標記檔
├── config/
│   └── sources.json           官方資料來源與抓取設定
├── tests/
│   └── test_backend.py        離線單元測試
├── requirements.txt           Python 外部套件清單
├── run.ps1                    Windows PowerShell 執行入口
├── README.md                  本使用說明
├── .gitignore                 排除資料庫、匯出檔與暫存檔
└── data/                      執行後自動產生，不是核心程式碼
    ├── welfare.sqlite3       SQLite 資料庫
    └── export/                前端使用的 benefits.json 與 institutions.json
```

其中 `welfare_backend/` 是整個 MVP 的核心；`config/sources.json` 決定要抓取哪些官方來源；`tests/` 用來確認程式修改後仍能正常運作。`data/` 由程式自動建立，通常不需要手動編輯或上傳到 GitHub。

## 整個資料流程

本 MVP 有兩條主要流程：一條負責「抓取與整理資料」，另一條負責「讓前端或其他程式查詢資料」。

### A. 抓取與整理官方資料

```text
輸入 PowerShell 指令
        │
        ▼
run.ps1 執行入口，將指令交給 Python
        │
        ▼
cli.py 判斷要執行 init、refresh、search、match、export 或 serve
        │
        ├── 讀取 config/sources.json
        │   確認要抓哪些官方來源、來源格式與是否啟用
        │
        ▼
pipeline.py 安排整個抓取、解析與更新流程
        │
        ├── fetch.py
        │   下載官方 JSON、CSV、HTML 與 PDF
        │
        ├── parse.py
        │   清理網頁、抽取福利欄位、分類服務類型與受眾
        │
        └── pdf_extract.py
            解析 PDF 文字、資格、補助內容、申請方式與附件欄位
        │
        ▼
db.py 寫入 SQLite，判斷新增、更新、未變更或停用
        │
        ├── data/welfare.sqlite3
        │   系統主要資料庫
        │
        └── changes
            保存資料異動紀錄，供之後提醒功能使用
```

### B. 查詢與提供資料

```text
SQLite 資料庫
        │
        ├── cli.py
        │   提供 search、match、institutions 等指令
        │
        ├── api.py
        │   啟動 HTTP API，讓前端或聊天機器人透過網址查詢
        │
        └── cli.py 的 export
            產生 benefits.json 與 institutions.json
```

### 各檔案在流程中的角色

| 檔案 | 主要工作 |
|---|---|
| `run.ps1` | Windows PowerShell 的啟動入口 |
| `cli.py` | 判斷使用者輸入的指令並分派工作 |
| `config.py` | 讀取設定檔與預設路徑 |
| `pipeline.py` | 管理完整資料更新流程 |
| `fetch.py` | 從官方網站下載資料 |
| `parse.py` | 解析 HTML 福利頁面 |
| `pdf_extract.py` | 解析 PDF 附件 |
| `db.py` | 儲存、搜尋與更新 SQLite 資料 |
| `matcher.py` | 根據個人條件做資格初步比對 |
| `api.py` | 對外提供查詢 API |

例如執行 `.\run.ps1 refresh` 時，主要走 A 流程；執行 `.\run.ps1 search 交通` 或啟動 API 時，主要走 B 流程。

## 主要程式功能簡述

以下是各個主要檔案的簡短功能說明，方便在簡報或分工文件中使用：

```text
run.ps1
執行入口：接收 PowerShell 指令，啟動對應的 Python 功能

cli.py
指令控制中心：判斷要執行抓取、搜尋、資格比對、匯出或 API

config.py
設定管理：讀取資料來源設定與資料庫、匯出資料夾路徑

pipeline.py
流程管理：把下載、解析、PDF 處理與資料庫更新串在一起

fetch.py
資料下載：從官方網站下載 JSON、CSV、HTML 與 PDF

parse.py
網頁整理：把 HTML 網頁轉成統一的福利資料

pdf_extract.py
PDF 整理：把 PDF 內容轉成福利的附加文字與結構化欄位

db.py
資料庫管理：儲存福利與機構資料、搜尋資料、記錄資料異動

matcher.py
資格比對：比較個人條件與福利資格，提供初步符合結果

api.py
查詢服務：提供 HTTP API，讓前端或聊天機器人查詢資料
```
