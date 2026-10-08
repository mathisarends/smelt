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

## Runde 3: Usability am aktuellen PR #6

Geprüft am 06.10.2026: smelt `a70a0f7` (aktueller PR-Head), Prompster `a066979`,
Python 3.14 unter Windows. Alle folgenden Reproduktionen liefen auf einem separaten
Prompster-Worktree; die laufenden Änderungen im ursprünglichen Projekt blieben unberührt.
Das sind Befunde zum aktuellen Produktstand, nicht ausschließlich neue Regressionen dieses PRs.

Maßstab bleibt: **Architektur-Guidelines verständlich ausdrücken und zuverlässig prüfen.**
Ein grüner Lauf muss von einem leeren Prüfauftrag unterscheidbar sein; `context`, Config,
Regeldoku und Checker müssen dieselbe Policy beschreiben.

Ausgangspunkt: `smelt init` erkennt 215 Module und 58 Tests, davon 15 bereits gespiegelt.
`smelt check --format json` meldet 75 Fehler und 22 Warnungen: 43 SMT401, 28 SMT102,
3 SMT101, 1 SMT104 und 22 SMT305. Ein unveränderter `--changed`-Lauf ist korrekt grün.
Der volle Lauf dauerte hier ca. 0,6 s, `--changed` ca. 1,9 s; kein Laufzeitproblem festgestellt.

### P1 — U1. Ein vertippter Prüfpfad sieht wie ein bestandener Architekturcheck aus

**Verifiziert:**

```sh
smelt check backend/src/backend/features/usr --format json
```

Exit 0, `status: passed`, alle Befundzähler 0, aber `modules: 215`.
Das Feature heißt `user`; der eingegebene Pfad existiert nicht. Der komplette Check hat
weiterhin 75 Fehler. Dass 215 Module gezählt werden, verstärkt den falschen Eindruck,
der gewünschte Teil sei geprüft worden.

**Vorschlag:** Explizite Pfade auf Existenz und Zugehörigkeit zu den analysierten Quellen
bzw. Tests prüfen. Bei Tippfehlern Exit 2 mit `did you mean .../user?`; bei existierenden,
aber nicht analysierten Pfaden den Grund nennen. In der Ausgabe geprüften Scope und
Anzahl der tatsächlich vom Bericht abgedeckten Dateien sichtbar machen.

**Stellen:** `smelt/cli/commands/check.py:31`, `smelt/engine/check.py:378`.

### P1 — U2. Ein unbekannter Rule-Selector deaktiviert still die gesamte Prüfung

**Verifiziert:**

```sh
smelt check --select SMT999 --format json
```

Exit 0, `status: passed`, keine Befunde. Anders als `smelt explain SMT999` wird der
unbekannte Code nicht zurückgewiesen. Für einen Agenten ist ein Tippfehler dadurch ein
erfolgreicher Check.

**Vorschlag:** Jeden `--select`- und `--ignore`-Eintrag gegen die bekannten Regeln prüfen.
Präfixe wie `SMT1` bleiben erlaubt; ein Präfix ohne Treffer ist ein Usage-Fehler mit Exit 2.
Eine bewusst leere Auswahl sollte ausdrücklich als leer erscheinen.

**Stelle:** `smelt/engine/check.py:77` (`resolve_active_rules`).

### P1 — U3. `--changed` meldet einen bestehenden Verstoß nach einem reinen Kommentar erneut

**Verifiziert:** In Prompster steht in
`backend/src/backend/features/auth/presentation/cookies.py:7` bereits:

```python
from backend.features.auth.infrastructure import AuthSettings
```

Nur `  # explanatory comment only` an diese Zeile anhängen, dann:

```sh
smelt check --changed --format json
```

Jetzt Exit 1 mit SMT101 für genau diesen bestehenden Import. Vorher war derselbe
`--changed`-Lauf grün. Die Architekturabhängigkeit hat sich nicht verändert.

**Ursache:** Der Fingerprint enthält die vollständige Quellzeile einschließlich Kommentar.
Whitespace wird normalisiert, Kommentare nicht. Das betrifft auch Debt: Ein Kommentar
kann eine vorhandene Baseline-Zuordnung verlieren.

