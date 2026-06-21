# viewer-ui Specification (delta)

## MODIFIED Requirements

### Requirement: Dashboard for files and libraries

The system SHALL provide a dashboard at `GET /` that lists products
and, per product, the versions and role-bound files of the selected
version (id, name, size, status, primitive count, upload time). The
dashboard SHALL offer a "New Product" action whose form requires both
a product name and a first-version label; submitting without a label
SHALL be blocked client-side and rejected server-side (422). There
SHALL be no library bar, no library creation, no upload-target library
selector, and no per-row library reassignment — libraries are version
internals not exposed in the UI.

#### Scenario: Dashboard renders without products
- **WHEN** the user opens `/` and no products exist
- **THEN** the dashboard shows an empty-state message and the New Product action

#### Scenario: New Product requires a version label
- **WHEN** the user opens the New Product form and submits a name with
  an empty version label
- **THEN** the form blocks submission and highlights the label field

### Requirement: Multi-file drop-zone upload

The product page SHALL accept `.dxf` files via either a drop-zone or
a file picker within a role slot. Each upload SHALL be tagged with
the currently selected `version_id` and the slot's role, posting to
`POST /api/versions/{vid}/files`. Upload affordances SHALL be
disabled when the selected version is signed off.

#### Scenario: Drop multiple DXFs at once
- **WHEN** the user drops three `.dxf` files onto a role slot of the
  selected (unsigned) version
- **THEN** three bindings appear with their lifecycle status
- **AND** each is bound to the selected version and role

#### Scenario: Upload zone disabled on a signed version
- **WHEN** the selected version is signed off
- **THEN** the drop-zone and picker are rendered disabled

## REMOVED Requirements

### Requirement: Library management modal
**Reason**: library 不再是使用者可見概念(隸屬 version 的內部容器)。範本管理改以「版本的範本清單」呈現——同一 modal 內容掛在版本上下文下,功能(縮圖、移類、刪除、折疊)不變,但不再有「Library」的命名與跨 product 語意。
**Migration**: modal 標題與資料來源改為當前 version 的 library;按鈕改名「Templates」。已畫押版本中刪除/移動按鈕停用。

### Requirement: Library switching in the viewer header
**Reason**: 「換 library」語意隨拓樸消失(一 version 一 library;等價操作是切換版本)。
**Migration**: 移除 header 的 library `<select>`;viewer 改顯示當前 product/version 標籤(唯讀),版本切換在 product 頁進行。

### Requirement: Dashboard products grouped into foldable customer sections
**Reason**: 「customer = library」維度隨拓樸消失(library 1:1 隸屬 version,不再承載客戶分組)。product 清單改為扁平列表;若未來需要客戶分組,應作為 product 的屬性另行設計。
**Migration**: 移除 customer section 折疊層與 `smdr2.dashboard.foldedCustomers` sessionStorage;product 卡片直接平鋪(卡片本身的外觀與行為不變)。
