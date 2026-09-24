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
- Bei `layout: mirror` entscheidet nur der Pfad. `tests/<dirs>/test_<x>.py` (oder
  `<x>_test.py`) ist in Ordnung, wenn `<root>/<dirs>/<x>.py` existiert oder `<x>` das Paket
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
4. Lose Module im Feature melden (#5) und Hints im `--changed`-Modus zeigen (#8).
5. Kleinkram und Bugs (#4, #11, #13, #14, #16).
