# Tasks: add-lidouter-class

## 1. library.py

- [x] 1.1 `DEFAULT_CLASSES`:`LidOuter` 插在 `Lid` 後
- [x] 1.2 `CLASS_DEFAULT_MATCH_CONFIG`:`LidOuter: (signature, 0.0001)`
- [x] 1.3 `CLASS_JSON_KEY`:`LidOuter → lid_outer`
- [x] 1.4 `LEGACY_CLASS_RENAME`:移除 `LidOuter→RingOuter`;`lid_outer→LidOuter`;
      註解更新
- [x] 1.5 `CLASS_CATEGORY`:`LidOuter: structure`

## 2. canvas.js 鏡像

- [x] 2.1 `CLASS_COLORS`:`LidOuter: #7e57c2`(紫家族)
- [x] 2.2 `CLASS_CATEGORY` 鏡像:`LidOuter: structure`

## 3. 驗證與文件

- [x] 3.1 tests:既有 library boot 後長出 LidOuter(rank 在 Lid 後)、
      LidOuter 範本跨 reboot 不被改名、lid_outer(snake)改名成 LidOuter;
      drift-guard / 全套綠
- [x] 3.2 CHANGELOG;知會規則團隊 `lid_outer` key 語意
