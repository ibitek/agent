# GitHub integration setup

SYNVIO currently authenticates one approved workspace owner. The GitHub integration
is an application feature; the Codex GitHub plugin does not automatically provide
credentials to the deployed website.

Public repository import works without a GitHub App. Private imports, repository
listing and publishing require a dedicated GitHub App installed on selected repos.
Configure these server-side Production variables through the hosting dashboard:

- SYNVIO_GITHUB_APP_ID: GitHub App ID.
- SYNVIO_GITHUB_PRIVATE_KEY: App PEM private key (Secret variable, never in source).
- SYNVIO_GITHUB_INSTALLATION_ID: the owner's approved App installation ID.
- SYNVIO_GITHUB_APP_SLUG: optional App slug for the installation link.

Minimum repository permissions: Metadata read, Contents read/write and Pull requests
read/write. No administration, secrets, Actions or workflow-write permissions are
requested by SYNVIO. The owner must select the repositories during installation.
App JWTs last under ten minutes. Source-reading installation tokens request Contents
read only; publication requests Contents and Pull requests write scoped to one repo.
Tokens are never put in source files, model context, browser VMs or client responses.

Import pins the commit and records its complete Git tree. The current editable
workspace limit is 100 UTF-8 files / 2 MiB, 256 KiB per file. Protected/hidden paths,
symlinks, submodules and binary files are retained remotely and visibly marked in
the explorer; they are not silently converted to editable text. Imports exceeding
editable limits or a truncated GitHub tree are refused before creating a project.
A full GitHub ZIP download remains available from the source panel. The workspace
ZIP contains editable files only. Large complete local clones, binary editing and
running arbitrary project stacks are future work, not supported by this release.

Publication compares the selected revision to the immutable import baseline, uses
GitHub base_tree to preserve untouched files, and creates a new synvio/* branch plus
a draft pull request. It never force-pushes, updates the default branch or merges.
A lost branch/PR response is reconciled by reading the deterministic branch/head
before the owner retries. GitHub Actions configured by the repository may run on
new branches/PRs according to the repository's own settings.

References: https://docs.github.com/en/rest/git/trees and
https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app
