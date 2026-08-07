# Agent Instructions

Rules for agents working on this project.

## Documentation sync

### Keep RULES.md in sync with the recommendation

`RULES.md` documents how raw indicators are turned into the traffic-light
recommendation and is the single source of truth for scoring rules.

Whenever a **new piece of data is fetched** (new API field, new endpoint,
new analyzer input) and it is **used for the recommendation or shown in
the report**, update `RULES.md` in the same change:

- Add or amend the **decision tree** entry (condition → level → message).
- List any new **thresholds** in the thresholds table.
- Mention new **objective facts** in the reasoning section.
- Add new i18n keys used by the recommendation to the message catalog
  table (`rec_*`, `fact_*`, `owner_type_*`, …).

Keep `SPECS.md` in sync too when it describes the same rule (it mirrors
the recommendation decision tree).

A code change that alters the verdict without updating `RULES.md` is
incomplete and must not be committed as-is.

### Keep TODO.md in sync

`TODO.md` tracks remaining work (coverage gaps, code smells, housekeeping)
and must stay current with the codebase.

Whenever a change completes or invalidates a TODO item, update `TODO.md`
in the same change:

- Tick `[x]` or drop items that are done; reword items whose scope
  shifted.
- Refresh drifting numbers (coverage %, test counts) and bump the
  "Last updated" date in the header.
- Add follow-ups discovered along the way.

A commit that resolves a TODO item without updating `TODO.md` is
incomplete and must not be committed as-is.