**Vorschlag:** Importbefunde anhand normalisierter Import-Syntax und der beteiligten Module
vergleichen; Kommentare und reine Formatierung auslassen. Die bestehende Multiset-Logik
beibehalten, damit ein zusätzlich eingeführter gleicher Import weiterhin auffällt.

**Stellen:** `smelt/diagnostics/debt.py:27`, `smelt/engine/check.py:168`.

### P1 — U4. Gleichwertige Pfadschreibweisen erzeugen unterschiedliche Mirror-Ergebnisse

**Verifiziert:** In der erzeugten Config nur ändern:

```yaml
project:
  test_roots: [./backend/tests, ./libs/agent/tests]
```

`smelt check --select SMT401 --format json` meldet nun **58 statt 43** Fehler.
Alle 15 bisher korrekt gespiegelten Tests werden zusätzlich bemängelt. Beispiel:

```text
test_google_oauth_flow.py belongs in ./backend/tests/backend/features/auth/application/
Hint: Move the file to ./backend/tests/backend/features/auth/application/test_google_oauth_flow.py.
```

Der Test liegt bereits genau dort. Der Hinweis fordert somit einen wirkungslosen Move.
Die Verzeichnisse existieren und bestehen die neue Config-Pfadvalidierung.

**Vorschlag:** Source- und Test-Roots einmal beim Laden zu einheitlichen projektbezogenen
Pfaden normalisieren und diese Darstellung auch für Discovery, Workspace-Zuordnung und
Mirror-Vergleiche verwenden. Gleichwertige Schreibweisen dürfen keine Befunde erzeugen.

**Stellen:** `smelt/analysis/files.py:115`, `smelt/rules/testing/location.py:70`.

### P1 — U5. `config show` gibt bei Feature-Ausnahmen keine wiederverwendbare Config aus

**Verifiziert:** Die gültige Config um diese Ausnahme ergänzen:

```yaml
architecture:
  cross_feature:
    default: deny
    allow:
      - "application -> application"
      - {from: session.presentation, to: auth.presentation}
```

Dann:

```sh
smelt config show > resolved.yaml
smelt --config resolved.yaml check --select SMT102
```

Der zweite Befehl scheitert mit Exit 2. `show` schreibt `source`/`target`, der Loader
verlangt `from`/`to`. Auch die JSON-Ausgabe nutzt die internen Namen.
Zusätzlich enthält die Fehlermeldung interne Pydantic-Texte wie
`allow[1].function-after[_feature_layers(), CrossFeatureAllowance].from` und einen
irrelevanten Fehler für den String-Zweig der Union.

**Vorschlag:** Mit den öffentlichen Config-Aliasnamen serialisieren (`by_alias=True`).
Validierungsfehler auf verständliche YAML-Pfade reduzieren, z. B.
`architecture.cross_feature.allow[1].from: required key is missing`.

**Stellen:** `smelt/cli/commands/info.py:99`, `smelt/config/loader.py:97`.

### P1 — U6. `context` beschreibt nicht zuverlässig die tatsächlich geltenden Ausnahmen

**Verifiziert, drei Fälle:**

- `smelt context backend/src/backend/features/user/infrastructure/di.py` zeigt
  `infrastructure → domain, application` und `Cross-feature: only application → application`.
  Für dieses deklarierte Wiring-Modul sind layer- und featureübergreifende Imports erlaubt.
  Die Ausgabe kennzeichnet diese Ausnahme nicht ausdrücklich.
- `smelt context backend/src/backend/app.py` zeigt dieselbe eingeschränkte Feature-Policy,
  obwohl dieses Modul als Composition Root übergreifend verdrahten darf.
- Ein neues, unklassifiziertes `agent.review_probe` mit
  `from backend.app import create_app` wird von SMT106 abgelehnt. `context` erklärt trotzdem:
  `No boundary rule covers this module: it may import anything` — und meldet direkt
  darunter einen SMT106-Fehler. Auch SMT104 gilt für unklassifizierte Module.

**Zusätzlich im JSON:** `context backend/src/backend/platform --format json` liefert
`module_kind: feature`, `feature: null`, aber kein `central`-Feld. Das vorhandene interne
Flag wird nicht serialisiert. Ein konsumierender Agent kann die zentrale Platzierung und
das Verbot von Feature-Imports daraus nicht direkt erkennen. Wiring ist ebenfalls nur
eine Liste von Mustern, kein expliziter Status des Zielmoduls.

