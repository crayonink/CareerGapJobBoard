# CareerGapJobBoard

Reverse job board. Candidates with employment gaps publish profiles; employers
browse and reach out. FastAPI + SQLite, deployed on Railway.

**Build steps 1, 2, 3 and 5 are done**: models and migrations, the `/submit`
form and `/admin` review queue, `/browse` with filters, `/p/{slug}` profiles,
and the landing page. **Step 4 — employer accounts and the contact-reveal
gate — is not built.**

Until it is, there is deliberately **no HTTP route that can return a
candidate's email, phone or CV**, and the only write path a stranger can reach
is the submit form. Two tests in [tests/test_api.py](tests/test_api.py)
enforce both by walking the app's own route table.

## Layout

| File | What lives there |
|---|---|
| [app/main.py](app/main.py) | ASGI entrypoint, robots policy, JSON API under `/api` |
| [app/web.py](app/web.py) | The website: landing, browse, profile, submit, admin |
| [app/templates/](app/templates/) | Jinja templates |
| [app/models.py](app/models.py) | `candidate`, `tag`, `proof_link`, `employer`, `contact_reveal` |
| [app/enums.py](app/enums.py) | Controlled vocabularies + the gap-length buckets |
| [app/schemas.py](app/schemas.py) | `BrowseFilters` and the three serialisation tiers |
| [app/search.py](app/search.py) | Filters → SQL. One WHERE builder shared by count and page |
| [app/present.py](app/present.py) | The only place a Candidate row becomes JSON |
| [app/policy.py](app/policy.py) | Work-email check, reveal quota |
| [app/derive.py](app/derive.py) | Slugs, public name, `gap_months`, nightly refresh |
| [app/gap.py](app/gap.py) | Gap arithmetic and bucketing |
| [migrations/](migrations/) | Alembic, wired to `app.db` so it can't target the wrong DB |

## Running it

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
alembic upgrade head
python -m pytest -q
```

```bash
uvicorn app.main:app --reload
```

`CGJB_DB` sets the SQLite path. Alembic reads it from `app.db`, not
`alembic.ini`, so a migration can't run against a different database than the
app.

## Pages

Server-rendered Jinja — no build step, no client-side framework, no JS beyond
two feature-detected `onclick` fallbacks. The modern platform does the work a
framework used to:

| Feature | What it buys |
|---|---|
| Speculation Rules | Hover-prefetch, and profile pages fully prerendered — an MPA that navigates instantly |
| `@view-transition` | Cross-document morph between pages; the sticky header keeps its identity |
| `animation-timeline: view()` | Scroll-driven reveals with no IntersectionObserver, and they reverse on scroll-up |
| `@property` | A conic border that actually rotates, because `--angle` is a registered type |
| `@container` | Cards drop low-value pills when *their column* is narrow, not the viewport |
| `@starting-style` + `allow-discrete` | The delete `<dialog>` animates in and out |
| `field-sizing: content` | Textareas grow with what's typed |
| `:user-invalid` | Validation styling that waits until you've left the field |
| `content-visibility` | Long result lists skip layout until near the viewport |
| `oklch` + `color-mix` | One `--hue` token drives the whole palette |

Light only, and `color-scheme: light` is pinned so form controls and scrollbars
don't flip on a machine set to dark.

### Palette

Loud and flat. Black ink on white paper, 2px ink rules everywhere, zero radius
on every token, hard offset shadows instead of blur, and buttons that press
*into* the page on `:active` rather than lifting. Headlines are 900 weight,
uppercase, `-0.055em`, up to 6rem.

| | |
|---|---|
| Paper | `#f7f7f7` |
| Cards | `#ffffff` |
| Rules & ink | `#000000` |
| Acid | `#f2de11` |

**`--accent` is black, not the acid, and that is load-bearing.** The stylesheet
uses `--accent` for link and pill *text*, so putting the acid there would mean
yellow type on white and would fail contrast across the whole site at once. The
acid lives in `--accent-soft`, which is only ever a background behind black —
where it measures 15.28:1. [tests/test_palette.py](tests/test_palette.py) pins
this, because it's exactly the tidy-up someone would make later without
realising.

That file also parses the tokens out of `style.css`, converts OKLCH → sRGB and
asserts every text pairing clears WCAG AA, plus the brief itself: true black
ink, zero radius, a 2px minimum rule, and no surviving 1px hairlines.

### Other designs

Both alternatives are tagged, so switching is one checkout:

| Tag | What it is |
|---|---|
| `design-loud` | Current. Black, hard rules, acid accent |
| `design-warm` | Sand ground, clay accent, rounded, soft shadows |

## Configuration

| Variable | Needed for | Notes |
|---|---|---|
| `CGJB_DB` | optional | Defaults to `/data/careergap.db` on Railway, `careergap.db` locally |
| `ADMIN_USER` | `/admin` | Without both, `/admin` returns 503 — no default password |
| `ADMIN_PASSWORD` | `/admin` | |

`/admin` can publish profiles and delete people's data. An unreachable page
beats a guessable one, so it refuses to run unconfigured rather than falling
back to a default.

## Deploying to Railway

`railway.json` pins the builder and start command; there's a `Procfile` too, so
it also works on anything Heroku-shaped:

