# Running in GitLab CI

One job serves every repository. What changes is the profile it resolves.

```yaml
# .gitlab-ci.yml of the target repository
delivery-review:
  stage: test
  rules:
    # only a merge request out of draft, or one explicitly labelled
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event" && $CI_MERGE_REQUEST_DRAFT != "true"'
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event" && $CI_MERGE_REQUEST_LABELS =~ /review:requested/'
  variables:
    GIT_DEPTH: 0                      # the base has to resolve; a shallow clone will not do
  script:
    - claude -p "/task-review --mr $CI_MERGE_REQUEST_IID --repo $CI_PROJECT_NAME --ci"
      --allowedTools "Workflow(task-review)"
  artifacts:
    when: always
    paths: [reports/]                 # the reports root declared in prumo.yml
    expire_in: 30 days
```

## Gating the merge request

| Mechanism | Tier | Behaviour |
|---|---|---|
| pipeline job + "Pipelines must succeed" | any | a red job holds the merge. **This is the recommended path.** |
| external status check | Ultimate | only blocks with "Status checks must succeed"; fails if it stays pending for more than two minutes; refuses with 409 any response that does not refer to the current HEAD |

## In CI, the item is not guessed

Interactively, a person confirms the derived item. In CI there is nobody: the item comes
from the proposal's template, **or the run stops**. Deriving it and carrying on in silence
would produce a review against the wrong item.

## Bot account

No merge permission on protected branches. The hook's guarantee is the first line; the
server's permission is the one that holds if the hook fails.

Token through a masked environment variable, minimum scope (`read_api`,
`read_repository`, and `api` only if publishing is automatic). **Never in a versioned
file.**
