# SMDR2 DRC 串接指南

這份文件給負責 Design Rule Check（DRC）開發的團隊看。讀完應該能：
寫程式去 SMDR2 拉一個 product 的 handoff bundle、解析裡面的
manifest / Match JSON / DXF，然後對需要的幾何/數量/關係跑檢查。

正式契約以同目錄下兩份檔案為準，本文件只是「白話導讀」：

- `spec.md` — 行為規範（每個 SHALL/MUST 都是測試保證會擋住的點）
- `drc-manifest.schema.json` — manifest 的 JSON Schema（draft 2020-12）

如果文件與 spec/schema 不一致，**以 spec 為主**，請回報文件需要更新。

---

## 1. 流程速覽

```
SMDR2 ─[ GET /api/products/{id}/drc-bundle ]──► drc-bundle-<id>.zip
                                                     │
                                                     ▼
                                       ┌── manifest.json
                                       ├── dxfs/<file_id>.dxf  (×N)
                                       └── match/<file_id>.json (×N)
                                                     │
                                                     ▼
                                       讀 manifest → group by role
                                       → 每筆 (DXF, Match JSON) 對
                                       → 套用 rule logic → 產報告
```

每次呼叫端點都是現抓現產的（無快取），re-export 同一個 product
內容會 byte-by-byte 相同，只有 `manifest.exported_at` 不同。

---

## 2. 端點

```
GET /api/products/{product_id}/drc-bundle
```

| HTTP 結果 | 何時發生 | Body |
|---|---|---|
| `200` | 一切就緒，bundle 產出 | `application/zip` 串流 |
| `404` | `product_id` 不存在 | `{"detail": "product not found"}` |
| `400` | product 下沒有任何掛 role 的 DXF | `{"detail": "no DXFs uploaded to this product yet"}` |
| `400` | 至少一個 DXF 還沒 Save Match | `{"detail": "these roles still need Save Match: BD, SBT"}` |

回應 header：

```
Content-Type: application/zip
Content-Disposition: attachment; filename=drc-bundle-<product_id>.zip
```

**沒有任何 auth**（SMDR2 目前部署在內網），若日後加上 token 會在這份
文件先公告。

### 速效呼叫

```bash
curl -fSL -o bundle.zip http://smdr2.local:8000/api/products/35a8dbb0-6ee/drc-bundle
unzip -l bundle.zip
```

---

## 3. Bundle 內部結構

```
drc-bundle-<product_id>.zip
├── manifest.json
├── dxfs/
│   ├── <file_id_1>.dxf
│   ├── <file_id_2>.dxf
│   └── …
└── match/
    ├── <file_id_1>.json
    ├── <file_id_2>.json
    └── …
```

- 檔名一律用 `file_id`（SMDR2 內部的內容雜湊識別字），不會用使用者
  上傳時的原始檔名 — 不會有撞名問題，bundle 完全 deterministic。
- DXF 跟 Match JSON 都是 **byte-for-byte copy**，不會 re-encode。

---

## 4. `manifest.json`

正式 schema：本目錄 `drc-manifest.schema.json`，draft 2020-12。
驗證可用 `jsonschema` （Python）、`ajv` （JS）等。

```json
{
  "bundle_version": "1.0.0",
  "product_id": "35a8dbb0-6ee",
  "product_name": "Sample",
  "exported_at": "2026-05-19T07:30:00Z",
  "files": [
    {
      "role": "BD",
      "file_id": "f7683af846df4d15",
      "dxf": "dxfs/f7683af846df4d15.dxf",
      "match_json": "match/f7683af846df4d15.json"
    },
    {
      "role": "BD",
      "file_id": "48b1731946531f6c",
      "dxf": "dxfs/48b1731946531f6c.dxf",
      "match_json": "match/48b1731946531f6c.json"
    },
    {
      "role": "SBT",
      "file_id": "54f486445b9e1667",
      "dxf": "dxfs/54f486445b9e1667.dxf",
      "match_json": "match/54f486445b9e1667.json"
    }
  ]
}
```

