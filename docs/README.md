# Documentation

Every page describes the code as it is on `main`. Commands are written for the
Docker deployment and are run from the checkout directory on the server.

## Pages

| # | Page | Answers |
|---|---|---|
| 1 | [overview.md](overview.md) | What the tool does, the end-to-end workflow, the vocabulary |
| 2 | [architecture.md](architecture.md) | How it is built; what runs where (container, host, browser, credentials, data) |
| 3 | [deployment.md](deployment.md) | Installing it with Docker Compose |
| 4 | [configuration.md](configuration.md) | Every environment variable and configuration file |
| 5 | [authentication.md](authentication.md) | The SSH key (git) and the GitHub token (pull requests) |
| 6 | [upstream-resolution.md](upstream-resolution.md) | How the upstream repository and ref are chosen and proven |
| 7 | [git-comparison.md](git-comparison.md) | Missing, present, backported, unknown and ARCoS-specific commits |
| 8 | [criticality-and-security.md](criticality-and-security.md) | When a commit is called critical, and the evidence sources |
| 9 | [patch-workflow.md](patch-workflow.md) | Previewing and applying upstream patches safely |
| 10 | [upstream-md.md](upstream-md.md) | Generating `debian/upstream.md` and the PR plan |
| 11 | [upstream-md-publishing.md](upstream-md-publishing.md) | Proposing `debian/upstream.md` as pull requests |
| 12 | [cli.md](cli.md) | Every `apm` command and option |
| 13 | [api.md](api.md) | Every HTTP endpoint |
| 14 | [testing.md](testing.md) | Running the tests and the output checks |
| 15 | [troubleshooting.md](troubleshooting.md) | Symptoms, causes, fixes |
| 16 | [operations.md](operations.md) | Start, stop, update, rebuild, back up, clean up |
| 17 | [production-handoff.md](production-handoff.md) | Current production state, the first `mstpd` PR, all eligible packages, **what happens next** |

`internship-project-document.md` and `internship-design-and-milestones.md` are
the original design and planning documents. They explain why the tool is built
the way it is; for how to run it, use the pages above.

## Reading paths

- **Operating the server:** deployment → operations → authentication →
  troubleshooting.
- **Creating the PRs:** production-handoff (it links to everything it needs).
- **Reviewing a result:** overview → upstream-resolution → git-comparison →
  criticality-and-security.
- **Changing the code:** architecture → api → testing.

## Conventions

- `docker compose exec app apm ...` runs the CLI inside the running container,
  against the same data the dashboard uses. Without Docker (development), use
  `scripts/apm ...` instead.
- `<server-ip>` is the address of the machine running the container.
- Output paths are given as they appear on the host (`out/...`); inside the
  container the same files are under `/app/out/...`.
