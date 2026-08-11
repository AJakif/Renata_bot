# SKILL.md — Project Memory System

<!--
INSTALL: Copy to ~/.claude/skills/project-memory/SKILL.md (global)
         Or .claude/skills/project-memory/SKILL.md (project-specific)
TRIGGER: Automatic on session start, or "update project memory"
-->

---
name: project-memory
description: Persistent project memory that tracks decisions, bugs, patterns, and context across sessions
version: 1.0.0
---

## Overview

This skill maintains living documentation that persists across Claude Code sessions. Instead of re-explaining context every time, Claude learns and remembers.

## When to Use

- **Automatically**: At session start, check memory files
- **After decisions**: Record why you chose approach A over B
- **After bugs**: Document the fix for future reference
- **After patterns**: Note recurring solutions

## Memory File Structure

Maintain these files in `.claude/memory/`:

### decisions.md
```markdown
# Architecture Decisions

## YYYY-MM-DD: [Decision Title]
- **Context**: [What problem we faced]
- **Options**: [What we considered]
- **Decision**: [What we chose]
- **Rationale**: [Why]
- **Consequences**: [Trade-offs accepted]
```

### bugs.md
```markdown
# Bug Fixes & Solutions

## [Bug Title]
- **Symptom**: [What went wrong]
- **Root Cause**: [Why it happened]
- **Fix**: [How we solved it]
- **Prevention**: [How to avoid in future]
- **Files**: [Affected files]
```

### patterns.md
```markdown
# Discovered Patterns

## [Pattern Name]
- **Use When**: [Trigger condition]
- **Implementation**: [How to do it]
- **Example**: [Code snippet]
- **Gotchas**: [Watch out for]
```

### context.md
```markdown
# Project Context

## Current Focus
- [What we're working on]

## Recent Changes
- [Last few significant changes]

## Blocked/Pending
- [What's waiting on external input]

## Key Contacts
- [Who to ask about what]
```

## Session Protocol

### On Session Start
1. Read `.claude/memory/` files
2. Check `AGENTS.md` for team context
3. Review recent git commits if available
4. Ask: "What are we working on today?"

### During Session
When you encounter something worth remembering:
- Significant decision → Update `decisions.md`
- Bug fix → Update `bugs.md`
- New pattern learned → Update `patterns.md`
- Context change → Update `context.md`

### On Session End
1. Summarize what was accomplished
2. Update relevant memory files
3. Note any pending items in `context.md`

## Commands

| Command | Action |
|---------|--------|
| `update memory` | Review and update all memory files |
| `record decision [topic]` | Add to decisions.md |
| `record bug [description]` | Add to bugs.md |
| `record pattern [name]` | Add to patterns.md |
| `what do you remember about [topic]` | Search memory files |
| `session summary` | Generate summary for context.md |

## Integration with CLAUDE.md

This skill works alongside your CLAUDE.md workflow:

```markdown
# CLAUDE.md

## Project Memory
@.claude/memory/context.md
@.claude/memory/decisions.md

## Workflow Rules
[Your existing CLAUDE.md content]
```

## AGENTS.md Auto-Update

Keep `AGENTS.md` in sync for cross-platform compatibility:

```markdown
# AGENTS.md

## Project: [Name]
## Last Updated: [Date]

### Active Context
[Current focus from context.md]

### Key Decisions
[Recent decisions summary]

### Known Issues
[Active bugs/limitations]

### Patterns
[Established patterns to follow]
```

## Example Session Flow

```
User: "Let's continue working on the auth feature"

Claude: [Reads memory files]
"I see from our memory that:
- We decided on magic links over passwords (decisions.md)
- There's a known issue with token expiry on Safari (bugs.md)
- We use the Result pattern for auth operations (patterns.md)

Last session we completed the email sender. Today we need to:
- Implement token verification endpoint
- Add rate limiting

Want me to continue with token verification?"
```

## Memory Hygiene

### Weekly
- Archive old decisions (move to `archive/`)
- Clean resolved bugs
- Consolidate duplicate patterns

### Monthly
- Review and prune context.md
- Update AGENTS.md summary
- Check for stale information

## File Size Guidelines

| File | Max Size | Action if Exceeded |
|------|----------|-------------------|
| context.md | 2KB | Archive old items |
| decisions.md | 10KB | Move old to archive |
| bugs.md | 5KB | Remove resolved |
| patterns.md | 10KB | Keep only active |

## Success Criteria

Memory system is working when:
- [ ] No re-explaining project context each session
- [ ] Past decisions are referenced automatically
- [ ] Bug fixes aren't repeated
- [ ] Patterns are consistently applied
- [ ] New team members can onboard from memory files
