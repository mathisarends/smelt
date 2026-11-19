# Proposed SMT319 facade coverage hint

Tracked in [#169](https://github.com/mathisarends/smelt/issues/169).
SMT319 currently suggests a replacement only when one eligible package facade
exports every public name in a from-import. SMT904 already warns when explicitly
enabled SMT107, SMT108, SMT109 or SMT308 lacks its required configuration. This
proposal covers a different case visible on full checks.

## Candidate and unit of reporting

Use the effective SMT319 setting at each caller. Count distinct outside
production modules making direct runtime imports of public names from an
analyzed implementation module. Repeated imports from one caller contribute one.
Exclude initializer callers, package-internal imports, imports of a facade,
private or star imports, and directives that explicitly disable the rule for
the caller. CLI selection alone supplies no project policy.

For a mixed import, first ask whether any eligible ancestor can provide the
whole statement under SMT319's existing direct re-export rules. If so, SMT319
already supplies the actionable finding. Otherwise attribute the evidence to
the nearest enclosing analyzed package with an initializer outside the caller.
Count unresolved names and implementation modules separately. A namespace
package without an initializer supplies no file anchor.

Propose one hint on line 1 of the initializer once at least three distinct
outside callers need names the facade cannot supply. A configuration option
should let projects set the minimum number of callers. Never raise this hint to
the caller's SMT319 error severity. A reasoned package ignore can document a
deliberately empty facade. Skip a package already governed by an active SMT109
export threshold, and skip path-scoped, changed and staged reports.

## Worked example

Three modules outside `app.jobs` each import `start, stop` from
`app.jobs.worker`. The following are the expected outcomes after implementing
the proposed hint.

| Direct exports from `app.jobs` | Existing SMT319 findings | Proposed coverage hints |
| --- | ---: | ---: |
| None | 0 | 1 at `app/jobs/__init__.py` |
| Only `start` | 0 | 1 naming `stop` and three callers |
| Both `start` and `stop` | 3 | 0 |
| Both, with callers already importing from `app.jobs` | 0 | 0 |

Include the caller and implementation-module counts in the evidence. Show a
bounded sample of paths and names. The repair text should invite a deliberate
API decision. Adding a re-export can change import-time behavior.

## Implementation checks

Reuse the direct-export and import-statement facts from SMT319. Persist
file-local syntax separately from the project-wide decision so a facade edit,
caller removal, package override or SMT109 activation changes the result on the
next check. Verify complete equality of cached and uncached reports. Add an
invalidation case that re-exports the second name after warming the cache,
plus scope, threshold, suppression and SMT109 overlap cases.