| 欄位 | 必填 | 說明 |
|---|---|---|
| `bundle_version` | ✅ | semver。**主版號不同請拒收**，contract 已斷。 |
| `product_id` | ✅ | SMDR2 內部 ID。對你方而言不透明，做報告 cross-ref 用。 |
| `product_name` | — | 人類可讀名稱，**可能不存在**。 |
| `exported_at` | — | UTC ISO-8601，秒精度。 |
| `files[]` | ✅ | 每筆對應一個 (DXF, Match JSON)；下節說明。 |
| `files[].role` | ✅ | `"SBT"` / `"BD"` / `"POD"` / `"RING"` 之一。**同一個 role 可能出現多次** — 多 DXF 情境。 |
| `files[].file_id` | ✅ | lowercase hex；前 8 字元是 SMDR2 內部慣用的短碼。 |
| `files[].dxf` | ✅ | zip 相對路徑（POSIX 分隔）。 |
| `files[].match_json` | ✅ | 同上。 |

---

## 5. Match JSON 格式

每個 `match/<file_id>.json` 都對應**一張** DXF。內容長這樣：

```json
{
  "top_view.substrate.0": [
    ["65"],
    ["319B"]
  ],
  "bottom_view.substrate.0": [
    ["31BB"],
    ["6099"]
  ],
  "top_view.smd_2t.0": [
    ["4D", "4E", "4F"],
    ["50", "51", "52"]
  ],
  "side_view.fiducial_cross.0": [
    ["31A0"]
  ]
}
```

### Key 格式

兩種：

- `<class>.<template_idx>` — 該 instance 不在任何 view rect 內，
  或這張 DXF 沒設 side region。
- `<view>.<class>.<template_idx>` — instance 的 bbox 中心落在
  該 view 的 rect 內。`<view>` ∈ `{top_view, bottom_view, side_view}`。

> ⚠️ 注意舊版規格只寫 `<class>.<idx>`，**新版實際輸出大多帶 view
> prefix**。寫 parser 時請兩種都吃。

### Value 格式

每個 key 對應的 value 是「比對到的所有 instance 清單」：

```
value: list[list[str]]
        │     └── 每個 inner list = 一個 instance，由若干 DXF handle 組成
        └── 整個外層 = 該 (view, class, template) 在這張 DXF 內的所有出現
```

**Instance 與 handle 的關係**：一個 instance 可能由多個 DXF
entity 組成（例如十字標靶通常是 4 條線段 → 4 個 handle）。所以
每個 inner list 是一群 handle。

### Class 對照表

DXF 內部統一用 snake_case key：

| Match-JSON key | 直觀意義 |
|---|---|
| `substrate` | 基板輪廓 |
| `pin_1` | 1 號接腳標示 |
| `lid` | 封裝蓋板（整體） |
| `ring_outer` | 環外輪廓 |
| `ring_inner` | 環內輪廓 |
| `die_area` | 晶粒區 |
| `dam1` / `dam2` | 封裝壩（內 / 外） |
| `fiducial_circle` | 圓形對位標 |
| `fiducial_cross` | 十字對位標 |
| `smd_2t` / `smd_3t` / `smd_8t` / `smd_14t` | 對應接點數的 SMD |
| `bga_ball` | BGA 錫球 |
| `2d_barcode` | 2D 條碼 |

清單以 SMDR2 設定為準（程式裡是
`app/library.py::CLASS_JSON_KEY`），新增 class 時 schema 不變、
直接出現在 match JSON 即可，**請不要 hard-code 一份白名單**。

### View prefix 語意

`top_view` / `bottom_view` / `side_view` 是工程師在 SMDR2 viewer
上人工框出來的「視圖區」rect。每張 DXF 通常會把多個視圖放在不同
版面位置；如果你的 rule 需要在「同一個視圖」內比幾何（例如距離），
**一定要以 view prefix 為單位 scope** — 不同 view 在圖紙上是不同
座標空間，跨 view 算距離不會有物理意義。

---

## 6. DXF Handle 怎麼用

Handle 就是 DXF 內 `EntityDB` 的索引字串（十六進位）。Match JSON
裡每個 handle 都是該檔案的 **原始 handle**，**沒有** 任何前綴 —
不管是外部交付的 zip bundle，或 SMDR2 內部呼叫你方 function 時
materialise 出來的 bundle dir，handle 一律保持原樣。

