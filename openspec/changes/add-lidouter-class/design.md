# Design: add-lidouter-class

## D1 — 改名規則的方向修正,不回滾舊資料

06-09 的 `LEGACY_CLASS_RENAME` 把 `LidOuter`/`lid_outer` 都指向 `RingOuter`,
因為當時 LidOuter 不再存在。LidOuter 重新成為正式 class 後:

- `"LidOuter": "RingOuter"` **必須移除** — 否則新建的 LidOuter class 與範本
  在下次 boot 被整批改名走(改名 pass 同時改 classes 與 templates 兩表)。
- `"lid_outer": "LidOuter"` — snake_case 舊資料按字面意義歸位。
- 已被 06-09 pass 轉成 RingOuter 的列**不回滾**:無法區分哪些原本真是 ring,
  且 prod 空庫開始,dev 資料不搬(2.3)。

## D2 — 其餘全走既有機制

- 種子:boot 的「補齊 DEFAULT_CLASSES」冪等迴圈讓所有既有 library 自動長出
  LidOuter,rank 依 DEFAULT_CLASSES 順序(排 Lid 後)。
- match 預設:`signature / 0.0001` — 與 Substrate/RingOuter 同為大型剛性
  外框,chamfer 對 winding/起點敏感的問題相同。
- view constraint:不設(同 Lid/Ring — 結構框不限視圖)。
- 顏色:`#7e57c2`(紫家族 — Lid `#9575cd`、RingOuter `#ba68c8` 的同族異色)。
- 一致性由既有 invariant 守住:DEFAULT_CLASSES ⊆ CLASS_CATEGORY 的 boot
  assertion + canvas.js drift-guard 測試。