**Vorschlag:** Die effektiv geltende Policy je Ziel ausgeben: Placement, Wiring-/Root-
Ausnahmen und verbleibende Verbote. Diese Informationen auch strukturiert im JSON
bereitstellen. Bei unklassifizierten Modulen präzise sagen, welche Grenzen ungeprüft
bleiben und welche Regeln weiterhin gelten.

**Stellen:** `smelt/engine/briefing.py:64`, `:113`, `:206`.

### P1 — U7. Die Grenzen der von `init` erzeugten Policy bleiben zu unsichtbar

**Verifiziert:** Mit der unveränderten Prompster-Config aus `init` wird ein neues Domain-
Modul mit jeweils einem dieser Imports beim gezielten Check grün:

```python
from sqlalchemy.orm import Mapped
```

```python
from backend.features.user import UserProvider
```

Im ersten Fall erlaubt der Default alle Third-Party-Pakete. Im zweiten Fall exportiert
Prompsters Feature-`__init__.py` den Infrastruktur-Provider; `transitive` ist standardmäßig
`false`. Mit `architecture.imports.transitive: true` wird der zweite Fall als indirekte
Layer-Verletzung erkannt.

**Einordnung:** Das sind konfigurierbare Abdeckungsgrenzen, nicht der Nachweis, dass jede
Anwendung pauschal SQLAlchemy oder Re-Exports verbieten muss. Für das Ziel dieser Library
ist aber entscheidend, dass die Adoption diese Entscheidungen sichtbar macht. Die
erzeugte Config wirkt ausführlich, lässt beide relevanten Grenzen jedoch implizit offen.

**Vorschlag:** `init` um kommentierte `third_party`- und `imports.transitive`-Entscheidungen
mit konkreten Beispielen ergänzen. `context` sollte auch ein uneingeschränktes
Third-Party-Default nennen. Die README sollte die Prüfung direkter Imports und den
Re-Export-Fall samt Konfiguration erklären. Den vorgeschlagenen globalen Allow-Eintrag
`application -> application` als bewusste Architekturentscheidung kennzeichnen.

**Stellen:** `smelt/engine/inference.py` (`_architecture`),
`smelt/config/models.py:97`, `:218`, `smelt/engine/briefing.py:167`.

### P2 — U8. Eine beschädigte Debt-Datei liefert einen Traceback und den falschen Exit-Code

**Verifiziert:** Config `debt: review-debt.json`, Dateiinhalt:

```json
{"version": 1, "violations": [{}]}
```

`smelt check --format json` endet mit `KeyError: 'fingerprint'`, vollständigem Python-
Traceback und **Exit 1**. Laut CLI-Vertrag bedeutet 1 einen Architekturverstoß;
ein Config-/Dateiformatfehler müsste Exit 2 ergeben.

**Vorschlag:** Debt-Schema einschließlich Version validieren und einen kurzen Fehler mit
Dateipfad und Elementpfad melden, z. B. `review-debt.json: violations[0].fingerprint is
missing`. Das ist eine beschädigte Eingabedatei, kein regulärer Befund.

**Stellen:** `smelt/diagnostics/debt.py:46`, `smelt/cli/app.py:126`.

### P2 — U9. `context` ist als Vorbereitung von Änderungen zu stark vom aktuellen Code abhängig

**Verifiziert:**

- `context backend/src/backend/features/user/domain/new_entity.py` liefert Exit 2, obwohl
  der bestehende Parent-Pfad Feature und Layer bereits eindeutig festlegt.
- Ein Syntaxfehler in einer neuen Datei von `health` blockiert `smelt context user` mit
  Exit 2. Das User-Briefing ist überhaupt nicht mehr erhältlich.

**Vorschlag:** Geplante Dateien über ihren bestehenden Parent einordnen. Das Architektur-
Briefing aus Config und Dateistruktur erstellen; aktuelle Befundzahlen ergänzend berechnen.
Bei einem Analysefehler die Policy weiterhin ausgeben und die Befundzahlen ausdrücklich
als nicht verfügbar kennzeichnen. Das unterstützt die README-Anweisung, `context`
**vor** der Implementierung zu benutzen.

**Stellen:** `smelt/cli/commands/discover.py:21`, `smelt/engine/briefing.py:85`.

### P2 — U10. Ein Tippfehler in einer gezielten Feature-Ausnahme wird nicht als Config-Fehler erkannt