如果你看到 `^[0-9a-f]{8}:` 開頭的 handle（例如
`a3f12b9c:7AF`），那是 SMDR2 早期 mock checker 的合併前綴遺留 —
現在那條 path 已經完全移除（rule logic 走你方的 in-tree module，
SMDR2 不再做任何 merge）。若你看到，請回報是 SMDR2 的 bug。

### 在 Python 用 ezdxf 查 handle

```python
import ezdxf

doc = ezdxf.readfile("dxfs/f7683af846df4d15.dxf")
entity = doc.entitydb["319B"]              # 直接吃十六進位字串
print(entity.dxftype())                    # 例: 'LWPOLYLINE'
print(entity.dxf.layer)                    # 來源 layer 名
for v in entity.vertices():                # 看你的 entity 類型
    print(v)
```

對 `LINE` / `CIRCLE` / `LWPOLYLINE` / `POLYLINE` / `ARC` 都成立。
有些 instance 的 handle 集合會跨多個 entity，逐一查再合併。

> 補充：SMDR2 內部會把曲線（CIRCLE / ARC / 帶 bulge 的 polyline）
> flatten 成多邊形，但 **匯出的 DXF 是原檔**，你拿到的 entity 還是
> 原本的型別。要對得上 Match JSON 描述的幾何，建議在你方也 flatten
> 一次（用 `ezdxf` 的 `flattening()` 或自己處理 bulge）。

---

## 7. 多 DXF per role（**重要**）

歷史上 SMDR2 是「一個 role 一個 DXF」，所以早期傳檔給你方都是
4 個檔案。**現在不是了** —— 一個 product 的 BD/SBT/POD/RING
任一 role 都可能含 **≥ 2 張 DXF**（典型：top view + bottom view
分檔；或一個 multi-view + 一個額外 rev）。

`manifest.files[]` 同一個 `role` 會出現多筆。請以 role 作為
groupby key，**不要假設 length == 4** 也不要假設 role 唯一。

### 規則該怎麼跑

| Rule 型態 | 處理方式 |
|---|---|
| 幾何距離 / 鄰接（同 DXF 內） | 對每張 DXF 各跑一次，**不要** 跨 DXF 算距離（座標空間不同）。 |
| 數量 / 統計（per role 聚合） | 把該 role 下所有 DXF 的 match instance 加總後比對。 |
| Sibling 比對（同 role 跨 DXF） | 用 `file_id` 拆組，互比；例 BD top vs BD bottom 的 substrate 數量。 |
| 跨 role | 取出兩個 role 的 instance 集合再做集合運算（例 SBT BGA 總數 vs POD BGA 總數）。 |

### 同一 DXF 內的 view-scope

即使在一張 DXF 內，`top_view` / `bottom_view` / `side_view` 三個區
在圖紙上座標也是分開的（一張圖三個 viewport）。所以：

- 算距離的 rule：**先 group by `(file_id, view)`**，再算。
- 算數量的 rule：依需求決定要 per-view 還是 aggregate。

---

## 8. 範例：Python loader

```python
"""Reference loader for SMDR2 DRC handoff bundles.

Reads a bundle, groups files by role, and yields ready-to-process
(role, file_id, dxf_doc, match_json) tuples.
"""

from __future__ import annotations

import json
import zipfile
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Iterator

import ezdxf
import requests
from jsonschema import validate

SUPPORTED_MAJOR = 1   # bump when you implement v2.x


def fetch_bundle(base_url: str, product_id: str) -> bytes:
    r = requests.get(f"{base_url}/api/products/{product_id}/drc-bundle", timeout=60)
    r.raise_for_status()
    return r.content


def load_bundle(zip_bytes: bytes, schema_path: Path):
    zf = zipfile.ZipFile(BytesIO(zip_bytes))
    manifest = json.loads(zf.read("manifest.json"))
    validate(manifest, json.loads(schema_path.read_text()))   # belt + braces
    major = int(manifest["bundle_version"].split(".", 1)[0])
    if major != SUPPORTED_MAJOR:
        raise RuntimeError(
            f"bundle major version {major} not supported "
            f"(this loader speaks v{SUPPORTED_MAJOR}.x)"
        )
    return zf, manifest


def iter_files_by_role(zf, manifest) -> Iterator[tuple[str, list[dict]]]:
    """Yields (role, [entry, ...]) one role at a time."""
    by_role: dict[str, list[dict]] = defaultdict(list)
    for entry in manifest["files"]:
        by_role[entry["role"]].append(entry)
    yield from by_role.items()


def open_dxf_in_zip(zf, entry):
    """ezdxf.readfile only takes paths, so spool the bytes through StringIO."""
    import io
    raw = zf.read(entry["dxf"]).decode("latin-1")   # DXF is ASCII/latin-1
    return ezdxf.read(io.StringIO(raw))


def load_match_json(zf, entry) -> dict:
    return json.loads(zf.read(entry["match_json"]))


# ---- Example use ---------------------------------------------------------
def run_drc(base_url: str, product_id: str, schema_path: Path):
    zf, manifest = load_bundle(
        fetch_bundle(base_url, product_id), schema_path
    )
    with zf:
        for role, entries in iter_files_by_role(zf, manifest):
            for entry in entries:
                doc = open_dxf_in_zip(zf, entry)
                mj = load_match_json(zf, entry)
                # ---- here: invoke your rule engine ----
                # e.g.  results = check_rules(role, doc, mj)
                ...


if __name__ == "__main__":
    schema = Path("drc-manifest.schema.json")   # ship a copy of the schema
    run_drc("http://smdr2.local:8000", "35a8dbb0-6ee", schema)
```

