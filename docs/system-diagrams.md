# 系統設計圖集(C4 / 流程圖 / DFD / UML)

> 配套 [`system-design.md`](system-design.md) 的完整視圖集,依 2026-06-10 定案繪製。全部 Mermaid,GitHub 直接渲染。
> 涵蓋:**C4**(L1 Context / L2 Container / L3 Component)、**系統流程圖**、**DFD**(L0/L1)、**UML**(use case / class / sequence / state / activity)。

---

## 1. C4 模型

### 1.1 C4-L1:System Context

```mermaid
C4Context
    title 尋形(Conform)— 系統情境圖(全封閉內網)
    Person(editor, "Editor", "封裝工程師:上傳圖、框範本、跑檢查、畫押")
    Person(viewer, "Viewer", "登入即可看所有 product/版本/結果")
    Person(admin, "Admin", "建/刪 product、指派 editor、強制解鎖、解畫押")

    System(conform, "尋形 Conform", "DXF pattern 分類 + 範本比對 + DRC 檢查;product 多版本管理與畫押")

    System_Ext(kc, "Keycloak SSO", "公司身分認證(只管登入, OIDC)")
    System_Ext(a4, "A4 授權系統", "公司授權系統(介接待定 E4b)")
    System_Ext(drc, "外部規則團隊", "提供 product 級規則邏輯(stub 介面)與 DRC bundle 消費")

    Rel(editor, conform, "編輯(需鎖+指派)", "HTTPS")
    Rel(viewer, conform, "唯讀檢視", "HTTPS")
    Rel(admin, conform, "管理操作", "HTTPS")
    Rel(conform, kc, "OIDC 認證(未登入強制導去)")
    Rel(conform, a4, "角色查詢(方式待定)")
    Rel(conform, drc, "check_rules(product, bundle) / DRC bundle 匯出")
```

### 1.2 C4-L2:Container

```mermaid
C4Container
    title 尋形 — 容器圖(TKS/K8s 單 replica)
    Person(user, "Editor / Viewer / Admin")
    System_Ext(kc, "Keycloak SSO")
    System_Ext(a4, "A4(待定)")

    Container_Boundary(pod, "app pod(單 replica)") {
        Container(web, "Viewer 前端", "HTML + canvas.js", "AutoCAD 式互動:框選、live match、class 工具列、版本切換器")
        Container(api, "FastAPI app", "Python / uvicorn 單行程", "API、OIDC、權限/鎖/畫押守門、_jobs 記憶體 registry、blob 代理")
        Container(workers, "ProcessPool workers", "Python 子行程 ×N", "CPU-bound:DXF parse、template matching、rule-check;只碰本地 temp")
        Container(ls, "Litestream", "sidecar binary", "SQLite → MinIO 連續複製(PITR)")
    }
    ContainerDb(sqlite, "library.sqlite", "SQLite on PVC 10GB RWO", "products/versions/libraries/templates/version_files/locks/roles/audit")
    ContainerDb(minio, "MinIO", "S3 相容物件儲存", "uploads(content-hash)、parsed/match/rule_check(以 version 為 key)、litestream replica")

    Rel(user, web, "瀏覽器")
    Rel(web, api, "JSON API", "HTTPS")
    Rel(api, kc, "OIDC code flow")
    Rel(api, a4, "角色查詢(待定)")
    Rel(api, sqlite, "讀寫(WAL + busy_timeout)")
    Rel(api, minio, "put/get/presigned")
    Rel(api, workers, "submit(本地 temp 路徑)")
    Rel(ls, sqlite, "監看 WAL")
    Rel(ls, minio, "連續複製")
```

### 1.3 C4-L3:Component(FastAPI app 內部)