**Verifiziert:** `{from: sessoin.presentation, to: auth.presentation}` wird akzeptiert.
Der Lauf meldet nur die normalen SMT102-Verstöße, keinen Fehler über `sessoin`.
Die Ausnahme greift nie, und die Nutzer müssen den Grund aus den Befunden erschließen.

**Vorschlag:** Bei Objekt-Ausnahmen auch die Feature-Namen gegen die entdeckten Features
prüfen und `did you mean session?` anbieten. Layer-Namen werden bereits validiert.
Damit gilt das neue Prinzip „Config-Einträge, die auf nichts zeigen, sind Fehler“ konsistent.

**Stellen:** `smelt/config/validation.py:60`, `smelt/engine/paths.py:46`.

### P2 — U11. Das Bad-Beispiel von SMT106 wird von SMT106 gar nicht erkannt

**Verifiziert:** `smelt explain SMT106` und `docs/rules/SMT106.md` zeigen:

```python
from dishka import FromDishka
```

Ein Domain-Modul mit dieser Zeile bleibt unter `smelt check <datei> --select SMT106`
grün. Die Regel prüft Imports auf deklarierte interne Composition-/Wiring-Module,
keine Third-Party-DI-Nutzung. Das Beispiel stammt inhaltlich noch aus dem entfernten Scope.

**Vorschlag:** Ein tatsächlich von SMT106 erkanntes Beispiel verwenden, etwa einen Import
aus `backend.main` oder einem deklarierten `infrastructure.di`. Regeldokumentation muss
zeigen, was der Checker wirklich prüft, damit ein Agent aus `explain` richtig lernt.

**Stelle:** `smelt/rules/dependencies/composition_root.py:31` (generiert auch die Regeldoku).

### Weitere kleine Ergonomie-Befunde

- Die README verspricht `context` für „feature, module or path“, aber gepunktete
  Modulnamen wie `backend.features.user.domain.user` werden abgelehnt. Entweder diese
  Eingabe unterstützen oder die Dokumentation eindeutig auf Feature-Namen und Pfade begrenzen.
- `smelt check --config smelt.yaml` wird von argparse abgelehnt; nur
  `smelt --config smelt.yaml check` funktioniert. Die natürliche Position beim Subcommand
  ebenfalls erlauben oder mindestens einen gezielten Hinweis auf die richtige Reihenfolge geben.
- Die 22 SMT305-Warnungen für das unklassifizierte Workspace-Paket `agent` wiederholen
  dieselbe Entscheidung. Eine gemeinsame Package-Meldung mit den betroffenen Modulen würde
  die eigentlichen Grenzbefunde besser sichtbar machen; die Einzelheiten können im JSON bleiben.

**Priorität:** Erst U1/U2 (falsches Grün), U3/U4 (falsche neue Befunde), U5 (kanonische
Config) und U6 (Policy-Widersprüche). U7 ist die wichtigste Produktentscheidung für
„Architektur-Guidelines umsetzen“; die übrigen Punkte verbessern Fehlerbehandlung und Adoption.

### Umgesetzt (Runde 3)

- **U1/U2:** Nicht existierende oder nicht analysierte Prüfpfade und unbekannte
  Select-/Ignore-Präfixe liefern Exit 2. JSON nennt Scope, Dateizahl und aktive Regeln;
  die Textausgabe nennt den eingegrenzten Scope.
- **U3:** Importbefunde werden nach Architektur-Kante statt Quelltext verglichen.
  Kommentare, Aliase und Formatierung erzeugen keine neuen Befunde; ein zusätzlicher
  identischer Import wird weiterhin erkannt. Alte Debt-Fingerprints bleiben lesbar;
  `debt --prune` aktualisiert bekannte Einträge, ohne neue Verstöße zu übernehmen.
- **U4:** Source-/Test-Roots werden beim Laden normalisiert, einschließlich `./`,
  abschließendem Slash und Windows-Trennzeichen.
- **U5:** `config show` serialisiert öffentliche Aliasnamen. Allowance-Fehler zeigen
  YAML-Pfade und öffentliche Keys ohne interne Pydantic-Union-Marker.
