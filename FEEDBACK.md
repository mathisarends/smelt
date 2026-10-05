# Feedback zu smelt

Gemessen am Ziel: ein Architektur-Linter, mit dem sich Coding Agents selbst prüfen, und zwar
für **Ordnerstruktur/Hierarchie** und **gespiegelte Testdateien**. Nicht jede Datei braucht
einen Test. Aber jeder Test, der existiert, muss an der gespiegelten Stelle liegen.

Kurzfassung: Import-Grenzen und die Qualität von Tests (Patching, Mocks, private Zugriffe)
deckt smelt schon gut ab. Die Spiegelung der Teststruktur ist aber lückenhaft, und ein Agent
kann die Prüfung zu leicht umgehen. Alle Punkte mit „verifiziert“ habe ich in einem
Scratch-Projekt nachgestellt.

---

## P0: Blockiert das Ziel

### ~~1. Die Spec formuliert die Spiegelung nur als Option~~
**Entfallen:** `SPEC.md` ist gelöscht und bleibt es. Die strenge Mirror-Regel steht jetzt in
der README und in der Regeldoku von SMT401 (`docs/rules/SMT401.md`).

Dass nicht jede Datei einen Test braucht, passt zur Spec (§1 Non-goals). Zwei Stellen
sollten trotzdem geschärft werden:
- §5 SMT4xx: *"It never demands 1:1 mirroring."* Gemeint ist „nicht jede Datei braucht einen
  Test“. Man kann es aber als „die Struktur muss nicht gespiegelt sein“ lesen. Besser:
  „Mit `layout: mirror` muss jeder vorhandene Test am Spiegelpfad liegen. Tests für jede
  Datei sind nicht gefordert.“