```
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Migrations run on boot, so a deploy that can't migrate fails loudly instead of
serving a stale schema. Healthcheck is `/health`.

### Mount a volume, or you will lose the board

Railway's container filesystem is **ephemeral**. A SQLite file written to the
app directory is destroyed on every redeploy and every restart.

1. Add a Railway Volume mounted at `/data`
2. Leave `CGJB_DB` unset (it defaults to `/data/careergap.db` on Railway), or
   set it to another path inside the volume

`app.db.storage_is_ephemeral()` detects this by checking whether the database's
directory is its own mount point, logs a warning at startup, and reports it as
`storage_ephemeral` in `/health` — because a board running happily on a disk
the next redeploy will erase looks exactly like a healthy one until it doesn't.

Do this before you hand-seed 20–30 profiles, not after.

## `/browse` filters

| Filter | Type | Notes |
|---|---|---|
| `tags` | list of slugs | Matches ANY. Normalised at review time via `tag_alias` |
| `role_sought` | list of slugs | Filters `role_sought_slug`, not the candidate's free text |
| `city` | string | Substring match, so "Bangalore" finds "Bangalore North" |
| `open_to_remote` | bool | Omit for no filter |
| `min_years` / `max_years` | int | Inverted ranges are swapped, not emptied |
| `gap_bucket` | list | `under_1y`, `1_3y`, `3_5y`, `5y_plus`; multiple OR together |
| `max_notice_days` | int | |
| `open_to_trial` | bool | |
| `page` / `page_size` | int | Max 50 per page |

Sort is fixed: recently updated, `id` breaking ties so paging is stable. No
relevance ranking, no promoted slots — nothing to game.

`BrowseFilters` is `extra="forbid"`, so `?gap_reason=health` is a 422 rather
than a silently ignored parameter.

### Deliberately not filterable

- **`gap_reason`** — sensitive personal data under the DPDP Act, and a reason
  filter is a discrimination tool. Displayed on the profile when the candidate
  disclosed it; never a query parameter, and not indexed either.
- **`expected_ctc`, `flexibility_note`** — visible on the profile, not sorting
  axes. A cheapness filter turns voluntary disclosure into a race.

[tests/test_privacy.py](tests/test_privacy.py) enforces both, including a test
that compiles a query with every filter set at once and asserts neither column
appears in the WHERE clause.

## Privacy tiers

Three serialisers, so leaking contact details takes deleting a line rather than
forgetting to add one:

- `CandidateCard` — `/browse`. Public name only (`Rupa K.`), no contact.
- `CandidatePublic` — `/p/{slug}`. Everything except how to reach them.
  `has_resume` is public; `resume_path` never goes over the wire.
- `CandidateContact` — the reveal endpoint only, verified employer only, only
  once a `contact_reveal` row exists. Resume comes back as a gated URL, never a
  filesystem path.

Slugs carry a random suffix (`rupa-k-9f3a`). Without it a slug is a pure
function of the name, and `/p/<guess>` becomes an enumeration oracle for "is
this person job-hunting".

## Guardrails already encoded

| # | Guardrail | Where |
|---|---|---|
| 1 | Nothing goes live unreviewed | `/admin` approve is the only path to `LIVE`; `search.base_query` filters to it |
| 2 | Contact behind verified employer login | `policy.can_reveal`, `present.to_contact` |
| 3 | 20 reveals/day per employer | `policy.DAILY_REVEAL_LIMIT`, rolling 24h window |
| 4 | `noindex` on admin and employer routes | `main.robots_policy` — default noindex, opt routes in |
| 5 | Resumes not public files | `resume_path` absent from every public schema |
| 6 | One-click delete | `/admin` delete: cascades plus an explicit file unlink |

Guardrail 2 is written and tested in `policy.can_reveal` but has no route yet — that is step 4.

Re-opening a profile you already unlocked is free and doesn't burn quota —
otherwise an employer re-reading their own shortlist pays twice.

## Three additions beyond the spec

1. **`tag` / `tag_alias` / `candidate_tag`.** The filter list requires
   skills/tags but no table was specified. Aliases are resolved during review,
   and `derive.resolve_tag` returns `None` on an unknown spelling rather than
   auto-creating — that's how you avoid `react`, `reactjs` and `react-js` as
   three separate filters.
2. **`candidate.gap_months`.** Derived from the dates and stored, because the
   bucket filter needs an indexed integer range scan. `derive.nightly_refresh`
   keeps ongoing gaps honest; without it a profile reviewed in January still
   advertises an 18-month gap in June.
3. **`candidate.role_sought_slug`.** The spec has `role_sought` as free text
   normalised on review, but also filterable. Storing both keeps the
   normalisation reversible and the candidate's own words on the profile.

## Open questions

- **`/admin` has a reject action; `ProfileStatus` has no `rejected` value.**
  Currently reject sends the profile back to `draft` with a `review_note`,
  which is really "request changes". If a hard reject needs to be
  distinguishable — so the same profile doesn't cycle back through the queue
  unchanged — that's a fifth enum value and a migration.
- **No keyword search on `/browse`.** The filter list didn't include one. At a
  few hundred profiles filters are enough; a `q` box over headline, summary and
  proof-link labels is maybe thirty lines if you want it.
- **Independent recruiters and the work-email rule.** They're the demand side
  most likely to browse daily, and plenty of them use Gmail.
  `policy.is_work_email` rejects free domains, so those signups need verifying
  by hand rather than a looser check.

## Next

Step 4 is what's left: employer signup with a work-email check, magic-link
verification, and the reveal gate on top of `policy.can_reveal` — which is
already written and tested, it just has no route yet.

Then seed 20–30 profiles by hand through `/submit` and `/admin` before
showing any employer. An empty board reads as a dead board, and you only get
one first impression per recruiter.