```mermaid
C4Component
    title FastAPI app — 元件圖
    Container_Boundary(api, "FastAPI app") {
        Component(oidc, "OIDC Middleware", "authlib/自製", "未登入 302 → Keycloak;session cookie;sub 為身分 key")
        Component(guard, "寫入守門鏈", "decorator/dependency", "角色 → product 指派 → 持鎖 → 目標版未畫押,全部 mutating endpoint 統一過")
        Component(lockmgr, "LockManager", "product_lock 表", "原子搶鎖 / heartbeat 30s / TTL 5min / admin force")
        Component(prodapi, "Products/Versions API", "router", "product CRUD(admin)、版本建立=clone、畫押/解畫押")
        Component(fileapi, "Files/Match API", "router(既有)", "選層、框選、live match、commit、scan-all;以 version_files 解析狀態")
        Component(jobs, "JobManager", "jobs.py + _jobs dict", "ProcessPool 排程、parent 代理 blob I/O、冪等 job")
        Component(blob, "BlobStore", "protocol", "LocalBlobStore(dev)/ MinioBlobStore(prod);(version_id, file_id) keying")
        Component(stores, "Relational Stores", "FileStore/Library/ProductStore", "全部 SQL 封裝;worker 一律重讀不吃 cache")
        Component(rule, "RuleCheck Adapter", "rule_check.py", "外部 stub 介接 + envelope 驗證")
        Component(audit, "AuditLogger", "audit_log 表", "增刪改/sign/unsign/force_unlock,與業務寫入同交易")
    }
    Rel(oidc, guard, "user context")
    Rel(guard, lockmgr, "持鎖檢查")
    Rel(prodapi, stores, "")
    Rel(prodapi, audit, "sign/unsign")
    Rel(fileapi, jobs, "觸發背景工作")
    Rel(fileapi, audit, "範本/檔案/調參 增刪改")
    Rel(jobs, blob, "下載 input / 上傳 output")
    Rel(jobs, stores, "load_library() 重讀")
    Rel(jobs, rule, "rule-check job")
```

---

## 2. 系統流程圖(end-to-end 業務流程)

```mermaid
flowchart TD
    A([使用者開啟系統]) --> B{已登入?}
    B -- 否 --> KC[302 → Keycloak SSO] --> B
    B -- 是 --> C[載入 product 清單<br/>人人可看 A3]
    C --> D{角色與意圖}

    D -- "Viewer:看" --> V[選 product + 版本切換器<br/>檢視圖/match/rule 結果<br/>含已畫押舊版] --> Z([結束])

    D -- "Admin:管理" --> M[建 product 必填版號 /<br/>指派 editor / 解畫押 /<br/>強制解鎖 / 刪 product] --> Z

    D -- "Editor:編輯" --> E{是該 product<br/>的 editor?}
    E -- 否 --> V
    E -- 是 --> F[POST lock 搶編輯鎖]
    F --> G{搶到?}
    G -- "否(423)" --> H[顯示誰在編+從何時<br/>唯讀等待或找 admin] --> Z
    G -- 是 --> I{目標版本已畫押?}
    I -- 是 --> J[唯讀;若要改 → 建新版<br/>= clone 上一版] --> K
    I -- 否 --> K[編輯 session<br/>heartbeat 30s]
    K --> L[上傳/替換角色檔<br/>SBT/BD 可沿用前版 只換 POD]
    L --> N[選層 → 預處理 job]
    N --> O[框選 pattern → live match<br/>→ ✓ commit 範本 寫 audit]
    O --> P[scan-all 全圖比對<br/>match/version_id/file_id.json]
    P --> Q{所有角色檔完成?}
    Q -- 否 --> L
    Q -- 是 --> R[rule-check job<br/>product 規則 × 該版全部檔]
    R --> S[檢視報告]
    S --> T{結果 OK?}
    T -- 否 --> O
    T -- 是 --> U[畫押 sign-off<br/>記誰/何時 寫 audit<br/>版本唯讀凍結]
    U --> W[釋放鎖] --> Z
```

---

## 3. DFD(資料流圖)

### 3.1 DFD-L0(Context)

```mermaid
flowchart LR
    ED[/Editor/] -->|DXF、框選、調參、畫押| S((0<br/>尋形<br/>Conform))
    AD[/Admin/] -->|product CRUD、指派、解畫押| S
    S -->|圖面、match 結果、DRC 報告、audit| VW[/Viewer · Editor · Admin/]
    KC[/Keycloak/] -->|id_token sub| S
    A4[/A4 待定/] -.->|角色| S
    RT[/外部規則團隊/] -->|規則邏輯 stub| S
    S -->|DRC bundle| RT
```

### 3.2 DFD-L1(主要 process 與 data store)