- **U6:** `context` benennt die geltenden Ausnahmen und verbleibenden Einschränkungen.
  JSON enthält `central`, `is_wiring`, Coverage und die Policy; zentrale Module bekommen
  ausdrücklich das Verbot von Feature-Imports.
- **U7:** `init` macht Third-Party- und Transitivitätsentscheidungen ausdrücklich sichtbar,
  ergänzt Beispiele für Einschränkungen und kennzeichnet den globalen Cross-Feature-Allow.
  `context` und README erklären diese Abdeckung. Die Policy bleibt vom Projekt bestimmt.
- **U8:** Debt-Dateiformat und Version werden validiert; beschädigte Dateien liefern
  einen Config-Fehler mit Dateipfad und Exit 2.
- **U9:** Geplante Dateien werden über vorhandene Source-Pakete eingeordnet. Ein
  Analysefehler verhindert nur die Befundzahlen, nicht das Architektur-Briefing.
- **U10:** Gezielte Feature-Ausnahmen prüfen beide Feature-Namen mit Tippfehlerhinweis.
- **U11:** SMT106 zeigt einen tatsächlich erkannten Composition-Root-Import; Regeldoku
  neu generiert.
- **Ergonomie:** Dotted Modules in `context`, `--config` auch nach Subcommands und
  zusammengefasste SMT305-Meldungen im Text; einzelne Befunde bleiben im JSON erhalten.

**Validierung:** 284 Tests bestanden, 1 übersprungen; Ruff, Mypy, eigener Smelt-Check
und sämtliche Pre-Commit-Checks grün. Alle 27 ursprünglichen Prompster-Probes erneut
ausgeführt, zusätzlich Framework-Policy, korrektes SMT106-Beispiel und Selector-Hinweis
geprüft. Die echte Prompster-Baseline bleibt bei 75 Fehlern und 22 Warnungen; die
15 zusätzlichen Fehler durch `./` und die Altlast nach einem Import-Kommentar entfallen.

## Release-Check (2026-10-06)

- **Behoben:** Der öffentliche Pre-Commit-Hook übergab alle passenden Dateinamen an
  `check`. Nicht konfigurierte Python-Skripte verursachten dadurch Exit 2; außerdem
  konnte die Eingrenzung auf einen geänderten Import-Target eingehende Verstöße in
  anderen Modulen übersehen. Der Hook prüft jetzt das gesamte Projekt wie der lokale
  Hook. Ein Regressionstest umfasst beide Fälle; `pre-commit try-repo` prüft die echte
  Installation des öffentlichen Hooks in einem separaten Git-Projekt.
- **Behoben:** CI baut Wheel und Source-Archiv und installiert das Wheel auf Python
  3.12 außerhalb des Checkouts für CLI-Smoke-Checks. Paketmetadaten enthalten jetzt
  Repository-, Issue- und Dokumentationslinks.