- §13 schiebt *"required layers per feature, nesting rules"* auf später. Für das Ziel
  „Ordnerhierarchie“ gehört das nach vorn (siehe #5).

### 2. Verwaiste Testdateien werden nicht erkannt (verifiziert)
Die Umkehrung der Spiegelung: `tests/billing/test_invoice.py` existiert, aber
`app/billing/invoice.py` nicht (mehr), etwa nach einem Rename oder Move durch den Agent.
Heute bleibt das still, und der Test hängt an einer Stelle, die es in der Quellstruktur
nicht gibt. Vorschlag: `SMT409 orphaned-test [error]` bei `layout: mirror`, wenn der
Spiegelpfad auf kein existierendes Quellmodul bzw. Quellpaket zeigt. Das ist im Kern der
pfadbasierte Check aus #3. Man kann ihn als eigene Regel oder als Teil von SMT401 umsetzen.

### 3. `layout: mirror` schweigt, sobald es unsicher ist (verifiziert)
`MisplacedTestFile._mirror_path` (`smelt/rules/testing/location.py:99`) gibt `None` zurück,
wenn der Dateiname nicht **exakt** zu genau einem importierten Modul passt. `None` bedeutet
„kein Befund“. Im Scratch-Projekt:

| Testdatei | Ergebnis |
| --- | --- |
| `tests/somewhere/deep/test_invoice.py` (importiert `app.billing.invoice`) | SMT401 ✓ |
| `tests/somewhere/test_payments.py` (importiert `app.billing.payment`, Plural) | **still** |
| `tests/billing/test_ghost.py` (importiert nichts, kein Modul `ghost`) | **still** |

In smelt selbst: `tests/cli/test_fix.py` testet `smelt/cli/commands/fix.py`, importiert aber
nur `smelt.cli`. Mit `layout: mirror` bleibt das still, obwohl der Spiegelpfad
`tests/cli/commands/test_fix.py` wäre.

Der Ansatz ist verkehrt herum: Die Regel leitet vom Import auf den Ort ab und meldet nur,
wenn sie sich sicher ist. Ist sie unsicher, schweigt sie. Für einen Strukturlinter muss es
umgekehrt sein.

**Vorschlag: Der Pfad ist die Wahrheit, nicht die Imports.** Bei `mirror` wird jede
Testdatei rein anhand ihres Pfads geprüft:

```
tests/billing/test_invoice.py  →  existiert app/billing/invoice.py?
```

- **Ja:** in Ordnung, egal was der Test importiert.
- **Nein:** Fehler („kein Quellmodul `app/billing/invoice.py` für diese Testdatei“). Zeigen
  die Imports eindeutig auf ein Modul, kommt der richtige Zielpfad als Hinweis und
  `smelt fix` dazu. Die Imports helfen also nur noch beim Vorschlag. Sie entscheiden nicht
  mehr, ob überhaupt geprüft wird.

Damit fallen `test_payments.py` (kein `payments.py`), `test_ghost.py` (kein `ghost.py`) und
`tests/cli/test_fix.py` (kein `smelt/cli/fix.py`) auf. Verwaiste Tests (#2) werden mit
demselben Check miterkannt.

Für Tests, die bewusst nicht gespiegelt sind (Integration, E2E, Szenarien), gibt es eine
Ausnahmeliste: `tests.unmirrored: ["tests/integration/**", "tests/e2e/**"]`.

**Entschieden:**
- **Standard: genau eine Testdatei pro Modul.** `app/billing/invoice.py` →
  `tests/billing/test_invoice.py`. Weitere Dateien wie `test_invoice_rounding.py` sind nur
  per Option erlaubt, z. B. `tests.mirror_suffixes: true` (Muster
  `test_<modul>_<irgendwas>.py`).
- **Tests für ein ganzes Paket** heißen nach dem Paket: `app/billing/` →
  `tests/billing/test_billing.py`. Der Pfad-Check akzeptiert also neben
  `test_<modul>.py` auch `test_<paketname>.py` im Paketordner.

**Umgesetzt (#2 und #3 zusammen, in SMT401, keine eigene Regel SMT409):**
- Bei `layout: mirror` entscheidet nur der Pfad. `tests/<dirs>/test_<x>.py` (Schreibweise
  seit #7 über `tests.mirror` einstellbar) ist in Ordnung, wenn `<root>/<dirs>/<x>.py` existiert oder `<x>` das Paket
  `<root>/<dirs>/` selbst ist (`tests/billing/test_billing.py`, `tests/test_app.py`).
  Ein Paket zählt nicht als Modul: `tests/billing/test_stripe.py` für `app/billing/stripe/`
  ist ein Fehler, richtig ist `tests/billing/stripe/test_stripe.py`.
- Passt der Pfad nicht und zeigen die Imports eindeutig auf ein Modul gleichen Namens:
  „test_invoice.py belongs in tests/billing/“ mit `expected.path`.
- Sonst ist der Test verwaist: „test_ghost.py mirrors no source module:
  app/billing/ghost.py does not exist“.
- Neue Optionen: `tests.unmirrored` (Globs, Standard leer) und `tests.mirror_suffixes`
  (Standard `false`). `conftest.py` und Hilfsdateien ohne `test_`-Präfix werden nicht geprüft.
- `smelt fix` im Vorschlag oben ist durch #17 hinfällig. Die Meldung reicht dem Agent.

### 4. SMT901 übersieht pauschale Suppressions (Bug, verifiziert)
`_unused_codes` (`smelt/engine/check.py:320`) meldet ein unbenutztes `# smelt: ignore`
**ohne Codes** nur, wenn *alle* bekannten Regeln gelaufen sind. SMT206 ist standardmäßig aus
und SMT407 läuft nur mit `--changed`, deshalb gilt `checked != known` in der
Standardkonfiguration immer. Tote pauschale Ignores bleiben so für immer liegen. Fix: Nicht
aktive Regeln dürfen die Prüfung nicht blockieren, weil sie ohnehin nichts unterdrücken können.

**Umgesetzt:** SMT901 vergleicht jetzt mit den Regeln, die ein voller Lauf mit dieser Config
ausführen würde, nicht mit allen bekannten Regeln. Ein totes `# smelt: ignore -- …` wird
damit gemeldet. Ebenso ein `ignore[SMT206]`, solange SMT206 aus ist, denn eine abgeschaltete
Regel braucht keine Suppression. Ein eingegrenzter Lauf (`--select`/`--ignore`) meldet
pauschale Suppressions weiterhin nicht, weil er das nicht beurteilen kann.

**Entschieden: keine Schutzmechanismen gegen den Agent.** Ein Agent *kann* smelt umgehen:
Config lockern (`layout: none`, `rules: {X: off}`), `# smelt: ignore-file` setzen oder die
Baseline neu schreiben. Das ist gewollt, vergleichbar mit `git commit --no-verify`. Solche
Änderungen sind im Diff sichtbar, und der Diff wird ohnehin gereviewt. smelt soll
informieren, nicht bevormunden. Config-Wächter, nicht unterdrückbare Regeln und
Baseline-Sperren sind deshalb bewusst nicht vorgesehen.

---

## P1: Ordnerstruktur und Hierarchie

### 5. Lose Module direkt im Feature
SMT301 meldet nur *unbekannte Pakete* in einem Feature. Ein **loses Modul** direkt im
Feature (`features/voice/helpers.py` neben `domain/`) landet dagegen nur als unklassifiziert
bei SMT305 [hint]. Hints sieht der Agent standardmäßig nicht (eingeklappt ohne
`--show-hints`), und das Modul ist damit von allen Layer-Regeln ausgenommen.

Vorschlag: Ein Modul, das innerhalb eines Features, aber außerhalb jedes Layers liegt, ist
ein Fehler. Entweder erweitert man SMT301 („voice enthält Modul helpers.py, das in keinem
Layer liegt“) oder man führt eine eigene Regel ein. SMT305 bleibt ein Hint für Module
außerhalb von Features. `__init__.py` des Features ist erlaubt.

**Entschieden:**
- **Keine Pflicht-Layer.** Nicht jedes Feature braucht jeden Layer (etwa ein Feature ohne
  `domain/`). Das ist in Ordnung und wird nicht gemeldet.
- **Maximale Verschachtelungstiefe** (`structure.max_depth`) höchstens als optionaler Hint.
- **Keine Whitelist für Dateinamen pro Layer.** Zu streng, passt nicht zu „informieren statt
  bevormunden“.

**Umgesetzt:** SMT301 meldet jetzt auch Module direkt im Feature (oder in einem
Zwischenpaket eines gepunkteten Layer-Pfads wie `infra/` bei `infra.adapters`), die in keinem
Layer liegen: „voice contains module helpers.py, which is in no layer (domain, …)“.
Ausnahmen: das `__init__.py` des Features sowie Module, die unter `shared` oder
`composition_root` stehen. In Projekten ohne Features bleiben Module neben den Layern
(`app/main.py`) erlaubt und höchstens ein SMT305-Hint. `structure.max_depth` ist nicht
umgesetzt, weil es kein konkreter Bedarf war.

### 6. ~~Test-Hierarchie bei `layout: feature`~~ (entfällt)
**Entschieden:** Das Ziel ist ein strenges `mirror`. Die Lücke bei `layout: feature` (Tests
ohne Feature-Import werden übergangen) ist deshalb nicht relevant. Test-Ordner ohne
passendes Quellpaket (z. B. `tests/somewhere/`) erkennt schon der Pfad-Check aus #3.

### 7. Die Spiegel-Konvention ist fest verdrahtet
`_mirror_path` entfernt das Root-Paket (`app.billing.invoice` → `tests/billing/`). Manche
Projekte nutzen `tests/app/billing/` oder `tests/unit/billing/`. Das sollte ein Pattern sein,
analog zu `tests.pattern`, z. B. `tests.mirror: "tests/unit/{path}/test_{module}.py"`.

**Entschieden:** Standard bleibt wie heute (`app/billing/invoice.py` →
`tests/billing/test_invoice.py`, ohne Root-Paket). Andere Konventionen sind über das Pattern
konfigurierbar.

**Umgesetzt:** `tests.mirror`, relativ zum Test-Root, Standard `"{path}/test_{module}.py"`.
Platzhalter: `{path}` (Paketpfad unterhalb des Root-Pakets, darf leer sein), `{module}`
(Pflicht) und `{root}`. Beispiele: `"{root}/{path}/test_{module}.py"`,
`"unit/{path}/test_{module}.py"`, `"{path}/{module}_test.py"`. Ein Dateiname, der nicht zum
Pattern passt, ist ein Fehler („invoice_test.py does not match tests.mirror (…)“). Zeigen die
Imports eindeutig auf das Modul, nennt die Meldung den richtigen Namen („should be named
test_invoice.py“). Das ist bewusst streng: `*_test.py` gilt beim Standard-Pattern nicht mehr
als gespiegelt. Ungültige Patterns weist die Config-Validierung ab.

---

## P1: Agent-UX

### 8. Hints sind für Agents unsichtbar
Im Textformat sind Hints ohne `--show-hints` nur eine Zahl. Die README empfiehlt zwar
`--format json`, aber genau die strukturellen Regeln (SMT304, SMT305, SMT408) sind Hints. Wenn
sie für den Agent relevant sind, braucht es entweder eine Severity pro Regel, die der Nutzer
leicht hochsetzt (das geht schon über `rules:`), oder eine Doku-Empfehlung „für Agents:
SMT305: error“. Besser wäre ein Preset `strict`.

**Entschieden:** Im `--changed`-Modus werden Hints immer angezeigt, auch ohne
`--show-hints`. Sie betreffen dann nur die gerade geänderten Dateien, sind also wenige und
relevant. Im vollen Check bleiben sie eingeklappt.

**Umgesetzt:** `smelt check --changed` (und `--base`) zeigt Hints im Textformat immer an.
JSON/SARIF enthalten sie ohnehin.

### 9. ~~`smelt where test` für den exakten Testpfad~~ (entfällt)
**Entschieden:** smelt wird *rückblickend* eingesetzt: Der Agent arbeitet, dann prüft
`smelt check`. Proaktive Hilfen, die dem Agent vorher sagen, wo etwas hingehört, sind
unnötiger Scope. Die Fehlermeldung aus #3 nennt den erwarteten Pfad ohnehin.

### 10. ~~Verwaiste Tests automatisch mitverschieben~~ (entfällt)
**Entschieden:** Kein automatischer Fix. Das bringt zu viel Komplexität für wenig Nutzen.
Der Agent verschiebt die Datei anhand der Fehlermeldung selbst.

---

## P2: Korrektheit und Kleinkram

11. **SMT408 test-only-api ist in Bibliotheken ein Fehlalarm.** Im Scratch-Projekt wurden
    `total()` und `pay()` gemeldet, weil nur Tests sie nutzen. Bei einer Library ist genau
    das die öffentliche API. Die Regel sollte `__all__`/Re-Exports aus dem Root-Paket als
    „öffentlich“ werten oder eine Option `tests.public_api: [app.api.**]` bekommen.
    smelt selbst braucht dafür schon ein Ignore (`smelt/docs.py:90`).
    **Entschieden:** SMT408 wird opt-in (standardmäßig aus, wie SMT206). Die Regel bleibt als
    Hilfe erhalten, bekommt aber keine neue Option. Das Ignore in `smelt/docs.py:90` fällt weg.
    **Umgesetzt:** `enabled_by_default = False`, aktivierbar über `rules: {SMT408: hint}` oder
    `--select SMT408`. smelt läuft jetzt ohne ein einziges Suppress-Kommentar.
12. ~~**Testdateien, die andere Testdateien importieren**~~ (entfällt).
    **Entschieden:** Keine eigene Regel, das wäre neuer Scope. Das Beispiel
    `tests/cli/test_fix.py` verschwindet ohnehin mit #17.
13. **Python-Version ist inkonsistent.** `SPEC.md` §2 fordert `>=3.14`, `.python-version`
    ist 3.14, aber `pyproject.toml` hat `requires-python = ">=3.12"`, Ruff/Mypy zielen auf
    3.12 und CI testet 3.12 bis 3.14.
    **Entschieden: smelt unterstützt 3.12 bis 3.14.** `pyproject.toml` und CI bleiben so, die
    Spec wird auf `>=3.12` korrigiert. Für smelts eigenen Code heißt das:
    `from __future__ import annotations` bleibt dort nötig, wo Annotationen Namen aus
    `if TYPE_CHECKING:` oder Vorwärtsreferenzen nutzen (3.12 und 3.13 werten Annotationen
    sofort aus), aber nicht pauschal in jeder Datei.

    **Dazu: versionsabhängige Eigenheiten des *geprüften* Codes testen.**
    - **Neue Syntax bricht die Analyse ab (verifiziert).** smelt parst mit `ast.parse` des
      Interpreters, auf dem smelt selbst läuft. Unter 3.12 schlagen fehl:
      `except A, B:` ohne Klammern (3.14, PEP 758), Template-Strings `t"..."` (3.14,
      PEP 750) und Typ-Parameter mit Default `def f[T = int]` (3.13, PEP 696). Das endet mit
      `syntax error` und Exit 2, obwohl der Code gültig ist. `ast.parse(feature_version=...)`
      hilft nicht, weil es nur *ältere* Grammatik erzwingen kann. Vorschlag: Die
      Fehlermeldung erkennt den Fall (`requires-python` des Projekts > laufender
      Interpreter) und sagt konkret: „Dieser Code nutzt Syntax aus Python 3.14, smelt läuft
      auf 3.12. Starte smelt mit 3.14, z. B. `uvx -p 3.14 smelt check`.“ Das README
      bekommt dazu einen Satz.
    - **3.14: Annotationen ohne `from __future__ import annotations`.** Mit PEP 649/749
      schreiben 3.14-Projekte `def f(repo: SqlRepo)`, wobei `SqlRepo` nur unter
      `if TYPE_CHECKING:` importiert wird, ohne Future-Import und ohne Anführungszeichen.
      smelt muss das genauso behandeln wie die gequotete bzw. Future-Variante (SMT202,
      SMT206, `imports.type_checking`). Laut Code sollte das klappen (`annotation_names` in
      `rules/code/common.py:76` arbeitet auf dem AST), aber es fehlt ein Fixture, das es
      absichert.
    - **3.12/3.13:** `type X = ...` (PEP 695) und generische Klassen `class Repo[T]:` sollten
      bei Rollen- und Basisklassen-Erkennung (SMT203, SMT204) funktionieren.
    - Umsetzung: je Version ein kleines Fixture-Projekt, dazu Tests mit
      `pytest.mark.skipif(sys.version_info < (3, 14))`. Die CI-Matrix 3.12 bis 3.14 gibt es
      schon.

    **Umgesetzt:**
    - Syntaxfehler (aus `ast` und aus grimp) enden jetzt mit „(parsed by Python 3.12)“.
      Zielt `requires-python` in `pyproject.toml` oder `.python-version` auf eine neuere
      Version, kommt dazu: „the project targets Python 3.14, so run smelt on it, e.g.
      `uvx -p 3.14 smelt check`“. Die README hat einen Abschnitt „Python versions“.
    - `tests/analysis/test_parsing.py`: Unter 3.12/3.13 erzeugt 3.14-Syntax den Hinweis.
      Unter 3.13+ bzw. 3.14 werden Typ-Parameter-Defaults bzw. t-Strings und
      `except A, B:` geparst, und die Regeln laufen normal (per `skipif`).
    - Annotationen im Stil von 3.14 (ohne Future-Import und ohne Anführungszeichen, mit
      `TYPE_CHECKING`-Namen) für SMT202 sowie PEP 695 (`class Repository[T](Protocol)`,
      `type Key = str`) für die Rollen-Erkennung sind jetzt durch Tests abgesichert.
      Beides funktionierte schon, ein Code-Fix war nicht nötig.
    - Die volle Suite lief lokal unter 3.12, 3.13 und 3.14 grün.
14. **Der Pre-commit-Hook läuft nur bei `types: [python]`.** Eine Änderung nur an `smelt.yaml`
    triggert ihn nicht, obwohl eine strengere Config neue Fehler im ganzen Projekt auslösen
    kann. `files: '(\.py|smelt\.yaml)$'` wäre robuster.
    **Umgesetzt:** Beide Hooks (`.pre-commit-hooks.yaml` für Nutzer, `.pre-commit-config.yaml`
    für smelt selbst) laufen jetzt auch bei `smelt.yaml`/`smelt.yml`. Weil der veröffentlichte
    Hook die Dateinamen übergibt, hätte `smelt check smelt.yaml` sonst nur die Config „geprüft“
    und nichts gemeldet. Deshalb gilt jetzt: Steht die Config unter den Pfaden, prüft
    `smelt check` das ganze Projekt. (Aufgefallen beim Commit von #15, der nur `smelt.yaml`
    änderte: Der smelt-Hook wurde übersprungen.)
15. **smelt dogfoodet die eigenen Test-Regeln nicht.** Die eigene `smelt.yaml` setzt
    `tests.layout: none`, obwohl die Tests fast gespiegelt sind. Wenn Spiegelung ein Kernziel
    ist, sollte smelt selbst `mirror` nutzen. Das deckt Fälle wie `tests/cli/test_fix.py`
    auf, und neue Regeln wie #2 würden sofort an einem echten Projekt getestet.
    **Umgesetzt:** `smelt.yaml` nutzt jetzt `layout: mirror`. smelts eigene Tests waren nach
    #17 bereits vollständig gespiegelt, der Check ist grün. Eine testweise angelegte
    `tests/cli/test_ghost.py` wurde korrekt als verwaist gemeldet.

16. **„Baseline“ ist kein selbsterklärender Name.** Gemeint ist: bekannte Altlasten, die
    beim Einführen von smelt eingefroren werden, damit nur neue Verletzungen fehlschlagen.
    Ein sprechenderer Name für Befehl, Config-Key, Datei und SMT903 wäre besser
    Betrifft etwa 100 Stellen in 21 Dateien.
    **Entschieden: `debt`.** Befehl `smelt debt` (bzw. `smelt debt --prune`), Config-Key
    `debt: .smelt/debt.json`, SMT903 wird zu `resolved-debt` („Eintrag behoben, bitte
    entfernen“).
    **Umgesetzt:**
    - Befehl `smelt debt` / `smelt debt --prune`, Flag `smelt check --no-debt`, Config-Key
      `debt`, Standarddatei `.smelt/debt.json`.
    - Modul `smelt/diagnostics/debt.py` mit `Debt`/`DebtEntry`. SMT903 heißt `resolved-debt`
      („resolved debt entry: …“).
    - Die Ausgabe zeigt „2 in debt“ statt „2 baselined“, im JSON-Summary heißt das Feld
      `in_debt`.
    - Die README erklärt `smelt debt` jetzt, vorher kam der Befehl dort nicht vor.
    - Keine Rückwärtskompatibilität für `baseline:`: Eine alte Config scheitert mit „unknown
      key "baseline"“ und damit klar erkennbar.

17. **Scope verkleinern: `smelt fix` und `smelt where` entfernen.**
    **Entschieden:** smelt prüft im Nachhinein, der Agent behebt selbst. Das heißt:
    - `smelt fix` fällt komplett weg, samt `smelt/fix/` (`move_class.py` mit 507 Zeilen ist
      die größte Datei im Projekt, dazu `plan.py`), `engine/fix.py`, `cli/commands/fix.py`
      und den Tests dazu. Außerdem die Fix-Typen in `diagnostics/violation.py`
      (`Fix`, `FileMove`, `LineEdit`), das Feld `fixable` an Regeln, in JSON und in der Doku
      (Spalte „Fixable“), sowie „Fixable with `smelt fix`“ in der Textausgabe.
    - Den Layer `fix` in der eigenen `smelt.yaml` entfernen.
    - `smelt where` fällt als Befehl weg. `smelt context` bleibt und zeigt weiter
      „Where things go“. Die interne Funktion `where_path` (`engine/briefing.py:204`) bleibt
      dafür erhalten.
    - Der Pfad in `expected` und der `hint` einer Verletzung bleiben. Sie sind das, womit der
      Agent selbst korrigiert.

    **Umgesetzt:** wie oben. Zusätzlich sind `layer_path` (nur von `smelt where` genutzt) und
    `without_codes` (nur vom SMT901-Fix genutzt) weggefallen. Die Doku unter `docs/rules/` ist
    neu generiert, Hinweise auf `smelt where` in SMT204 und `smelt init` zeigen jetzt auf
    `smelt context`.

---

## Vorgeschlagene Reihenfolge

1. ~~Spec schärfen (#1)~~ entfallen. ✓ Scope verkleinern (#17).
2. ✓ `mirror` pfadbasiert machen und verwaiste Tests melden (#2, #3).
3. ✓ smelt selbst auf `mirror` umstellen (#15) als Realitätstest.
4. ✓ Lose Module im Feature melden (#5) und Hints im `--changed`-Modus zeigen (#8).
5. ✓ Kleinkram und Bugs (#4, #11, #13, #14, #16), dazu die Spiegel-Konvention (#7).

---
---

# Runde 2: Usability-Test an Prompster (2026-10-05)

Ziel, an dem gemessen wird: smelt soll in automatisierten Architektur-Reviews sicherstellen,
dass Agents **(a) die Teststruktur der Produktivstruktur spiegeln** und **(b) Architektur-
grenzen einhalten**. Alles andere ist Nebensache und Kandidat für eine Scope-Reduktion.

Vorgehen: Kopie von `prompster` (Commit `a066979`, uv-Workspace `backend` + `libs/agent`,
Features unter `backend.features.<name>/{domain,application,infrastructure,presentation}`)
in einem Temp-Ordner, dann der Weg eines neuen Nutzers: `--help` → `init` → `check` →
Mirror einschalten → Verstöße gezielt einschleusen → `--changed` → `debt`. Prompster selbst
wurde nicht verändert.

## P0: Falsch-grüne Ergebnisse für die beiden Kernziele

### R1. Alles außerhalb von `features.root` ist eine Blackbox für Grenzen (verifiziert)
36 von 215 Modulen sind unklassifiziert: das ganze Paket `agent`, `backend.platform.*`,
`backend.presentation.*`, `backend.app`, `backend.env`. Für sie gilt keine einzige
Grenzregel, und sie erscheinen nur als eingeklappter SMT305-Hint. Eingeschleust und
**nicht gemeldet**:

| Import | Ergebnis |
| --- | --- |
| `user/domain/__init__.py` → `backend.platform.database.orm` (Domain importiert ORM) | **still** |
| `backend.platform.database.settings` → `session.infrastructure.repository` | nur SMT305-Hint (+ zufällig SMT104-Zyklus) |
| `agent.views` (Lib) → `backend.features.session.domain` (Lib importiert App) | nur SMT305-Hint |

Das ist der größte Befund: Ein Agent kann die Domain an die Infrastruktur koppeln, solange
die Infrastruktur nicht im Feature liegt. In DDD-Projekten liegt aber genau die technische
Infrastruktur (DB, Storage, Logging, HTTP-Middleware) oft zentral.

Vorschlag (eine Mechanik, kein neues Konzept): Module außerhalb von Features einem Layer
zuordnen können, z. B.

```yaml
architecture:
  modules:                       # Name offen
    backend.platform: infrastructure
    backend.presentation: presentation
    agent: infrastructure        # oder eigene "library"-Kategorie
```

Dann greift SMT101 automatisch: `domain → platform` ist `domain → infrastructure` und damit
ein Fehler. Zusätzlich sollte ein Import von *unklassifiziert* nach *Feature* mindestens eine
Warnung sein, kein Hint. Die Abhängigkeitsrichtung zwischen Workspace-Paketen (`agent` darf
`backend` nicht importieren) lässt sich mit derselben Zuordnung oder einer einfachen
`forbid`-Liste ausdrücken.

### R2. Falsche Pfade in der Config werden still akzeptiert (verifiziert)
- `features.root: backend.featurez` (Tippfehler) → kein Config-Fehler, stattdessen 156
  Hints und null Grenzfehler. Die Policy ist effektiv aus.
- `test_roots: [backend/testz]` → kein Fehler, alle SMT401-Befunde verschwinden.

Beides muss Exit 2 mit „does not exist“ sein, analog zu den schon guten Meldungen bei
Tippfehlern in Keys und Layernamen (`unknown layer "domian" (did you mean "domain"?)`).
Vermutlich gilt dasselbe für `shared`, `composition_root`, `wiring` und `source_roots`.

### R3. Mirror im Workspace mit mehreren Root-Paketen ist mit dem Standard-Pattern kaputt (verifiziert)
Mit `layout: mirror` und dem Standard `{path}/test_{module}.py` wird
`libs/agent/tests/agent/tools/test_x.py` gegen `backend/src/backend/agent/tools/x.py`
geprüft, also gegen das **falsche** Root-Paket. Ergebnis: 100 Fehler, viele davon falsch.
Mit `{root}/{path}/test_{module}.py` stimmt es für beide Roots. Ohne `{root}` müsste smelt
jede Test-Root ihrer Source-Root zuordnen (gleiches Workspace-Member), statt das erste
Root-Paket zu nehmen.

### R4. `smelt init` schlägt `tests.layout: none` vor, obwohl das Projekt spiegelt
Prompster spiegelt konsequent (`tests/backend/features/...`). `init` sollte das Layout aus
den vorhandenen Tests ableiten: Wenn die Mehrheit der Testdateien unter `{root}/{path}` bzw.
`{path}` einen Treffer hat, `layout: mirror` mit passendem `mirror`-Pattern vorschlagen.
Sonst ist das Kernfeature nach `init` aus, und der Nutzer merkt es nicht.

## P1: Usability für Agents

### R5. `--changed` meldet Altlasten in fremden Dateien (verifiziert)
Nur ein Kommentar in `user/domain/__init__.py` geändert → 4 SMT102-Fehler in
`auth/application/*` und `session/application/*`, weil `_in_scope`
(`smelt/engine/check.py:348`) auch Verstöße meldet, deren *Ziel*modul geändert wurde. Der
Agent wird damit aufgefordert, fremde, unveränderte Features umzubauen. Für einen
automatisierten Review-Loop ist das das Gegenteil von hilfreich.

Vorschlag: `--changed` meldet nur, was die Änderung **neu eingeführt** hat. Am saubersten:
Verstöße auf `--base` (Standard `HEAD`) berechnen und als implizite Debt abziehen. Dann
braucht der Agent-Loop keine `debt.json` mehr, und Altlasten in der geänderten Datei werden
nicht mehr gemeldet. Zyklen, die durch die Änderung entstehen, bleiben sichtbar.

### R6. Mirror-Meldungen ohne Vorschlag, wo einer möglich wäre (verifiziert)
- `test_session_infrastructure_repository.py` importiert eindeutig
  `session.infrastructure.repository` und liegt im richtigen Ordner, aber die Meldung sagt
  nur „mirrors no source module“. Erwartet: „should be named test_repository.py“. Der
  Import-Hinweis greift heute nur, wenn der Modulname exakt im Dateinamen steht.
- `test_fernet_token_cipher.py` neben der Quelle `fernet_token_cypher.py`, `test_usr.py`
  neben `user.py`: Ein „did you mean `fernet_token_cypher.py`?“ per Ähnlichkeit im selben
  Ordner würde Tippfehler sofort sichtbar machen.
- JSON: Bei SMT401 sind `feature`, `layer`, `source_module` und `expected` leer, obwohl der
  Spiegelpfad Feature und Layer eindeutig bestimmt. `expected` sollte mindestens den
  geprüften Quellpfad enthalten.

### R7. `init` erzeugt eine Config, die sofort an sich selbst scheitert
`init` meldet direkt 78 Fehler. Einige davon sind Artefakte der Ableitung:
- `backend.app` importiert `backend.lifespan` und die DI-Provider. Das ist Teil der
  Composition (FastAPI-App-Factory), `init` stuft es aber nicht als Composition Root ein →
  3× SMT106.
- 9 Wiring-Module werden einzeln aufgezählt, obwohl das Muster
  `backend.features.*.infrastructure.di` unterstützt wird. Die Liste taucht danach in jeder
  SMT205-Meldung und in jedem `smelt context` auf (siehe R9).
- 41 der 78 Fehler sind SMT403 (private Zugriffe in Tests), obwohl `tests.layout: none`.
  Für das erste Architektur-Review verdecken sie die Grenzbefunde (siehe Scope unten).

### R8. Doppelte Meldungen für dieselbe Zeile
`shared/ddd.py` importiert ein Wiring-Modul eines Features → SMT105 **und** SMT106 auf
derselben Spalte. Ein Befund pro Import reicht, und zwar der spezifischere.

### R9. `smelt context` ist für ein Feature zu laut und zu ungenau
- Die `Wiring:`-Zeile listet die DI-Module aller Features und der Plattform.
- `Tests: mirror the source tree, …` nennt nicht den konkreten Pfad. Für das Feature `user`
  wäre `backend/tests/backend/features/user/<layer>/test_<module>.py` die eigentlich
  nützliche Zeile.
- Für ein unklassifiziertes Modul (`context backend/src/backend/platform/database/orm.py`)
  kommt die volle Feature-Policy, obwohl keine davon gilt.

### R10. SMT205 bei FastAPI + dishka nicht umsetzbar
`conn.state.dishka_container.get(...)` in `presentation/dependencies.py` wird gemeldet, der
Hint sagt „resolve it in backend.main“. In einer FastAPI-Dependency gibt es dafür keinen
Weg. Entweder kennt die Regel die Integrationsmuster des deklarierten Frameworks, oder sie
fliegt raus (siehe Scope).

## P2: Kleinkram

- **R11.** `smelt.schema.json` hat keine `description`s. Editor-Autocomplete zeigt Keys ohne
  Erklärung. Pydantic-`Field(description=...)` würde reichen.
- **R12.** `smelt debt` schreibt die Datei, aktiviert sie aber nicht („add `debt:` to
  smelt.yaml“). Entweder trägt `debt` den Key selbst ein, oder `.smelt/debt.json` wird
  benutzt, sobald sie existiert.
- **R13.** Die README kennt `inspect`, `verify` und `config` nicht, `--help` listet sie.
- **R14.** `explain SMT999` schlägt „SMT903“ vor, `context nonexistent` listet die Features
  nicht auf. Bei unbekanntem Feature wäre „known features: auth, health, …“ hilfreich.

Positiv, sollte so bleiben: Config-Validierung mit „did you mean“, das Textformat mit
Snippet + `Expected` + `Hint`, der „belongs in …“-Vorschlag, wenn der Dateiname passt,
robuste Debt-Fingerprints (Zeilenverschiebung ok, zusätzlicher gleichartiger Verstoß wird
erkannt) und eine Laufzeit unter 2 s für 215 Module.

## Scope-Reduktion: Vorschlag

Gemessen an den Zielen (a) Mirror und (b) Grenzen trägt dieser Kern:

| Behalten | Warum |
| --- | --- |
| SMT101, SMT102, SMT104, SMT105, SMT106 | Grenzen |
| SMT301, SMT302, SMT305 | Ordnerstruktur, kein Modul außerhalb der Ordnung |
| SMT401 | Mirror |
| SMT901, SMT902, SMT903 | Suppressions und Debt bleiben ehrlich |
| `check`, `explain`, `context`, `init`, `debt`, `rules` | Agent-Loop und Adoption |

Kandidaten zum Streichen (oder zumindest standardmäßig aus):

| Kandidat | Begründung |
| --- | --- |
| Rollen: SMT203, SMT204, SMT303, `roles:`, `analysis.types: pyright` | eigenes Konzept mit Konfigurationsaufwand, in Prompster nicht sauber anwendbar (Ports teils in domain, teils in application) |
| SMT201, SMT202, SMT205, SMT206, `di_frameworks` | Code-Stil/DI-Pattern, keine Grenze; SMT205 bei FastAPI nicht umsetzbar (R10) |
| Testqualität: SMT402–SMT408, `tests.patching/mocks/private_access/bloat/interaction_assertions` | machen in Prompster 58 von 78 Befunden aus und verdecken die Grenzen; Teststil ist nicht Teststruktur |
| SMT304 crowded-package | Geschmackssache |
| `verify`, `inspect`, `plugins` | nicht Teil des Agent-Loops, `verify` dupliziert pre-commit/CI |
| `tests.layout: feature` | Ziel ist `mirror`, `feature` ist ein Zwischenweg |

Offen: SMT103 `third-party-denied` eher **behalten**. „`domain` darf kein `sqlalchemy`
importieren“ ist eine echte Grenze; `init` schlägt es nur nicht vor.

Nach dieser Reduktion gibt es für Agents im Kern nur noch zwei Befunde: „Grenze verletzt“
oder „Test liegt nicht am Spiegelpfad“.

## Vorgeschlagene Reihenfolge (Runde 2)

1. Scope reduzieren (oben). Damit entfallen R7 (SMT403-Rauschen) und R10 von selbst.
2. R2 (stille Config-Pfade) und R3 (Mirror im Workspace): klein, verhindern falsch-grüne
   bzw. falsch-rote Ergebnisse.
3. R1 (Module außerhalb von Features klassifizieren): größter Hebel für Ziel (b).
4. R5 (`--changed` meldet nur Neues): größter Hebel für den Agent-Loop.
5. R4 + R6 (Mirror ableiten, bessere Vorschläge).
6. R8, R9, P2.

## Umgesetzt (Runde 2)

Alle Punkte sind umgesetzt, jeweils in einem eigenen Commit. Validiert wurde auf einer
frischen Kopie von Prompster.

- **Scope:** wie in der Tabelle oben gestrichen. Neben den genannten Regeln ist auch
  `di_frameworks` weggefallen: Ein DI-Framework in einem Layer wird über
  `layers.<x>.third_party` verboten. SMT103 bleibt. R7 (SMT403-Rauschen) und R10 entfallen
  damit.
- **R1:** `architecture.modules` ordnet Pakete außerhalb der Features einem Layer zu
  (`backend.platform: infrastructure`). SMT101 gilt dann auch zwischen Feature und
  zentralem Modul, SMT105 verbietet zentralen Modulen Feature-Importe. SMT305 ist jetzt
  eine Warnung und sagt, wie man ein Modul einordnet. In Prompster werden alle drei
  eingeschleusten Fälle gemeldet.
- **R2:** Nicht existierende `source_roots`, Root-Pakete, `features.root`/`pattern`,
  `shared`, `composition_root`, `modules`, Wiring-Muster ohne Treffer und (bei `mirror`)
  `test_roots` sind Config-Fehler mit Exit 2 und „did you mean“.
- **R3:** Eine Test-Root spiegelt die Root-Pakete ihres Workspace-Members (längster
  gemeinsamer Pfad mit der Source-Root).
- **R4/R7:** `init` leitet `layout: mirror` samt Muster aus den Tests ab (Prompster:
  `{root}/{path}/test_{module}.py`, 15 von 58 Tests spiegeln schon). Außerdem erkennt es:
  App-Factory (`backend.app`) als Composition Root, Settings/Logging-Module als `shared`,
  zentrale Pakete per Name als `modules` sowie Wiring als `*`-Muster. Nicht zuordenbare
  Workspace-Pakete (`agent`) stehen auskommentiert in der Config.
- **R5:** `--changed` prüft den Basis-Commit (HEAD bzw. Merge-Base mit `--base`) mit der
  aktuellen Config und zieht dessen Verstöße per Fingerprint ab. Gemeldet wird nur, was
  neu ist.
- **R6:** „should be named test_repository.py“ auch bei Präfix-Namen, „did you mean
  "fernet_token_cypher.py"?“ bei Tippfehlern, Hinweis auf fehlendes `{root}`. Im JSON sind
  `source_module`, `feature`, `layer` und `expected.source` gesetzt.
- **R8:** SMT105 macht SMT106 (und SMT101) für denselben Import überflüssig.
- **R9/R14:** `context` zeigt nur die Wiring des Features und den konkreten Spiegelpfad,
  benennt zentrale Module und erklärt unklassifizierte, statt eine Policy aufzulisten, die
  nicht gilt. Bei unbekanntem Ziel listet es die Features. `explain` schlägt einen Code nur
  vor, wenn genau einer sich in einem Zeichen unterscheidet.
- **R11:** Jeder Key im Schema hat eine `description`.
- **R12:** `smelt debt` trägt `debt:` selbst ein bzw. entkommentiert die Zeile aus `init`.
- **R13:** README neu: alle Befehle, `--changed`-Semantik, DDD-Beispiel, Mirror, Adoption.
