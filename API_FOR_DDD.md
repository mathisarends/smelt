# Smelt als DDD-Guardrail: Prompster als Use Case

Stand: 2026-09-24. Untersucht wurde `../prompster` lesend; dort wurde keine
Konfiguration angelegt und kein Code geändert. Die folgenden Ergebnisse stammen
aus einem In-Memory-Check mit der lokalen Smelt-Version.

## Ausgangslage

Prompster ist ein `uv`-Workspace mit den Python-Paketen `backend`, `agent` und
`tokens`. Im Backend liegen Features unter `backend.features.<name>`; die meisten
haben `domain`, `application`, `infrastructure` und `presentation`. Daneben gibt
es zentrale Infrastruktur und Präsentation direkt unter `backend` sowie das
eigenständige `agent`-Paket.

Eine mögliche erste Konfiguration (als `prompster/smelt.yml`) ist:

```yaml
version: 1
project:
  root_packages: [backend, agent, tokens]
  source_roots: [backend/src, libs/agent/src, libs/tokens/src]
  test_roots: [backend/tests, libs/agent/tests]

architecture:
  features:
    root: backend.features
  shared: [backend.shared]
  composition_root: [backend.main, backend.lifespan]
  layers:
    domain: {path: domain}
    application: {path: application, may_depend_on: [domain]}
    infrastructure:
      path: infrastructure
      may_depend_on: [domain, application]
    presentation:
      path: presentation
      may_depend_on: [domain, application]
  cross_feature:
    default: deny
    allow: ["application -> application"]

tests:
  layout: none
```

Diese Policy ist bewusst streng und noch keine Empfehlung, sie unverändert in
Prompsters CI zu aktivieren. Sie macht zunächst die tatsächlichen Kopplungen
sichtbar. Insbesondere sind Importe in Feature-lokalen `infrastructure/di.py`
oft Verdrahtung und brauchen eine eigene Entscheidung.

## Gefundene und behobene Fehler

### Importgraph übersah `backend.features.*`

`backend` ist ein reguläres Paket, aber `backend/features` hat keine
`__init__.py`. Smelts Datei-Index fand die Feature-Dateien; Grimp übersprang
jedoch dieses Namespace-Verzeichnis innerhalb des regulären Pakets. Vor dem
Fix enthielt der Graph 85 Module und keine Importdetails für Feature-Module.
Selbst eine strenge Cross-Feature-Policy meldete **0** Verstöße, obwohl etwa
`backend.features.user.application.user_service` Auth importiert. Das war ein
gefährlich falsches grünes Ergebnis.

Smelt übergibt Grimp jetzt dieselben Module und Namespace-Verzeichnisse, die
sein eigener Datei-Index entdeckt. Zusätzlich bricht die Analyse mit einem
Fehler ab, falls der Importgraph künftig wieder Quelldateien auslässt. Der
Regressionstest deckt genau den gemischten Paketfall ab. Danach umfasst der
Graph 254 Module einschließlich Paketen und erfasst 698 Importe aus
`backend.features.*`. Mit der obigen Policy meldet Smelt 42 `SMT102`- und
6 `SMT101`-Verstöße sowie einen Importzyklus `SMT104` im `agent`-Paket.

### `SMT408` verwechselte Routen mit Test-Hilfs-APIs

Im ersten vollständigen Lauf waren sieben FastAPI-Handler angeblich „nur von
Tests verwendet“. Die Handler werden aber über Dekoratoren wie `@router.get`
registriert. `SMT408` überspringt jetzt entsprechend registrierte HTTP- und
WebSocket-Routen. Ein Regressionstest stellt sicher, dass eine echte, nur von
Tests importierte Hilfsfunktion weiterhin gemeldet wird. Im erneuten
Prompster-Lauf sind die sieben Meldungen verschwunden.

## Nutzen und offene API-Fragen

- **Feature-Grenzen sind nach dem Importgraph-Fix nützlich.** Die 42
  `SMT102`-Meldungen zeigen echte direkte Kopplungen, zum Beispiel
  `auth.application -> user.domain` und
  `session.presentation -> auth.presentation`. Ob sie erwünscht sind, ist eine
  Architekturentscheidung; Smelt liefert dafür konkrete Fundstellen.
- **`cross_feature.allow` ist zu grob für eine gewachsene Anwendung.**
  `"presentation -> presentation"` erlaubt diesen Import zwischen *allen*
  Feature-Paaren. Für Prompster wäre eine nach Quell- und Zielfeature
  eingeschränkte Ausnahme hilfreich, etwa nur `session -> auth` für bestimmte
  Layer-Paare. Globale Ausnahmen schwächen sonst die gesamte Policy.
- **Feature-lokale DI-Module passen nicht sauber zur Composition-Root-API.**
  `backend.features.auth.infrastructure.di` verdrahtet andere Features und
  importiert dabei auch Präsentationsmodule. `composition_root` akzeptiert
  momentan nur konkrete Modulnamen; ein Muster für
  `backend.features.*.infrastructure.di` oder eine eigene Kategorie für
  Verdrahtungsmodule würde die Absicht klarer ausdrücken. Pauschales Erlauben
  von `infrastructure -> presentation` wäre für alle anderen Module zu breit.
- **Ein Feature-Root reicht für den Workspace nicht aus.** `agent` und `tokens`
  werden als Root-Pakete gefunden, aber nicht demselben Feature-/Layer-Modell
  zugeordnet. Der vollständige Lauf meldet 39 `SMT305`-Hinweise für solche und
  andere nicht klassifizierte Module. Mehrere Architektur-Sektionen pro Paket
  oder eine bewusst schmalere Analyse-Scope wären hilfreich.
- **Ports sind nicht einheitlich verortet.** Prompster hat ABC-Ports teils im
  `domain`, teils in `application`, bei Session als `ports/`-Paket. Eine globale
  `roles.port.file: ports.py`-Regel wäre für dieses Projekt künstlich. Die
  optionale Rollen-API sollte solche Mischformen ohne breite False Positives
  unterstützen oder zunächst weggelassen werden können.
- **Adoption braucht abgestufte Regeln.** Im vollständigen Lauf sind 43
  `SMT403`-Meldungen Zugriffe auf private Attribute in Tests und 12 `SMT406`
  Interaktions-Assertions. Das kann wertvoll sein, überdeckt beim ersten DDD-
  Check aber die Importgrenzen. Ein dokumentiertes Einstiegsprofil oder eine
  einfache Auswahl von Regelgruppen würde den Einstieg erleichtern.

Priorität für weitere Arbeit: zuerst gezielte Cross-Feature-Ausnahmen und eine
ausdrückliche Behandlung von Feature-lokaler Verdrahtung; danach mehrere
Architektur-Scopes für Monorepos. Die beiden nachgewiesenen Analysefehler oben
sind bereits im Code behoben.