```mermaid
flowchart TD
    ED[/Editor/]
    AD[/Admin/]
    VW[/Viewer/]
    KC[/Keycloak/]

    P1((P1 認證授權<br/>OIDC+守門鏈))
    P2((P2 版本與檔案管理<br/>建版=clone、綁定))
    P3((P3 預處理<br/>parse/選層))
    P4((P4 比對<br/>live match/commit/scan-all))
    P5((P5 規則檢查<br/>外部 stub))
    P6((P6 畫押與審計))

    D1[(D1 關聯資料<br/>SQLite:products/versions/<br/>libraries/templates/version_files)]
    D2[(D2 DXF 原檔<br/>MinIO uploads/ content-hash)]
    D3[(D3 衍生結果<br/>MinIO parsed/match/rule_check<br/>以 version_id,file_id 為 key)]
    D4[(D4 audit_log)]
    D5[(D5 鎖與角色<br/>product_lock/user_roles/<br/>product_editors)]

    KC -->|sub| P1
    ED & AD -->|每個請求| P1
    P1 <-->|角色/指派/鎖/畫押狀態| D5
    P1 -->|放行的寫入| P2

    ED -->|DXF bytes| P2
    P2 -->|去重存檔| D2
    P2 -->|versions/version_files| D1
    P2 -->|觸發| P3
    P3 -->|讀原檔| D2
    P3 -->|parsed JSON| D3
    ED -->|框選/調參| P4
    P4 <-->|templates/class_config| D1
    P4 -->|讀 parsed| D3
    P4 -->|match JSON| D3
    P4 -->|增刪改事件| D4
    P2 -->|齊備後觸發| P5
    P5 -->|讀該版全部結果| D3
    P5 -->|rule_check JSON| D3
    ED -->|畫押| P6
    AD -->|解畫押/強制解鎖| P6
    P6 -->|signed_off_by/at| D1
    P6 -->|sign/unsign/force 事件| D4
    D1 & D3 & D4 -->|查詢/檢視| VW
```

---

## 4. UML

### 4.1 Use Case 圖(近似表示)

```mermaid
flowchart LR
    subgraph actors[" "]
        direction TB
        V([Viewer])
        E([Editor])
        A([Admin])
    end
    subgraph system["尋形 Conform"]
        UC1(["檢視 product/版本/結果(含舊版)"])
        UC2(["搶/釋放編輯鎖"])
        UC3(["上傳/替換角色檔(沿用前版)"])
        UC4(["框選→比對→commit 範本"])
        UC5(["調 match 參數"])
        UC6(["跑 rule-check"])
        UC7(["建新版本(=clone)"])
        UC8(["畫押版本"])
        UC9(["建/刪 product、指派 editor"])
        UC10(["解畫押、強制解鎖"])
        UC11(["查 audit log"])
    end
    V --> UC1
    E --> UC1 & UC2 & UC3 & UC4 & UC5 & UC6 & UC7 & UC8
    A --> UC1 & UC9 & UC10 & UC11
    UC3 & UC4 & UC5 & UC6 & UC7 -.->|«include» 持鎖+未畫押| UC2
```

### 4.2 Class 圖(領域模型)

```mermaid
classDiagram
    class Product {
        +str id
        +str name
        +rules() 外部stub 跨版不變
    }
    class Version {
        +str id
        +str label  UNIQUE per product
        +str signed_off_by  NULL=編輯中
        +float signed_off_at
        +sign_off(user)
        +unsign(admin)
        +is_frozen() bool
    }
    class Library {
        +str id
        +clone() Library  建新版用
    }
    class ClassConfig {
        +str name
        +str strategy
        +float bbox_ratio
    }
    class Template {
        +str id
        +str class_name
        +json entity_point_sets
        +signature() dedup用
    }
    class VersionFile {
        +str role  SBT|BD|POD|RING|LID
        +json selected_layers
        +json overrides
    }
    class File {
        +str id  content-hash
        +int size
    }
    class ProductLock {
        +str holder_sub
        +float heartbeat_at
        +claim(user) atomic
        +is_expired() TTL 5min
    }
    class ProductEditor {
        +str user_sub
    }
    class AuditEntry {
        +float ts
        +str user_sub
        +str action
        +str target_type
        +str target_id
    }

    Product "1" *-- "1..*" Version : 容器(不可刪版)
    Version "1" *-- "1" Library : 1:1 快照
    Library "1" *-- "*" Template
    Library "1" *-- "*" ClassConfig
    Version "1" *-- "0..5" VersionFile : role 綁定
    VersionFile "*" --> "1" File : 跨版共用
    Product "1" o-- "0..1" ProductLock
    Product "1" o-- "*" ProductEditor
    Product "1" o-- "*" AuditEntry
```

### 4.3 Sequence 圖:建新版 → 換 POD → 比對 → 畫押

