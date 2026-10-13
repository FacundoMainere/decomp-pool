#!/bin/sh
# Apply the GitHub settings a decomp-pool repository needs. Safe to run again.
#
#   tools/configure_repo.sh OWNER/REPO [COLLABORATOR...]
#
# - Adds the decomp-pool topic (discovery).
# - Private repos only:
#   - runs workflows on fork PRs, with write tokens, no secrets and no approval. Write tokens
#     are needed by pool.yml's merge job; every workflow caps its token with permissions:, and
#     the only one that runs PR code (test.yml) is read-only.
#   - gives each COLLABORATOR read access: on a private repo only collaborators can fork, so
#     only they can contribute. Read access cannot push; everything still goes through PRs.
set -e
[ -n "$1" ] || { sed -n 2,13p "$0"; exit 2; }
R=$1
shift
gh repo edit "$R" --add-topic decomp-pool
if [ "$(gh repo view "$R" --json isPrivate -q .isPrivate)" = true ]; then
    gh api -X PUT "repos/$R/actions/permissions/fork-pr-workflows-private-repos" \
        -F run_workflows_from_fork_pull_requests=true -F send_write_tokens_to_workflows=true \
        -F send_secrets_and_variables=false -F require_approval_for_fork_pr_workflows=false
    for u in "$@"; do
        gh api -X PUT "repos/$R/collaborators/$u" -f permission=pull > /dev/null
        echo "invited $u (read access; they must accept the invitation)"
    done
fi
echo "configured $R"
