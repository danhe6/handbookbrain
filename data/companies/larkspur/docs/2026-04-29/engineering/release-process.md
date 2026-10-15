---
title: "Release Process"
last_reviewed: "2026"
---

# Release Process

## 1. Pre-Release Code Review

All code changes for the folder engineering module must undergo peer review before merging to the main branch. At least two engineers from the originating office (either St. Louis or Vancouver) must approve the pull request. Use CodeHarbor for all code reviews. For critical patches (severity 1 or 2), a senior engineer (level 4 or above) must be one of the reviewers. 
- Minimum review time: 24 hours
- All review comments must be resolved before proceeding
- Attach CodeHarbor review summary to the release ticket

## 2. Automated Testing and QA

After code review, the release candidate must pass the full suite of automated tests in TestNest. The minimum pass rate is 98% for unit tests and 95% for integration tests. QA team in the Dallas office is responsible for manual regression testing. 
- Manual test cycle: 2 business days
- All critical and high-priority bugs must be resolved before sign-off
- Attach TestNest report and QA checklist to the release ticket

## 3. Staging Deployment

Deploy the release candidate to the staging environment using DeployMate. Only DevOps engineers with Level 2 clearance or higher can perform deployments. Notify the product owner (currently Jamie Lin) and the support lead (currently Priya Desai) upon successful deployment. 
- Staging environment must mirror production (AWS us-east-2, 4 vCPUs, 16GB RAM)
- Run smoke tests within 4 hours of deployment
- Log all deployment steps in the DeployMate activity feed

## 4. Business Sign-Off

Business sign-off is required from the product owner and the regional manager of the deploying office. For St. Louis, this is currently Mark Hensley; for Vancouver, it is Sara Choi. Sign-off must be recorded in the ReleaseTracker system. 
- Attach release notes (including Jira ticket references and vendor impact)
- Confirm that all customer-facing documentation is updated
- Deadline for sign-off: 2 business days before scheduled production release

## 5. Vendor Notification

If the release impacts external integrations (e.g., with CargoSync or FleetBridge), notify the vendor at least 3 business days in advance. Use the VendorNotify portal for official communication. 
- Include release summary, expected downtime (if any), and rollback plan
- For releases with financial impact over $10,000 USD, copy the finance team (finance@larkspurfreight.com)
- Track vendor acknowledgments in the ReleaseTracker

## 6. Production Release and Post-Release Monitoring

Production releases are scheduled for Wednesdays at 10:00 UTC, unless otherwise approved by the CTO (currently Elena Voss). Deployments must be performed by a certified DevOps engineer and witnessed by a QA lead. 
- Monitor system health via PulseWatch for 48 hours post-release
- Any incidents must be logged in the IncidentHub within 30 minutes
- Prepare a post-release report within 3 business days, including metrics on error rates and user feedback

## 7. Exception Handling

If any checklist item cannot be completed, an Exception Request must be filed in ReleaseTracker and approved by both the CTO and the head of QA (currently Miguel Alvarez). Exceptions are only granted for critical business needs and must include a mitigation plan. 
- Exception approval turnaround: 1 business day
- No more than 2 exceptions per quarter per office
- All exceptions are reviewed in the quarterly compliance audit
