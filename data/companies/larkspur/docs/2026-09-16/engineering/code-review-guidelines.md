---
title: "Code Review Guidelines"
last_reviewed: "2026"
---

# Code Review Guidelines

## Purpose and Scope

All code changes in the Larkspur Freight engineering folder must undergo peer review before merging into the main branch. This applies to all repositories managed under the Larkspur GitHub Enterprise account, including those integrated with our primary logistics platform (vendor: RailSyncPro). The guidelines herein are mandatory for the Chicago and Rotterdam offices, while the Singapore office follows a modified process (see Section 7). The goal is to ensure code quality, maintainability, and compliance with our security standards, as outlined in the 2023 Engineering Handbook (Doc #ENG-2023-14).

## Eligibility and Reviewer Assignment

Only engineers with at least 90 days tenure at Larkspur Freight are eligible to approve code reviews. Reviewers must not be the original author of the code. For critical modules (e.g., shipment tracking, invoicing), at least two reviewers are required, one of whom must be a Senior Engineer or above. Use the CodeFlow360 tool to assign reviewers automatically based on module ownership. Exceptions require written approval from the Engineering Manager (currently: Priya Desai, Chicago office).

## Review Process and Deadlines

- All pull requests must be submitted via GitHub Enterprise and tagged with the appropriate JIRA ticket number.
- Reviewers have 2 business days to complete their review for standard changes, and 4 business days for critical modules.
- If no review is completed within the deadline, the request is escalated to the Lead Developer (Rotterdam: Jeroen van Dijk; Chicago: Marcus Lee).
- Emergency hotfixes (defined as issues impacting >10% of shipments or >$5,000 in daily revenue) may bypass standard review with CTO approval.

## Code Quality Standards

All code must adhere to the Larkspur Freight Style Guide v2.1. Key requirements include:
- 100% passing unit tests (minimum 85% code coverage, as measured by CoverSure)
- No hardcoded credentials or API keys (use Vaultify integration)
- All SQL queries must use parameterized statements
- Documentation updates required for any public API changes
- For Python code, enforce PEP8 compliance using Black formatter (configured in pre-commit hooks)
Violations must be documented in the review comments and resolved before merging.

## Merge Approval and Documentation

A pull request may only be merged after all assigned reviewers have approved and all required checks (CI/CD pipeline via BuildMatrix) have passed. Merge commits must reference the JIRA ticket and include a summary of changes (minimum 30 words). For changes affecting external vendors (e.g., FleetOpsConnect), notify the Integration Team via Slack channel #integration-alerts within 1 hour of merging. All merged pull requests are archived in the CodeReviewLog (SharePoint folder: /Engineering/CodeReviews/2024).

## Frequent Problems and Escalation Procedures

Frequent issues include missing test coverage (noted in 17% of reviews Q1 2024), inconsistent variable naming, and outdated documentation. If a reviewer identifies a security vulnerability, they must tag the Security Team (@sec-team) in the review. Unresolved disputes after 3 business days are escalated to the Director of Engineering (current: Elena Markovic). For repeated non-compliance (3+ incidents in a quarter), the engineer will be required to attend a mandatory code quality workshop (next session: July 15, 2024).

## Regional Variations (Singapore Office)

The Singapore office follows a 1-reviewer policy for non-critical modules due to team size (currently 6 engineers). Reviews must be completed within 3 business days. All other standards remain consistent with global guidelines. For vendor-specific integrations (e.g., PortLinkAsia), a local subject matter expert must be consulted before merging. Any deviations from these rules require approval from the Singapore Engineering Lead (current: Wei-Lin Tan).
