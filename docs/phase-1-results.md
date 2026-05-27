# Phase 1 Results — ADK Agent against Atlassian Rovo MCP

**Date:** _TBD_
**Operator:** _TBD (e.g. stsadmin@2tdgcb.onmicrosoft.com)_
**Atlassian site:** _TBD (e.g. brokenai.atlassian.net)_
**Model:** `bedrock/us.anthropic.claude-sonnet-4-6` (Bedrock, via LiteLLM)
**Endpoint:** `https://mcp.atlassian.com/v1/mcp`
**ADK / mcp versions:** _fill from `uv pip list`_

> Template — fill in during the first live run. Goal: capture ground truth and any
> deviation from `docs/VISION.md` and the Phase-0 results.

## TL;DR
_One paragraph: did Phase 1 pass its exit criterion (second prompt after restart, no browser)?_

## Definition of Done — checklist
- [ ] `uv sync` + `adk web` boots with no errors
- [ ] First prompt → browser OAuth → agent answers using the 8 validated tools
- [ ] `~/.atlassian-mcp/{client.json, token.json}` persisted (perms: _____ — note Windows is no-op)
- [ ] Second prompt after process restart (within TTL) → Jira issues, NO browser
- [ ] Granted scopes logged on first persist and compared to Phase-0 ground truth
- [ ] This doc completed

## Measurements
| Metric | Value |
|---|---|
| Time-to-first-token (incl. both consent screens) | _____ |
| Observed access-token TTL | _____ |
| Refresh token issued? | _____ (expected: no) |
| Granted scopes (`OAuthToken.scope`) | _____ |

## Consent screens (exact text)
1. **Consent #1** (`mcp.atlassian.com`, products): _____
2. **Consent #2** (`api.atlassian.com`, scopes): _____

## Deviations from VISION / Phase-0
_List anything that differed (scopes, endpoint behaviour, schema vs runtime, indexing lag…)._

## Runtime open questions resolved (from scratchpad/05)
- [ ] Q3 `server_url` base-vs-resource discovery: _____
- [ ] Q4 auto-reauth behaviour on 1h expiry: _____
- [ ] Q5 Windows 0600 perms: _____
- [ ] Q6 Rovo indexing-lag SLO: _____
- [ ] Q7 revoked-grant behaviour: _____

## Next steps
_Phase 2 (multi-user) prerequisites, or fixes needed before declaring Phase 1 done._
