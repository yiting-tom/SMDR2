# Proposal: add-lidouter-class

## Why

2026-06-12 需求:結構類 class 最終集合應為 **Lid、LidOuter、RingInner、RingOuter**。
06-09 的改名把 LidOuter/LidInner 整併進 RingOuter/RingInner,但 Lid 蓋子的外框
(LidOuter)與 stiffener ring 的內外框是**不同的實體 feature**,需要獨立分類;
LidInner 維持刪除(蓋子無有意義的內框,06-09 已併入 RingInner)。

## What Changes

- `DEFAULT_CLASSES` 重新引入 `LidOuter`(排在 Lid 之後;既有 library 由
  boot 補種子自動獲得)。
- match 設定:`LidOuter` 預設 `signature / 0.0001`(大型剛性外框,同
  RingOuter 理由)。
- match JSON key:`LidOuter` → `lid_outer`(⚠️ 此 key 06-09 前語意 = 現在的
  RingOuter;規則團隊需知悉語意回歸 — prod 空庫,無資料影響)。
- legacy 改名規則:**移除** `LidOuter→RingOuter`;`lid_outer`(snake)改指回
  `LidOuter`。已被 06-09 轉換過的 dev 資料維持 RingOuter 不回滾。
- 分類/顏色:`LidOuter` 入 structure 群組;canvas.js 鏡像(顏色 + 分組)同步,
  drift-guard 測試鎖住。

## Capabilities

### Modified Capabilities

- `template-library`:canonical class 集合異動(重新引入 LidOuter)。

## Impact

- **Code**:`app/library.py`(5 個常數)、`app/static/canvas.js`(2 個鏡像)。
- **資料**:無 schema 變更;boot 種子補類 + 改名規則調整皆冪等。
- **下游**:rule-check JSON 多一個 `lid_outer` key — 知會規則團隊。
