---
name: atlassian-mcp-tool-usage
description: |
  How to use the Atlassian Rovo MCP Server tools efficiently from an LLM agent.
  Use this whenever you are answering a user's question about Jira issues,
  Confluence pages, sprints, epics, comments, or any Atlassian content. Covers
  decision rules for tool selection (search vs JQL vs CQL vs direct fetch),
  workflow patterns for read and write, JQL and CQL cheatsheets, and pitfalls
  around cloudId, content formats, and OAuth scopes.
---

# Atlassian Rovo MCP — Tool Usage Skill

This skill encodes how to use the 30 tools exposed by `https://mcp.atlassian.com/v1/mcp` correctly and efficiently. Follow the decision rules in order; they're written to minimize total tool calls.

## Decision rules (apply in order)

1. **Is the user's question natural language about "what" exists?** → use `search` (Rovo unified Jira+Confluence). **Always pass `cloudId`** even though the schema says it's optional — runtime requires it. **Caveat:** Rovo Search has an indexing lag. Content created or updated in the last hour may not be in the index yet. If the user references recent content and `search` returns empty or partial results, fall back to JQL/CQL.
2. **Did `search` return a hit and the user wants details?** → use `fetch` with the ARI from the result. Pass `cloudId` here too.
3. **Does the user explicitly mention JQL, a project key, or a specific Jira field?** → use `searchJiraIssuesUsingJql`.
4. **Does the user mention CQL, a Confluence space key, or a specific Confluence field?** → use `searchConfluenceUsingCql`.
5. **Does the user reference a specific Jira issue key (e.g. KAN-1)?** → use `getJiraIssue` directly. Don't search first.
6. **Does the user reference a specific Confluence page ID or tiny link?** → use `getConfluencePage` directly.
7. **Is the user asking about themselves ("my issues", "what's on my plate")?** → use `searchJiraIssuesUsingJql` with `assignee = currentUser()`. Don't call `atlassianUserInfo` unless you need their accountId for an explicit reason.
8. **Are you about to mutate (create/edit/comment/transition)?** → read first to confirm, then write. Never mutate on a vague reference.

## When to call `getAccessibleAtlassianResources`

Only when:
- A tool that needs `cloudId` returns an error saying it's missing or invalid.
- The user has multiple Atlassian sites and you need to disambiguate.

Skip it otherwise. If you have one site and the user's question is about that site, just pass the site hostname (e.g. `brokenai.atlassian.net`) as `cloudId` to tools — the MCP server resolves it. If that fails, fall back to `getAccessibleAtlassianResources`.

## Workflow patterns

### Read patterns

**Pattern A — Natural language question (preferred):**
```
search(query) → fetch(ari)  [if details needed]
```

**Pattern B — Known Jira issue:**
```
getJiraIssue(cloudId, issueIdOrKey)
```

**Pattern C — Filtered Jira list:**
```
searchJiraIssuesUsingJql(cloudId, jql) → getJiraIssue(...)  [if details needed beyond the search fields]
```

**Pattern D — Confluence content discovery:**
```
search(query)  [preferred]
OR
searchConfluenceUsingCql(cloudId, cql) → getConfluencePage(...)
```

### Write patterns

**Add a comment:**
```
getJiraIssue (confirm issue exists and you have the right one)
→ addCommentToJiraIssue
```

**Change issue status:**
```
getJiraIssue (confirm)
→ getTransitionsForJiraIssue (returns available transition IDs for current status)
→ transitionJiraIssue (with the chosen transition.id)
```

**Edit a Jira field:**
```
getJiraIssue (confirm current state)
→ editJiraIssue (with fields object)
```

**Link two issues:**
```
getIssueLinkTypes  [if you don't know the link type name]
→ createIssueLink (inwardIssue + outwardIssue + type)
```
> Directional gotcha: for "Blocks", `inwardIssue` is the blocker, `outwardIssue` is the blocked. "A is blocked by B" → inwardIssue=B, outwardIssue=A.

**Create a new Confluence page:**
```
getConfluenceSpaces  [if you don't know spaceId]
→ createConfluencePage (cloudId, spaceId, title, body, contentFormat=html)
```

## JQL cheatsheet