### View-scoped iteration helper

```python
import re

KEY_RE = re.compile(
    r"^(?:(?P<view>top_view|bottom_view|side_view)\.)?"
    r"(?P<cls>[a-z0-9_]+)\."
    r"(?P<idx>\d+)$"
)

def iter_class_groups(match_json, cls):
    """Yield ((view, template_idx), handles) for every match group of
    the given class. ``view`` is None for unassigned instances."""
    for key, instances in match_json.items():
        m = KEY_RE.match(key)
        if not m or m.group("cls") != cls:
            continue
        view = m.group("view")   # may be None
        idx = m.group("idx")
        for handles in instances:
            yield (view, idx), handles
```

用法：

```python
# 「同一張 DXF 內，每個 (view, template) 的 substrate 跟最近的 SMD-2T 算距離」
sub_by_view = {origin: hs for origin, hs in iter_class_groups(mj, "substrate")}
smd_by_view = {origin: hs for origin, hs in iter_class_groups(mj, "smd_2t")}
for view in set(sub_by_view) & set(smd_by_view):
    sub_handles = sub_by_view[view]
    smd_handles = smd_by_view[view]
    # ... look up entities, compute distance ...
```

---

## 9. 錯誤處理建議

| 狀況 | 建議行為 |
|---|---|
| `400` from SMDR2（Save Match 沒做） | 顯示給操作者，請他們回 SMDR2 把該 role 的 Match Save 起來；不要重試 |
| `404` | product 不存在；不要重試 |
| Manifest schema 驗證失敗 | 寫 log + 拒收；contract 被打破，應該開 issue 給 SMDR2 team |
| Manifest 主版號不認得 | 拒收（見上面的 loader 範例） |
| 同一 role 出現 ≥ 2 個 file，但你的舊 rule 只看 `files[0]` | 修 rule，**不要** 預設 length 1 |
| `match_json` 內出現 `^[0-9a-f]{8}:` 前綴 handle | SMDR2 bug（內部合併前綴外洩），回報；勿自行處理 |

---

## 10. 結果該怎麼回傳？

SMDR2 會以 `from app.external_rule_check import check_rules` 直接 import
你方的 module，呼叫 `check_rules(product_id: str, bundle_dir: str) -> dict`。
你方 return 的 dict 就是 rule check 結果，SMDR2 會：

1. 跑 envelope 驗證（不符合就 raise，job 變 `status: "error"`）
2. 寫到 `data/rule_check/{product_id}.json` 原樣保存
3. viewer 直接 render

正式契約見 `spec.md` 兩個 requirement：
- **RuleChecking JSON output shape** — sub-rule 欄位與不變式
- **External rule function contract** — 邊界呼叫 + envelope 驗證規則

### 回傳格式

