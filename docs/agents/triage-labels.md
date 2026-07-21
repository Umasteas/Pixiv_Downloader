# Triage Labels

This repository uses the default five-label vocabulary for issue triage.

## Label mapping

| Role | Label string |
|------|-------------|
| Needs triage | `needs-triage` |
| Needs info | `needs-info` |
| Ready for agent | `ready-for-agent` |
| Ready for human | `ready-for-human` |
| Won't fix | `wontfix` |

## Applying labels

```bash
gh issue edit <issue-number> --add-label "needs-triage"
```
