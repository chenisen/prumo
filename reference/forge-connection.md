# Connecting a forge, so the proposal reads itself

A review needs **one** connection, and it is not an API: `git`, reaching the remote the
repository pushes to. The diff comes from the ref the server publishes — `refs/…/head`,
per provider — and that ref is readable with the same access that clones the repository.
Nothing in this repository calls a forge API.

Everything else on this page is about the **second** ledger: the proposal's own
description, which is mandatory reading and lives behind an API. A connection is how the
*harness* gets that text. It is never how the *method* gets it.

## The invariant, before any of the routes

**The text always arrives as a file.**

```
review.py prepare … --item-file ITEM.md --proposal-file PROPOSAL.md
```

No script here reaches a tracker or a forge. The reading is **declared by whoever did
it**, by handing the file over. So a connector does not remove the `--proposal-file`
argument — it removes the *copying and pasting* that would otherwise produce it.

Two consequences worth stating, because both have been got wrong in this very repository:

- **A token in the environment does not satisfy the gate.** Setting a variable is not
  reading a description. The gate opens when the text arrives, and a gate that opened on
  a variable's presence let reviews run with the proposal's story missing from the one
  judgement that compares three stories.
- **A file that is missing, or present and empty, does not count as read.** The
  difference between *"it states no criterion"* and *"I could not read it"* is the entire
  point of the gate, and a blank file erases it.

## The three connections

`prumo.yml` declares one, under `forge`:

```yaml
forge:
  type: gitlab            # gitlab | github | bitbucket
  connection: mcp         # mcp | token | none
```

| | reads the description | reads the pipeline/checks | publishes | needed to review |
|---|---|---|---|---|
| `mcp` | yes, through the connector | yes | no — publishing stays a human act | no |
| `token` | yes, through the API or the CLI | yes | yes | no |
| `none` | no — you paste it | no | no | no |

**`none` is a valid answer, and it is the honest default.** The review runs in full: the
diff is read over git, the description is pasted into a file, and publishing is three
deliberate human acts anyway. What a connection buys is the pasting.

The configuration holds the **name** of an environment variable, never a token.
`providers.py` refuses a value that looks like a secret, and `hygiene.py` fails any
secret that reaches a commit.

## Route `mcp` — a connector the harness already carries

This is the easiest of the three, and the one to prefer: it is read-only in practice, it
needs nothing in your shell, and it survives a token rotation.

### GitHub

GitHub publishes an official MCP server. Two ways to run it:

**Hosted** — nothing to install, authenticated by OAuth in your client:

```bash
claude mcp add --transport http github https://api.githubcopilot.com/mcp/
```

**Local** — a container you run yourself, authenticated by a personal access token with
`repo` scope (read is enough for a review):

```bash
claude mcp add github -- \
  docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server
```

The toolset that matters is `pull_requests`. What a review needs from it is the PR's
body, its title and its linked item — plus, optionally, the checks.

### GitLab

GitLab ships an MCP server as part of its agent platform, served by your own instance:

```bash
claude mcp add --transport http gitlab https://YOUR-INSTANCE/api/v4/mcp
```

Two things to check before assuming this works, because both are instance-level and
neither fails loudly:

1. **the version** — the endpoint exists from recent major releases onward, so an older
   self-managed instance simply will not answer at that path;
2. **the setting** — it is disabled by default on many instances, and turning it on is an
   administrator's act, at the instance or group level.

If either is no, a community MCP server for GitLab works against self-managed instances
with a personal access token (`read_api` is enough for a review). Whichever you use, the
test is the same one below.

### Then declare it

```yaml
forge:
  connection: mcp
```

This changes what the prumo *says*, not what it does: the preflight stops printing a
token remedy to someone who has a connector, and the "no token" warning narrows to the
truth — the connector reads, and only publishing stays manual.

## Route `token` — a variable in your environment

For a forge with no connector you trust, or when you also want to publish from a script.

```bash
export GITLAB_TOKEN=…      # or GLAB_TOKEN
export GITHUB_TOKEN=…      # or GH_TOKEN
```

The names come from the provider preset and can be overridden in `prumo.yml`
(`forge.tokens`). An authenticated provider CLI counts as this route too — `setup.py`
measures `auth status` and will say so.

Remember the invariant: the token lets the harness *fetch* the description. The fetched
text still gets handed over in `--proposal-file`.

Scope it for **reading**. A review never needs write access, and the one act that would —
publishing — is deliberately left as a human act with its own confirmation.

## Route `none` — paste it

Always available, and not a failure state.

1. Open the proposal in a browser.
2. Copy its description into a file.
3. Pass that file in `--proposal-file`.

If the proposal genuinely has **no** description, say exactly that in the file. An empty
file is refused on purpose: it cannot be told apart from a failed reading.

## Checking what this installation understood

```bash
python3 scripts/providers.py
```

```
  forge       gitlab · MR !123 · refs/merge-requests/123/head
              connection: mcp · no token in the environment (GITLAB_TOKEN, GLAB_TOKEN)
```

The declared connection and the measured environment print on the same line, because the
interesting case is the two disagreeing: `connection: token` with nothing in the shell is
a configuration that has drifted away from the machine it runs on.

To re-answer the question interactively:

```bash
python3 scripts/setup.py --at <your-workspace> --force
```

or, without being asked anything:

```bash
python3 scripts/setup.py --forge github --forge-connection mcp
```

## The tracker is the same problem, one ledger earlier

The item — ledger 1 — is read the same way, and `--item-file` is its `--proposal-file`.
The connectors differ by tracker, and for a tracker with no published connector the paste
route is the answer. `setup.py` names what reaches yours when it writes the file.

The item gate applies to **every** origin. The description gate applies only when the
origin is a proposal: a loose branch has no description, and demanding one there would be
demanding something that does not exist.
