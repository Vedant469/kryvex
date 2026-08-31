# Kryvex — Agent Instructions

## Project

Kryvex is a hackathon project focused on:

- Face ID / face verification
- Identity verification
- Blockchain-based verification / attestation
- A clean, demonstrable user experience

The goal is to build a working prototype for the hackathon, not an unnecessarily complex production system.

## Team

Team name: Kryvex

## Repository

GitHub:
https://github.com/Vedant469/kryvex

Primary branch:
main

## Current Status

The project is currently in the initial setup stage.

Completed:
- Python environment created
- MCP Python SDK installed
- Custom MCP server tested successfully with MCP Inspector
- Git repository initialized
- Local repository connected to GitHub
- Initial project committed and pushed to main
- Codex CLI configured
- Cursor is available as a coding agent

Not yet implemented:
- Face verification
- Liveness / anti-spoofing
- Blockchain integration
- Frontend
- Backend API
- Database/storage
- Production deployment

## Current Files

Important files currently include:

- pipeline.py
- pyproject.toml
- README.md
- src/kryvex/
- .gitignore

Inspect the repository before making architectural decisions.

## Development Rules

1. Do not rewrite the project from scratch unless explicitly requested.
2. Inspect existing files before modifying them.
3. Preserve working functionality.
4. Prefer simple, hackathon-ready implementations.
5. Avoid unnecessary dependencies.
6. Do not expose secrets, API keys, private keys, or credentials.
7. Never commit `.env` files or secrets.
8. Do not modify Git history destructively.
9. Keep changes focused and explain what was changed.
10. Run relevant tests/checks after making changes.

## Git Workflow

The main branch is:

main

Before making substantial changes:

- Check `git status`
- Inspect the current branch
- Review recent commits when necessary

After completing a meaningful feature:

- Test the feature
- Review the diff
- Commit the changes with a descriptive message

Do not force-push unless explicitly instructed.

## Agent Handoff

Multiple coding agents may work on this repository, including:

- Cursor
- OpenAI Codex
- Gemini-based tools
- Claude Code when available

Agents should assume another agent may have worked on the project previously.

Before starting work:

1. Inspect the repository.
2. Read this file.
3. Check `git status`.
4. Inspect relevant existing code.
5. Understand the current implementation before changing it.

When handing work to another agent, leave the repository in a clear working state and summarize:
- What was implemented
- What remains
- How to run/test it
- Any known issues

## Hackathon Priority

Prioritize:

1. Working end-to-end demo
2. Face verification
3. Reliable verification result
4. Blockchain proof/attestation
5. Good user experience
6. Demo reliability
7. Security basics

Do not spend excessive time building infrastructure that does not improve the final demo.

## Security

Never store or commit:

- API keys
- Private blockchain keys
- Passwords
- Authentication tokens
- Face images containing sensitive personal data unless explicitly required and handled safely

Use environment variables for secrets.

## Important

If requirements are unclear, inspect the existing project first rather than guessing.

Do not make large architectural changes without explaining the reason.