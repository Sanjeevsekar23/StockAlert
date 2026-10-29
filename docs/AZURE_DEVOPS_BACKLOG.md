# Azure DevOps Boards starter backlog

This file is a proposed starter backlog for import or manual entry in Azure Boards. No Azure DevOps organization, project, board, work items, or sprint has been created or verified.

| Type | Title | Description / acceptance criteria | Priority |
|---|---|---|---|
| Epic | Reliable inventory operations | Track product, stock movement, expiry, staff and attendance improvements under one parent. | 1 |
| User Story | Maintain a verified backup and restore process | Document and rehearse a backup/restore procedure for the production database; verify restored products, users, batches, stock movements, and attendance. | 1 |
| User Story | Add CSRF protection to state-changing forms | Add CSRF tokens and verify unauthenticated and cross-site submissions cannot change data. | 1 |
| User Story | Improve deployment configuration documentation | Document required production settings, persistent storage, secret rotation, and deployment rollback steps. | 2 |
| Task | Connect Azure Repos and enable the pipeline | Import or connect the approved GitHub repository; run the pipeline on pull requests and main branch updates. | 1 |
| Task | Define release and database migration checks | Establish a release checklist and a safe, reversible process for schema changes. | 2 |
| Bug | Review legacy application snapshots | Decide whether app_old.py, app_broken.py, and app_backup.py should remain in the maintained codebase; verify active deployment uses app.py. | 3 |

Suggested initial iteration: leave unset until a team, dates, and capacity are agreed. Suggested states and assignments should be set in the Azure DevOps project by its owner.
