# 系統設計圖(target architecture)

> 狀態:依 2026-06-10 全部定案繪製(見 [`DISCUSSION.md`](DISCUSSION.md));**尚未實作**,是目標架構。
> 唯一未定:A4 授權系統的介接(虛線標示,等 E4b 回覆)。

---

## 1. 部署架構(全封閉網路 / TKS K8s)

```mermaid
flowchart LR
    subgraph net["全封閉公司內網"]
        B["瀏覽器<br/>(≤10 併發, <100 總用戶)"]
        KC["Keycloak SSO<br/>(只管登入, OIDC)"]
        A4["A4 授權系統<br/>(介接待定 E4b)"]

        subgraph k8s["TKS / K8s — 單 replica"]
            subgraph pod["app pod"]
                APP["FastAPI (uvicorn)<br/>· OIDC middleware(強制登入)<br/>· product 編輯鎖 hb30s/TTL5m<br/>· _jobs 記憶體 registry"]
                WK["ProcessPool workers<br/>(parent 代理 blob I/O,<br/>worker 只碰本地 temp)"]
                LS["Litestream sidecar"]
                SCR[("emptyDir scratch<br/>≈1GB (workers×150MB)")]
            end
            PVC[("PVC 10GB RWO block<br/>library.sqlite<br/>WAL + busy_timeout")]
        end

        MINIO["MinIO<br/>· blob bucket(uploads/parsed/match/<br/>rule_check/layer_preview)<br/>· litestream replica(備份/PITR)"]
    end

    B -->|"未登入一律導去"| KC
    B --> APP
    APP -.->|"查角色(方式待定)"| A4
    APP --> PVC
    APP <-->|"put/get/presigned"| MINIO
    APP --> WK
    WK <--> SCR
    LS -->|"連續複製"| MINIO
    LS --> PVC
```

要點:**「多人」靠單 replica 內的鎖與 auth 解,不靠多 replica**(`_jobs` 在記憶體,天生單 replica;HA 是未來牌)。`library.sqlite` 絕不放 MinIO/NFS。

---

## 2. 資料模型(ER)

```mermaid
erDiagram
    PRODUCT ||--o{ VERSION : "容器(≥1版, 建立時必填版號)"
    VERSION ||--|| LIBRARY : "1:1(路線1)"
    LIBRARY ||--o{ CLASS_CONFIG : "每類 match 調參"
    LIBRARY ||--o{ TEMPLATE : "範本(無共用, 空白開始)"
    VERSION ||--o{ VERSION_FILES : "role 綁定"
    FILE ||--o{ VERSION_FILES : "跨版共用(content-hash)"
    PRODUCT ||--o{ PRODUCT_EDITOR : "editor 指派(一對多)"
    PRODUCT ||--o| PRODUCT_LOCK : "悲觀編輯鎖"
    AUDIT_LOG }o--|| PRODUCT : "記錄增刪改/畫押/解鎖"

    PRODUCT {
        text id PK
        text name
        text rules "product 級, 跨版不變(外部 stub)"
    }
    VERSION {
        text id PK
        text product_id FK
        text label "自由輸入, 同product不重複, 不可刪"
        text library_id FK "1:1"
        text signed_off_by "畫押者(NULL=編輯中)"
        real signed_off_at
    }
    TEMPLATE {
        text id PK
        text library_id FK
        text class_name
        text entity_point_sets "JSON 點雲(23-426B/列)"
    }
    VERSION_FILES {
        text version_id FK
        text role "SBT|BD|POD|RING|LID"
        text file_id FK
        text per_version_state "selected_layers/rect/unit override"
    }
    FILE {
        text id PK "content-hash, 純內容儲存"
    }
    USER_ROLE {
        text user_sub PK "Keycloak sub"
        text role "admin(或由 A4 提供, 待E4b)"
    }
    PRODUCT_EDITOR {
        text product_id FK
        text user_sub FK
    }
    PRODUCT_LOCK {
        text product_id PK
        text holder_sub
        real heartbeat_at "30s 續, TTL 5min"
    }
    AUDIT_LOG {
        int id PK
        real ts
        text user_sub
        text product_id
        text version_id
        text action "add|delete|modify|sign|unsign|force_unlock"
        text target_type
        text target_id
        text detail
    }
```

衍生 artifact(MinIO,不在 DB):`uploads/{file_id}.dxf`(跨版共用)、`parsed|match|prematch/{version_id}/{file_id}.json`、`rule_check/{version_id}.json` —— **以 version 為 key,舊版永久可回看**。

---

## 3. Version 生命週期(畫押狀態機)

```mermaid
stateDiagram-v2
    [*] --> 編輯中 : 建 product(必填版號)<br/>或 editor 建新版=clone 上一版<br/>(library+綁定, 只換有改的角色)
    編輯中 --> 已畫押 : editor 畫押<br/>(記錄誰/何時, 寫 audit)
    已畫押 --> 編輯中 : 僅 admin 解畫押<br/>(寫 audit)
    已畫押 --> [*] : 永久保留<br/>唯讀可回看, 不可刪
    note right of 已畫押 : 唯讀凍結 — 範本/檔案/調參/重跑全擋
    note left of 編輯中 : 受 product 編輯鎖保護<br/>(一次一個人類寫入者)
```

---

## 4. 權限模型(三級)

| 角色 | 取得方式 | 能做什麼 |
|---|---|---|
| **Viewer** | 能登入 Keycloak 即是 | 看**所有** product/版本/結果(全部可看) |
| **Editor** | admin 指派(per-product, 一對多) | 被指派 product 內全部動作:上傳/換檔、範本增刪改、調參、rule-check、**建新版**、**畫押** |
| **Admin** | env 白名單或 A4(待 E4b) | + 建/刪 product、指派 editor、強制解鎖、**解畫押** |

身分 key = Keycloak `sub`;角色儲存:App DB 兩張表(若 A4 強制則混合制,待 E4b)。

---

## 5. 對應的施工順序(建議)

1. **versioning + 拓樸轉換**(§2/§3 的 schema;不遷移,砍掉重練)
2. **Plan A 儲存**(BlobStore + MinIO + Litestream;§1 右半)
3. **auth + 編輯鎖 + audit**(§1 左半 + §4;等 A4/infra 回覆 E2/E3/E4b)
4. 150MB DXF perf 驗證(獨立,隨時可做)