JQL = Jira Query Language. Used with `searchJiraIssuesUsingJql`.

| Goal | JQL |
|---|---|
| Open issues assigned to me | `assignee = currentUser() AND statusCategory != Done` |
| In current sprint | `sprint in openSprints()` |
| In a specific project | `project = KAN` |
| Bugs only | `issuetype = Bug` |
| Updated this week | `updated >= -7d` |
| By status | `status = "In Progress"` |
| Combined | `project = KAN AND assignee = currentUser() AND status != Done ORDER BY priority DESC` |
| Specific issue keys | `key in (KAN-1, KAN-2)` |
| All epics in project | `project = KAN AND issuetype = Epic` |
| Children of an epic | `parent = KAN-1` (or `"Epic Link" = KAN-1` for old-style projects) |

Quoting rules: use double quotes for multi-word values (`status = "In Progress"`). Don't quote single words.

## CQL cheatsheet

CQL = Confluence Query Language. Used with `searchConfluenceUsingCql`. **CQL is NOT JQL** — different syntax.

| Goal | CQL |
|---|---|
| Pages in a space | `space = BROKENAI AND type = page` |
| By title (fuzzy) | `title ~ "onboarding"` |
| By exact title | `title = "Onboarding Runbook"` |
| Modified recently | `lastmodified >= now("-2w")` |
| My pages | `creator = currentUser() AND type = page` |
| Text search | `text ~ "OAuth"` |
| Combined | `space = BROKENAI AND text ~ "OAuth" AND type = page ORDER BY lastmodified DESC` |
| Across all content | `text ~ "MCP" AND (type = page OR type = blogpost)` |

Important: use the **space key** (e.g. `BROKENAI`), not the space name, for `space = ...`. Use `space.title ~ "name"` for name searches.

## Content formats

Several tools accept a `contentFormat` parameter for body content:

- **`markdown`** — use when you're reading content to summarize or display. Easiest for the LLM to reason about.
- **`html`** — use when you're writing/updating Confluence pages with rich elements (panels, tables, task lists, layouts). Round-trip safe.
- **`adf`** — Atlassian Document Format JSON. Use only when you need exact programmatic fidelity, or the tool defaults to it.

Default rule: **read in `markdown`, write in `html`**.

## Output formatting for the user

- **Issue references:** always include the key (e.g. `KAN-1`) so the user can click through.
- **Confluence references:** include the page title and link if available in the response.
- **Lists of issues:** show key, summary, status, assignee in a compact form. Skip noisy fields like `created` timestamps unless the user asked.
- **Don't dump raw ADF or HTML** at the user. Summarize.
- **Cite the source tool** in your reasoning (internal) but not in the final answer.

## Pitfalls and gotchas

1. **No `offline_access` scope is granted.** No refresh token. When the access token expires (~1 hour), the next call fails with 401 and you'll need to re-authenticate. Surface this clearly when it happens — don't loop retrying.
2. **`write:sprint:jira-software` is NOT granted.** If you try to `transitionJiraIssue` on a Scrum project where the transition screen includes the Sprint field, it will fail with a permission error referencing the Sprint field. There's no client-side workaround — the user has to do the transition in the Jira UI, or you have to remove the Sprint field from the transition screen.
3. **JSM tools are not available via OAuth** (only via API token). If the user asks about service desk tickets, tell them this MCP connection doesn't expose JSM.
4. **`createConfluenceInlineComment` requires fetching the page first** to count text occurrences. Don't skip the read step.
5. **Pagination:** `searchJiraIssuesUsingJql` defaults to 10 results. Set `maxResults` higher (up to 100) when the user asks for "all" something. `searchConfluenceUsingCql` uses cursor-based pagination — use the `cursor` field from the response to get the next page.
6. **Empty results are not errors.** If `searchJiraIssuesUsingJql` returns `[]`, the JQL was valid but matched nothing. Tell the user that.
7. **Validate before mutating.** If the user says "close KAN-1", first `getJiraIssue` to confirm it exists and what its current status is. Then `getTransitionsForJiraIssue` to see valid transitions. Then transition. Three calls is fine; a wrong mutation is not.
8. **Don't fabricate cloudId.** If you genuinely don't have one, call `getAccessibleAtlassianResources`. Don't make up a UUID.
9. **Tool schemas occasionally lie about parameter requirements.** The `search` and `fetch` tools' schemas claim `cloudId` is "not needed — derived from token" but the runtime returns errors without it. **Always pass `cloudId` to every tool.** Treat schema descriptions as aspirational; trust empirical behaviour.
10. **Rovo Search indexing lag is real.** Verified: a Confluence page <1 hour old was missed by `search` but found instantly via `searchConfluenceUsingCql`. For freshly-created content, default to direct JQL/CQL. Use `search` for established content where ranking and cross-product results matter more than freshness.
11. **CQL quoting.** In `searchConfluenceUsingCql`, multi-word text values need double quotes (`text ~ "OAuth flow"`). Single-word values can be unquoted (`space = BROKENAI`, `type = page`). When constructing the CQL JSON value programmatically, escape inner quotes with `\"`. When typing CQL into a UI form, use literal `"` — don't pre-escape.