```json
{
  "Rule1": {
    "pass": false,
    "text": "Substrate-to-first-SMD-2T distance must exceed 5 mm",
    "rules": [
      {
        "part": "BD",
        "file_id": "f7683af846df4d15",
        "from": "319B",
        "to":   "4D",
        "text": "distance = 3.214 mm (<= 5.0 mm)",
        "tol":      null,
        "tol_text": null
      }
    ]
  }
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `<ruleName>.pass` | bool | 整條 rule 通過與否 |
| `<ruleName>.text` | str | 整條 rule 的描述（pass 或 fail reason） |
| `<ruleName>.rules` | list | 0+ 個 sub-rule（可以是空 list） |
| `rules[].part` | enum | `"SBT"` / `"BD"` / `"POD"` / `"RING"` / `"LID"` — 對應的 role |
| `rules[].file_id` | str \| null | 該 sub-rule 幾何所在的 DXF id；只要 from/to/tol 任一非 null 就必須有值 |
| `rules[].from` | str \| null | 單一 source handle（不再是 list） |
| `rules[].to` | str \| list[str] \| null | Target handle(s)。單字串 = 一個 target（原本的形式）；非空 list = fan，from 連到每個 to_i。空 list 不合法（請 emit null）；list 元素必須是非空 string。只在 `from` 也有值時可以設定（不論 scalar 或 list）。 |
| `rules[].text` | str | sub-rule 訊息；rules 非空時必填 |
| `rules[].tol` | str \| null | 獨立高亮 entity（跟 from/to 距離無關的標註） |
| `rules[].tol_text` | str \| null | 顯示在 `tol` 旁的文字；只在 `tol` 有值時可以設定 |

### Viewer 顯示語意（你方 emit 時請預先決定）

| 你 emit | Viewer 行為 |
|---|---|
| `from` + 單字串 `to` | 兩 entity 間畫虛線，`text` 顯示在中點 |
| `from` + list `to` | 對每個 to_i 從 from 各畫一條虛線（fan）；`text` 只顯示在第一條（to[0]）的中點，避免多個 label 重疊 |
| 只有 `from` | 高亮 `from`，`text` 顯示在 `from` 旁 |
| `tol`（可同時有 from/to） | 高亮 `tol` entity |
| `tol` + `tol_text` | 高亮 `tol`，`tol_text` 顯示在 `tol` 旁 |

你方只需挑出 from/to **是哪一對 entity**（哪兩個 handle）；連線時
落在 entity 上的哪兩個點由 viewer 自己跑 vertex-vs-edge perpendicular-foot
搜尋決定（最短距離的兩個點）。所以「from 距離 to 多少」的 text 你可以
照算的數值寫，但畫線的端點是 viewer 拿這對 entity 自己找出來的。

### 不變式（違反會被 SMDR2 reject）

- `rules` 可以是空 list；非空時每個 sub-rule 必須有非空 `text`
- 任一 handle 欄位（`from` / `to` / `tol`）非 null 時，`file_id` 也必須非 null
- 每個 sub-rule 至少要有 `from` 或 `tol` 其中一個（不能全空）
- `to` 只能在 `from` 也有設的情況下出現（不論 `to` 是 scalar 還是 list）
- `to` 是 list 時必須非空；list 元素必須是非空 string。`to: []` 不合法，請 emit `null` 表達「沒有 to」
- `tol_text` 只能在 `tol` 也有設的情況下出現

---

## 11. 變更與版本

- Manifest 結構變動會 bump `bundle_version`：
  - **MAJOR**：契約破壞性變更（欄位移除、語意改變）
  - **MINOR**：相容性新增（多新欄位、新 enum 值）
  - **PATCH**：純文字 / 註解修正
- SMDR2 端會在 `spec.md` 用 `### Requirement:` 區塊紀錄每次變更，
  archive 後可在 git log 追到完整歷史。
- 任何契約問題請開 issue / 聯絡 SMDR2 維護者；不要自行繞過
  schema 驗證。

---

## Appendix A. 真實 bundle 範例（debug 用）

可以拿 SMDR2 dev 機任一個 product 來看：

```bash
curl -fSL -o sample.zip http://smdr2.local:8000/api/products/<pid>/drc-bundle
unzip -d sample sample.zip
jq . sample/manifest.json
jq 'keys[:5]' sample/match/<file_id>.json
```

典型 production bundle 大小：每張 DXF 100KB–2MB，整包通常 < 10MB。