- **Behoben:** Installations- und CI-Beispiele verwenden die Git-Quelle. Der PyPI-Name
  `smelt` gehört zu einem anderen Projekt (https://pypi.org/project/smelt/); die bisherigen
  `uvx smelt`-Beispiele installierten dieses statt unserer Architekturprüfung.
  Auch der Hinweis bei neuerer Python-Syntax empfiehlt keine Installation des fremden
  Pakets mehr.
- **Behoben:** Ein beim Windows-Hook-Test gefundenes UTF-8-BOM wurde als Syntaxfehler
  behandelt, obwohl Python diese Quelldatei akzeptiert. Der Leser entfernt jetzt das
  BOM; ein Regressionstest prüft weiterhin den Importbefund auf der korrekten Zeile.
- **Offen vor PyPI-Release:** Eigenen Distributionsnamen wählen und anschließend
  Metadaten, Versionsabfrage und Installationsbeispiele darauf umstellen. CLI-Befehl
  und Python-Paket können weiterhin `smelt` heißen.
- **Offen für einen Open-Source-Release:** Lizenz festlegen und als Datei sowie
  Paketmetadatum aufnehmen. Aktuell ist keine Lizenz deklariert.
- **Release-Schritte:** Nach erfolgreicher CI den PR mergen, den im README verwendeten
  Tag `v0.1.0` erstellen und die Distribution veröffentlichen. Aktuell existiert weder
  dieser Tag noch ein GitHub-Release; es wurde nichts veröffentlicht.

**Lokal geprüft:** Wheel aus dem Source-Archiv gebaut, beide Archive und `py.typed`
kontrolliert, Installation mit ausschließlich Laufzeitabhängigkeiten auf Python 3.12
und 16 CLI-Smoke-Checks erfolgreich. 286 Tests bestanden, 1 übersprungen; Ruff, Mypy,
eigener Smelt-Check und Pre-Commit-Checks grün. CI am vorherigen PR-Stand war auf
Python 3.12–3.14 unter Linux, macOS und Windows vollständig erfolgreich.

## Runde 4: Adoption in einem FastAPI/Dishka-Workspace (2026-10-07)

Externes Feedback zu smelt `598131e` in einem uv-Workspace mit FastAPI, fastapi-canon,
Dishka, Feature-Layern, Libraries und gespiegelten Tests. Ergebnis dort: 81 Befunde in
63 Dateien (20 SMT101, 40 SMT102, 2 SMT104, 2 SMT105, 1 SMT106, 16 SMT401), aber
**keine 81 unabhängigen Bugs**: Viele gehen auf dieselbe Fassade, dieselbe fehlende
Cross-Feature-Erlaubnis oder ein altes Testlayout zurück.

Maßstab für die Auswahl: hilft es bei jedem weiteren Projekt, die Kernziele Spiegelung
und Grenzen zu prüfen, und lässt es sich mit wenig neuer Oberfläche umsetzen?

### Bewertet

| # | Vorschlag | Entscheidung |
|---|---|---|
| 1 | JSON-Fehlerobjekt statt stderr-Text bei `--format json` | umgesetzt |
| 2 | Testort-Vorschläge nur mit Beleg statt Namensähnlichkeit | umgesetzt |
| 3 | App-Factory über Paket-Fassaden als Composition Root erkennen | umgesetzt |
| 4 | Befunde nach Abhängigkeitskante gruppieren | umgesetzt, schlank |
| 5 | Zyklen kennzeichnen, die nur unter `TYPE_CHECKING` bestehen | umgesetzt |
| 6 | Coverage-Tabelle der Policy | **bewusst nicht**: U7 macht die Entscheidungen schon in `init` und `context` sichtbar; eine weitere Tabelle wäre vor allem mehr Ausgabe |

### Umgesetzt (Runde 4, PR #7)

- **1:** Mit `--format json` liefert Exit 2 ein Dokument
  `{"schema_version": 1, "status": "error", "error": {...}}` mit `kind`
  (`usage`/`config`/`analysis`), `message`, abgelehnter Eingabe (`option`, `value`) und
  bei Config-Fehlern Datei und YAML-Location jedes Problems. Unbekannte Selektoren und
  Prüfpfade sind `InvalidOptionError` (usage) statt allgemeiner Analysefehler. Auch
  argparse-Fehler (`--fail-on bogus`, unbekannte Flags) kommen als JSON, sobald
  `--format json` auf der Kommandozeile steht; ein ungültiger `--format`-Wert selbst
  bleibt argparse-Text, weil das gewünschte Format dann unbekannt ist.
- **2:** Ursache der falschen Vorschläge war `did_you_mean` auf Dateinamen: das gemeinsame
  `.py` hob `memory.py`/`errors.py` und `commands.py`/`channels.py` über die
  Ähnlichkeitsschwelle. Verglichen werden jetzt Modulnamen; importiert der Test
  First-Party-Code, kommen nur importierte Module in Frage. Heißt der Test wie ein Paket
  neben dem Spiegelpfad, zeigt SMT401 auf dessen Paket-Testort, außer alle Imports
  kommen von woanders. Die Meldung lautet „has no source module at its mirrored path“,
  der Hinweis sagt ausdrücklich, dass das Verhalten trotzdem existieren kann, und nennt
  Verschieben, Zusammenführen, Topic-Suffix, Löschen und `tests.unmirrored`.
- **3:** `init` folgt von `app.py` aus Paket-Fassaden (`__init__.py`, nur Imports im
  eigenen Paket, höchstens drei Ebenen) bis zum Wiring. FastAPI- oder Dishka-Imports
  allein machen kein Modul zum Composition Root. Dabei behoben: relative Imports in einem
  Composition Root, der ein Paket ist (`bootstrap/__init__.py`), wurden vom Root-Paket
  aus aufgelöst; `from . import app` zählte fälschlich `shop/app.py`.
- **4:** Jeder Import-Befund trägt `edge` (`billing.application -> voice.domain`, zentraler
  oder Shared-Eintrag, Third-Party-Paket; bei SMT104 der Zyklus). Die Textausgabe endet
  mit den fünf häufigsten Kanten mehrerer Befunde. SMT102 nennt den passenden
  `cross_feature.allow`-Eintrag und benennt ihn als Policy-Entscheidung. README beschreibt
  den Ablauf: jede Kante einmal entscheiden, Rest mit `smelt debt` als Baseline.
  `edge` geht nicht in Debt-Fingerprints ein.
- **5:** SMT104 hängt `(type checking only)` an, wenn eine Kante des Zyklus nur unter
  `if TYPE_CHECKING:` importiert wird, mit `expected.type_checking_only` und dem Hinweis,
  dass der Zyklus beim Import nicht fehlschlagen kann.

**Gegenprobe an Prompster** (frischer Klon, Config aus `init`, Original unberührt):
SMT401/SMT104 bleiben bei 44 Befunden; vier geratene Vorschläge entfallen
(z. B. `spotify_service` → `spotify_search.py`, `guardrails_post_run` → `guardrails.py`),
der echte Tippfehler `fernet_token_cypher.py` bleibt. Die 75 Fehler bündeln sich auf
wenige Kanten, allein 13 auf `* -> auth.presentation`:

```
Repeated dependency edges (often one decision or fix each; JSON "edge"):
  8x SMT102 session.presentation -> auth.presentation
  5x SMT102 oauth_connections.presentation -> auth.presentation
  3x SMT102 auth.application -> user.domain
```

`app.py` war bei Prompster schon vorher Composition Root (importiert `lifespan` direkt);
der Fassaden-Fall ist durch Tests abgedeckt.

**Validierung:** 306 Tests bestanden, 1 übersprungen; Ruff, Mypy, eigener Smelt-Check
und Pre-Commit-Checks grün; CI auf Python 3.12–3.14 unter Linux, macOS und Windows.

## Runde 5: Rest-Befunde aus dem FastAPI/Dishka-Workspace (2026-10-08)

Externes Feedback zu smelt 0.1.2 im selben uv-Workspace (FastAPI, fastapi-canon, Dishka,
Feature-Layer, Libraries, Namespace-Paket für E2E). Mit der geprüften Config: 86 Befunde
in 69 Dateien (20 SMT101, 40 SMT102, 2 SMT104, 2 SMT105, 1 SMT106, 21 SMT401), keine
Suppressions, keine Debt-Baseline. Maßstab wie in Runde 4, dazu ausdrücklich: nicht auf
dieses Projekt zuschneiden. Jede Heuristik ist generisch (Strings `"modul:attr"`,
Re-Exports über `__init__`, Namen in Definitionen) und durch Fixtures abgedeckt, die das
Projekt nicht nachbauen.

### Bewertet

| # | Vorschlag | Entscheidung |
|---|---|---|
| 1 | `init --config` beachten oder ablehnen | umgesetzt: `--config` ist der Zielpfad |
| 2 | Namespace-Pakete in Workspace-Mitgliedern | umgesetzt, mit Status pro Mitglied |
| 3 | Composition Root über Assembly-Fassaden | umgesetzt, unsichere Fälle nur als Kandidat |
| 4 | Strukturierte Testkandidaten statt Wiederholungsabsatz | umgesetzt |
| 5 | Gruppierte Befunde, transitive Ursachen | umgesetzt, schlank |
| 6 | Vergleichs-Provenienz und Coverage im Report | umgesetzt (Runde 4 hatte die Coverage-Tabelle abgelehnt; als JSON-Feld statt Textausgabe ist sie günstig und beantwortet „sauber oder nur nichts Neues?“) |

### Umgesetzt (Runde 5)

- **1:** `smelt init --config custom.yaml` (vor oder nach dem Subcommand) schreibt genau
  diese Datei; nur sie wird geprüft und mit `--force` überschrieben, ihr Verzeichnis ist
  die Projektwurzel der Inferenz. Ein bestehendes `smelt.yml` wird ohne `--config` an Ort
  und Stelle überschrieben statt gelöscht.
- **2:** Mitglieder-Erkennung in `engine/workspace.py`. Ein Verzeichnis ohne eigene
  Python-Datei zählt als Namespace-Paket, wenn es reguläre Pakete enthält und unter `src/`
  liegt oder `tool.uv.build-backend` `namespace`/`module-name` setzt; `deploy/` mit
  Skripten bleibt draußen. `tool.uv.workspace.exclude` wird beachtet. Die Zusammenfassung
  nennt jedes Mitglied mit Paketen oder Grund; die Config kommentiert übersprungene.
- **3:** `analysis/exports.py` löst Namen über Re-Exports auf (Hop-Limit, Zyklusschutz).
  `init` erkennt App-Factories auch über `"backend.app:app"`-Strings und folgt von jedem
  Composition Root den importierten Namen in ihre Definitionen (`FEATURES = (chat.feature,
  …)`). Ein Nicht-Fassaden-Modul, dessen Definition aus Wiring gebaut ist
  (`platform/feature.py` mit `ApiProvider`), wird Wiring; bloße Framework-Importe oder ein
  Import des Providers ohne Verwendung reichen nicht. Unklassifizierte Pakete, die nur der
  Root nutzt (`backend.rpc`), erscheinen als auskommentierter Kandidat mit Kette und
  Konfidenz (medium/low). Config und Zusammenfassung zeigen die Kette jedes Eintrags.
- **4:** SMT401 folgt Test-Importen durch Fassaden. `expected` trägt `subject`
  (module/package/ambiguous/unknown) und bei Verschiebungen `evidence` (imports/name).
  Ohne belastbaren Ort listet `expected.candidates` die importierten Module des eigenen
  Mitglieds (nächstliegende zuerst) mit importierten Namen, Fassade und Spiegelpfad.
  `test_bootstrap.py` mit `application/bootstrap.py` und `infrastructure/bootstrap.py`
  bleibt bewusst unentschieden. „did you mean“ braucht einen Import des Moduls und eine
  Ähnlichkeit ab 0,85 (Tippfehler liegen darüber, `spotify_service`/`spotify_search` mit
  0,83 darunter); die Suffix-Regel verlangt, dass der Präfix den Paketpfad nennt.
- **5:** Jeder Befund hat eine stabile `id`; JSON `groups` (deterministisch sortiert)
  fasst `facade` (transitive Befunde, die ein Modul weiterreicht), `edge` (mit direct/
  transitive), `cycle` (Zeugenpfad, type-checking-only) und `test_move` zusammen. Der Text
  nennt die Fassaden. SMT104-Zeugen verbinden ihre Hops innerhalb eines Knotens, wo ein
  Modulpfad existiert (`features → platform.auth → platform.auth.guard → features`).
- **6:** JSON `scope.mode` und `comparison` (base, merge_base, compared_revision, head);
  `coverage` mit Wurzeln, nicht analysierten Workspace-Mitgliedern, Klassifikationszahlen,
  Ausnahmen, Third-Party-Policy pro Layer, Import-Einstellungen, Cross-Feature-Einträgen
  als Felder und `policy_hash`. SARIF-Runs tragen Modus, Vergleich und Hash; `context
  --format json` die Import-Einstellungen, `cross_feature_policy` und den Hash.

**Gegenprobe am Projekt** (Kopie ohne Config, Original unberührt): `init` findet jetzt
`e2e_stack` (Namespace), `backend.app` (über `uvicorn.run("backend.app:app")`),
`backend.platform.feature` und `telegram.feature` als Wiring und schlägt `backend.rpc` als
Kandidat vor, also fast die geprüfte Config. Mit der geprüften Config bleiben es 86
Befunde mit identischer Verteilung; `test_commands.py` zeigt nun auf
`application/commands/`, `test_obstore_storage.py` über die Storage-Fassade auf
`storage/impl/`.

**Gegenprobe an Prompster:** gleiche Befundzahlen (75 Fehler, 22 Warnungen); `init`
liefert dieselben Roots plus Begründung. Geänderte SMT401-Meldungen nur dort, wo Tests den
Paketpfad ausschreiben und über Fassaden importieren (`test_session_domain_views.py` →
`test_views.py`); die in Runde 4 entfernten Ratevorschläge bleiben weg.

**Validierung:** 348 Tests bestanden, 1 übersprungen, an jedem Commit einzeln grün;
Ruff, Mypy, eigener Smelt-Check und Pre-Commit-Checks grün.
