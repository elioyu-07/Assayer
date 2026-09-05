# Asset Import Spec — Grouping Test Case

## 1. Module Definition

This module imports assets from an uploaded file. The import service owns validation
and persistence. The document does not define what is out of scope.

## 2. Functional Requirements

### FR-001: Import assets

The system should process an import in a timely and reasonable manner.
The endpoint must return `202 Accepted` immediately after validation.
The endpoint must complete the entire import before returning `202 Accepted`.

### FR-002: Duplicate asset codes

TODO: define the behavior when an uploaded `assetCode` already exists.

AC-FR001-01: A valid upload is accepted and an import result is returned.

## 3. Data Fields

| 字段名 | 类型 | 必填 | 取值范围 | 校验规则 |
|---|---|---|---|---|
| projectCode | VARCHAR(64) | 是 |  |  |
| projectName | VARCHAR(128) | 是 |  |  |
| assetCode | VARCHAR(64) | 是 | 全局唯一 |  |
| status | VARCHAR(16) | 是 | DRAFT, ACTIVE |  |

## 4. State Model

An asset starts in `DRAFT` and may move to `ACTIVE` after import.
An asset may be deleted only while it is `ACTIVE`.

## 5. Permissions

Managers may edit assets. Other users may view assets.

## 6. Dependencies and Assumptions

The dictionary service is available. If it is unavailable, the import continues
with the submitted values.