```mermaid
sequenceDiagram
    actor E as Editor
    participant G as 守門鏈(角色/鎖/畫押)
    participant API as FastAPI
    participant DB as SQLite
    participant J as JobManager+Workers
    participant M as MinIO

    E->>G: POST /products/{pid}/versions {label:"v2"}
    G->>G: editor指派✓ 持鎖✓
    G->>API: 放行
    API->>DB: UNIQUE(pid,label) 檢查 → clone library(templates+config) + 複製 version_files
    API-->>E: 201 v2(SBT/BD 沿用 v1 綁定)

    E->>G: POST /versions/v2/files {role:POD, dxf}
    G->>API: 放行(v2 未畫押✓)
    API->>API: content-hash → 去重
    API->>M: put uploads/{hash}.dxf(已存在則略過)
    API->>DB: version_files(v2,POD,hash) upsert
    API->>J: 預處理 job
    J->>M: get 原檔 → 本地 temp
    J->>J: worker: parse(只碰本地)
    J->>M: put parsed/v2/{hash}.json

    E->>API: 框選 → live match → commit
    API->>DB: template insert(v2 的 library)+ audit(add)
    E->>API: scan-all
    API->>J: 比對 job
    J->>M: put match/v2/{hash}.json(不動 v1 的結果)

    E->>G: POST /versions/v2/rule-check
    G->>API: 放行
    API->>J: rule-check job(product 規則 × v2 全部角色檔)
    J->>M: put rule_check/v2.json

    E->>G: POST /versions/v2/sign-off
    G->>API: 放行
    API->>DB: signed_off_by=sub, at=now + audit(sign)
    API-->>E: 200(v2 唯讀凍結,顯示誰/何時)
```

### 4.4 Sequence 圖:鎖競爭與 admin 介入

```mermaid
sequenceDiagram
    actor E1 as Editor甲
    actor E2 as Editor乙
    actor AD as Admin
    participant API as FastAPI
    participant DB as SQLite(product_lock)

    E1->>API: POST /products/p/lock
    API->>DB: 原子 claim(holder=甲)
    API-->>E1: 200
    loop 每30s
        E1->>API: heartbeat
    end
    E2->>API: POST /products/p/lock
    API->>DB: claim 失敗(甲的 heartbeat 未逾5min)
    API-->>E2: 423 {holder:甲, since:…}
    Note over E2: 唯讀等待(D1:不做接手通知)
    alt 甲關分頁/休眠
        Note over DB: heartbeat 停 → 5min 後過期
        E2->>API: 再 POST lock → 200
    else 急件
        AD->>API: DELETE /products/p/lock?force=1
        API->>DB: 釋放 + audit(force_unlock)
        E2->>API: POST lock → 200
    end
```

### 4.5 State 圖:Version 生命週期

```mermaid
stateDiagram-v2
    [*] --> 編輯中 : 建 product(必填版號)<br/>或建新版 = clone 上一版
    編輯中 --> 編輯中 : 上傳/換檔、commit 範本、調參、重跑<br/>(需:editor 指派 + 持 product 鎖)
    編輯中 --> 已畫押 : editor sign-off<br/>(記 who/when, audit)
    已畫押 --> 編輯中 : 僅 admin unsign(audit)
    已畫押 --> [*] : 永久保留:唯讀可回看,不可刪
    note right of 已畫押 : 守門鏈擋所有寫入<br/>(server 端, 非前端)
```

### 4.6 State 圖:檔案處理管線(單一角色檔)

```mermaid
stateDiagram-v2
    [*] --> uploaded : 上傳/沿用(content-hash 去重)
    uploaded --> layers_discovered : discover-layers job
    layers_discovered --> parsing : user 確認選層(存 version_files)
    parsing --> ready_to_match : parse job 完成<br/>parsed/(vid,fid).json
    parsing --> failed : parser 錯誤
    failed --> parsing : 重觸發(job 冪等)
    ready_to_match --> matched : 框選/commit/scan-all<br/>match/(vid,fid).json
    matched --> matched : 迭代框選(版本未畫押)
    matched --> [*] : 該版 rule-check 納入
```

### 4.7 Activity 圖:寫入守門鏈(所有 mutating endpoint)

```mermaid
flowchart TD
    A([收到寫入請求]) --> B{已登入?}
    B -- 否 --> R1[302 → Keycloak]
    B -- 是 --> C{admin?}
    C -- 是 --> H
    C -- 否 --> D{是該 product 的 editor?}
    D -- 否 --> R2[403]
    D -- 是 --> E{持有 product 編輯鎖?}
    E -- 否 --> R3[423 + 持有者資訊]
    E -- 是 --> F{目標版本未畫押?}
    F -- 否 --> R4[409 已凍結]
    F -- 是 --> H[執行業務寫入]
    H --> I[同交易寫 audit_log]
    I --> Z([回應])
```

---

## 索引:圖 ↔ 設計書章節

| 圖 | 對應 system-design.md |
|---|---|
| C4 L1/L2/L3 | §6 高層架構、§7 細部設計 |
| 系統流程圖 | §4 API、§7.1/7.3/7.4 |
| DFD L0/L1 | §5.2 blob 佈局、§7.4 管線 |
| UML use case / class | §2 需求、§5.1 資料模型 |
| UML sequence ×2 | §7.1 鎖、§7.3 版本、§7.4 管線 |
| UML state ×2 | §7.3 生命週期、§7.4 管線 |
| Activity(守門鏈) | §4 權限矩陣與守門順序 |