## Tool inventory (30 tools)

### Identity & discovery (4)
- `atlassianUserInfo` — current user (name, email, accountId)
- `getAccessibleAtlassianResources` — list sites + cloudIds
- `getVisibleJiraProjects` — list Jira projects user can see
- `lookupJiraAccountId` — find Jira user by name/email

### Rovo unified search (2) — preferred entry points (with caveats)
- `search` — natural language across Jira+Confluence, returns ARIs. **Pass cloudId despite the schema saying otherwise. May miss content created in the last hour due to indexing lag.**
- `fetch` — get full content by ARI (`ari:cloud:jira:<cloudId>:issue/...`). **Pass cloudId.**

### Jira read (7)
- `getJiraIssue` — full issue detail
- `searchJiraIssuesUsingJql` — JQL search
- `getJiraIssueRemoteIssueLinks` — external links on an issue
- `getTransitionsForJiraIssue` — what status changes are possible
- `getJiraProjectIssueTypesMetadata` — issue types in a project
- `getJiraIssueTypeMetaWithFields` — required/optional fields for an issue type
- `getIssueLinkTypes` — types of issue links (Blocks, Duplicate, etc.)

### Jira write (6)
- `createJiraIssue` — new issue
- `editJiraIssue` — update fields
- `addCommentToJiraIssue` — comment
- `transitionJiraIssue` — change status
- `addWorklogToJiraIssue` — log time
- `createIssueLink` — link two issues

### Confluence read (8)
- `getConfluenceSpaces` — list spaces
- `getPagesInConfluenceSpace` — list pages in a space
- `getConfluencePage` — page detail
- `searchConfluenceUsingCql` — CQL search
- `getConfluencePageFooterComments` — page comments
- `getConfluencePageInlineComments` — inline (anchored) comments
- `getConfluenceCommentChildren` — reply threads
- `getConfluencePageDescendants` — child pages

### Confluence write (4)
- `createConfluencePage` — new page or blog
- `updateConfluencePage` — edit page
- `createConfluenceFooterComment` — comment on page
- `createConfluenceInlineComment` — comment anchored to specific text

## Quick examples

**User: "What's on my plate?"**
```
searchJiraIssuesUsingJql(
  cloudId="<site>",
  jql="assignee = currentUser() AND statusCategory != Done ORDER BY priority DESC",
  fields=["summary", "status", "priority"]
)
```

**User: "Show me KAN-1"**
```
getJiraIssue(cloudId="<site>", issueIdOrKey="KAN-1")
```

**User: "Anything about OAuth in our docs?"**
```
search(query="OAuth")
→ if user wants details on a result, fetch(id="<ari from search>")
```

**User: "Close KAN-1"**
```
getJiraIssue(cloudId="<site>", issueIdOrKey="KAN-1")
→ getTransitionsForJiraIssue(cloudId="<site>", issueIdOrKey="KAN-1")
→ transitionJiraIssue(cloudId="<site>", issueIdOrKey="KAN-1", transition={"id": "<Done transition id>"})
```

**User: "What's in the BROKENAI space?"**
```
searchConfluenceUsingCql(
  cloudId="<site>",
  cql="space = BROKENAI AND type = page ORDER BY lastmodified DESC",
  limit=25
)
```
