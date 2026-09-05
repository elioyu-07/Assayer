# Product Spec: Registration Service

## 1. Module Definition
The registration service creates and manages user accounts for the platform.

## 2. State Model
An account is draft until email verification, then becomes active. A closed
account cannot be reopened.

## 3. Functional Requirements
FR-001: The service must create an account for a unique email address.
FR-002: The service must reject an already registered email address.
AC-FR001-01: Given a valid email, when the account is created, then a
verification email is sent and the account is in the draft state.
CASE-01: Given a duplicate email, when registration runs, then the request is
rejected with a duplicate-email error.

## 4. Key Entities
The Account entity owns the email address, verification status, and lifecycle
state.

## 5. Data Fields
The email field is a required string with a maximum length of 254 characters
and must match a standard email format.

## 6. Non-functional Requirements
NFR-GEN-001: Every create operation must complete within 2000 milliseconds at
p95.

## 7. Success Criteria
SC-01: Every accepted email produces exactly one account.

## 8. References and Compliance
The platform governance document is the selected authority.

## 9. Key Decisions
D-01: Email is the unique account identity.

## 10. Dependencies and Assumptions
The email delivery provider must be available; otherwise the operation fails
explicitly with a retryable error.

## 11. Stage Differences
The first stage includes FR-001 and FR-002.

## 12. Revision History
Version 1.0 was created for the initial review.
