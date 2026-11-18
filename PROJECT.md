# agent-architecture – Projektbeschreibung

## Was das Projekt ist
Eine lokale, visuelle Entwicklungsumgebung für Multi-Agent-Systeme (MAS).
Sie liest eine bestehende Codebasis, macht ihre Architektur als interaktive
Karte sichtbar und hilft dabei, das System zu erweitern, zu prüfen und
reproduzierbar zu betreiben.

Leitidee: Die GUI ersetzt den Code nicht. Der Entwickler behält volle Code Ownership.
Motto: "Build visually. Understand deeply. Own the code."

Die Karte ist nicht das Ziel, sondern das Fundament. Darauf bauen Datenfluss,
Governance und Reproduzierbarkeit auf. Ein reiner Repo-Mapper beantwortet
"Was macht dieses Repo?". Dieses Projekt beantwortet zusätzlich "Darf dieses
System so laufen, und kann ich nachweisen, was es getan hat?".

## Zielgruppe
- V1 für Engineers und Forschende, die MAS bauen oder pflegen
- Vibecoder können es nutzen, eine vereinfachte Ansicht kommt erst in V2

## Scope
V1
- Mapping der Architektur (Agenten, Verbindungen, Schemas)
- Datenfluss (welche Daten gehen von wo nach wo)
- Hinzufügen neuer Agenten und Dateien per GUI inklusive Code-Skeletten
- Governance (Policy Checks an der Architektur, Audit Trails)
- Reproduzierbarkeits-Checks (Provenance, Run Manifests, Replay, Re-Execution)

V2
- Einfaches Monitoring
- Vereinfachte Ansicht in einfacher Sprache für Vibecoder

## Architekturentscheidungen
- Local-first, kein Postgres. Architekturmodell als `architecture.yaml` im Repo
  des Nutzers, Runs in SQLite (`.architecture/runs.db`)
- Monorepo mit drei Einheiten: `core` (reine Modelle, kein IO),
  `backend` (FastAPI), `frontend` (Vite, React, TypeScript, React Flow, dagre)
- Generator erzeugt plain Python ohne Framework-Abhängigkeit
  (Jinja2 für neue Dateien, LibCST für Änderungen an bestehenden)
- Plan/Apply-Pattern mit Ownership-Tracking und Drift Detection (Impact-Analyse
  entfernt, Datenfluss wird später auf dem Digest neu aufgebaut)
- Provenance pro Element: confirmed, declared, inferred, unknown

## Inspector (geplante Neuausrichtung)
Bisher gibt es die Adapter declarations (`@agent`-Decorator) und project_local
(vom Nutzer geschriebene `.architecture/inspect.py`). Der LangGraph-Adapter ist
gelöscht. Wie es mit den Adaptern weitergeht, wird separat besprochen.

Problem: Niemand schreibt eine eigene inspect.py, und die Heuristik allein findet
bei eigenen Architekturen zu wenig.

Neue Richtung:
- Ein LLM liest die Codebasis und schlägt die Karte bzw. die Mapping-Regeln vor
- Der Nutzer bestätigt per Klick, danach ist die Karte deterministisch und reproduzierbar
- LLM-Ergebnisse landen als `inferred`, der deterministische Teil (AST, Imports,
  Aufrufe) hebt sie auf `confirmed`
- Das LLM liefert pro Agent und Verbindung eine kurze Beschreibung in einfacher
  Sprache mit (in V1 als Tooltip, Grundlage für die V2-Ansicht)
- Der Ablauf muss bei jeder Codebasis funktionieren, unabhängig von Framework
  oder eigener Architektur, ohne framework-spezifische Sonderwege

### Geplanter Ablauf (Entwurf, Stand 2026-11-13)
Das LLM schreibt keine `inspect.py`, sondern eine deklarative Regeldatei
`.architecture/mapping.yaml`. Es läuft nur einmal beim Entdecken, danach ist
jeder `inspect` reines AST ohne LLM.

```
 EINMALIG: agent-architect discover <project>         (braucht LLM)
 ───────────────────────────────────────────────────────────────────
  Codebasis
     │
     ▼
  1. Fakten-Digest        deterministisch, über ctx (AST):
     │                    Klassen, Basen, Decorators, Signaturen,
     │                    Aufrufe, Imports, Literal-Listen
     ▼
  2. Discovery-Agent      LLM liest den Digest, holt gezielt Dateiausschnitte
     │                    und schlägt Regeln mit Beleg (file:line) und
     │                    Kurzbeschreibung vor
     ▼
  3. Validator            deterministisch: Schema ok? Trifft jede Regel
     │                    mindestens ein Symbol? Existiert jeder Beleg?
     │                    Fehler gehen zurück an den Agenten (max. N Runden)
     ▼
  4. Review               Nutzer bestätigt oder verwirft pro Regel per Klick
     │                    (GUI) oder per CLI
     ▼
  .architecture/mapping.yaml                       → wird committet

 JEDES MAL: agent-architect inspect <project>         (kein LLM)
 ───────────────────────────────────────────────────────────────────
  mapping.yaml ─► rules-Adapter ─┐
  @agent       ─► declarations  ─┼─► merge.py ─► architecture.yaml
  inspect.py   ─► project_local ─┘   (confirmed > declared > inferred)
```

Warum Regeln statt generierter `inspect.py`:
- Sicherheit: `project_local` führt `inspect.py` per `exec` aus, Regeln sind nur Daten
- Prüfbarkeit: Der Validator testet jede Regel einzeln
- Review per Klick: Regeln lassen sich einzeln annehmen oder ablehnen
- Diffbarkeit: Bei Codeänderungen schlägt `discover` einen Regel-Diff vor
- `inspect.py` bleibt als Ausweg für Fälle, die die Regelsprache nicht abdeckt

Regeldatei (Skizze für `prozess-mas`), aufgebaut auf den vorhandenen
`ctx`-Primitiven:

```yaml
version: 1
meta: {digest_hash: "sha256:...", model: claude-opus-5-5, prompt_version: 1}
agents:
- id: agents
  status: accepted              # proposed | accepted | rejected
  evidence: [core/base.py:6]
  inherits: core.base.BaseAgent
  input_schema: {param_of: run}
  output_schema: {returns_of: run}
  tools: {attr: TOOLS}
connections:
- id: pipeline
  status: accepted
  sequence: pipeline.PIPELINE   # Reihenfolge aus einer Literal-Liste
unresolved:
- id: dynamic-tools
  calls: core.registry.register
  kind: dynamic_tool_registration
descriptions:                   # LLM-Texte, eingefroren, für Tooltips
  CriticAgent: "Prüft einen Vorschlag und nimmt ihn an oder lehnt ihn ab."
```

Determinismus:
- LLM nur in `discover`; `inspect`, `drift`, `plan` und CI brauchen keinen API-Key
- Regeln und Beschreibungen liegen im Repo: gleicher Code und gleiche Regeln
  ergeben dieselben Ids und dieselbe `architecture.yaml`
- Der `meta:`-Block in `mapping.yaml` hält Hash des Fakten-Digests, Modell und
  Prompt-Version fest (keine eigene Lock-Datei)
- Trifft eine Regel nichts mehr oder gibt es nicht abgedeckte Kandidaten, meldet
  `inspect` das als `unresolved` (`rule_stale`, `uncovered_candidate`), nichts
  wird still neu geraten

Provenance:
- Regel vorgeschlagen, nicht bestätigt: `inferred`
- Regel vom Nutzer bestätigt: `declared` (Vorschlag, noch offen)
- Zusätzlich per AST belegt: `confirmed`

Einordnung im Code:
- `core/inspector/rules.py`: Regelsprache und rein deterministischer Adapter für `mapping.yaml`
- `core/inspector/calls.py`: welche Aufrufe einen sicheren Aufgerufenen haben
- `core/discovery/`: neue Schicht über `inspector` (Digest, Agenten-Schleife,
  Validator, Provider-Schnittstelle); nur `backend` importiert sie
- `pip install agent-architect` läuft ohne LLM, `agent-architect[llm]` bringt `discover` mit
- Backend-Endpunkte für Vorschlag und Bestätigung; im Canvas ein Review-Panel,
  unbestätigte Elemente gestrichelt

Stand (2026-11-16):
- Umgesetzt: Fakten-Digest, Regelsprache mit `rules`-Adapter, Validator,
  Discovery-Agent, CLI-Befehle `digest` und `discover`, `rule_stale`
- `examples/prozess-mas/.architecture/mapping.yaml` liefert dieselbe Karte wie
  die handgeschriebene `inspect.py`
- Offen: Review in CLI und GUI (bisher `status` von Hand in `mapping.yaml`
  setzen), Backend-Endpunkte, `uncovered_candidate`, ein Lauf mit echtem LLM
  (Tests nutzen handgeschriebene Antworten)

### Validator (Plan, Stand 2026-11-16)
`core/discovery/validate.py` prüft einen Regelvorschlag deterministisch gegen
den Code. Er bleibt eine Datei.

Schweregrade:
- Fehler: Die Regel ist nicht vertrauenswürdig. Sie geht zurück an den Agenten
  und wird verworfen, wenn sie bleibt
- Warnung: Die Regel funktioniert, aber etwas ist nicht statisch lesbar. Die
  Regel bleibt und die Warnung erscheint im Review

Schritte:
1. Schweregrad und `kind` an jedem Befund (umgesetzt). Fehler: doppelte Id,
   Regel trifft nichts, Beleg ungültig oder fehlend, Beschreibung ohne Agent.
   Warnung: Wert nicht statisch lesbar, einzelne Verbindung zeigt ins Leere
2. Belege inhaltlich prüfen (umgesetzt). Jede Regel braucht mindestens einen
   Beleg. Er muss in einer Datei liegen, in der die Regel trifft oder in der das
   benannte Symbol definiert ist. Ausnahme: explizite `edges`, dort kann der
   Beleg überall liegen
3. Qualitäts-Warnungen (offen, erst nach dem ersten echten LLM-Lauf): zu breite
   Regel (getroffene Klasse ist Basis anderer getroffener Klassen), Lücke
   (`uncovered_candidate`), isolierter Agent, Selbstverbindung, zwei Regeln für
   denselben Agenten
4. Vergleich mit dem vorherigen Mapping (offen): welche Agenten und
   Verbindungen der neue Vorschlag verliert oder gewinnt

Ebenfalls offen: CLI-Befehl `validate <project>` für die CI (Exit-Code 1 bei
Fehlern).

Bewusst nicht im Validator: Trefferqualität gegen die Golden Files (eigener
Auswertungslauf) und das Hochstufen auf `confirmed` (offene Entscheidung unten).

### Review-Phase (Stand 2026-11-16)
Reviewt wird pro Regel, nicht pro Agent oder Verbindung. Eine Entscheidung setzt
nur das Feld `status` in `mapping.yaml`.

Grundsätze:
- Die Entscheidung trifft nur der Nutzer. Das Modell schlägt vor, der Validator
  prüft, annehmen oder ablehnen kann nur ein Mensch
- Wer eine Regel annimmt, bestätigt alles, was sie liefert, auch künftige Treffer
- Annehmen hebt die Elemente der Regel von `inferred` auf `declared`
- Abgelehnte Regeln bleiben in der Datei, damit `discover` sie nicht erneut
  vorschlägt
- Eine Regel mit Fehler kann nicht angenommen werden
- Ein Review ändert den Digest-Hash nicht und löst keinen neuen LLM-Lauf aus

Umgesetzt:
- `core/inspector/rules.py` merkt sich pro Regel die Ids der erzeugten Elemente
- `core/discovery/review.py`: `review(project)` liefert pro Regel Status, Text,
  erzeugte Elemente, Belege und Befunde; `decide(project, rule_ids, status)`
  setzt den Status und speichert
- CLI: `review <project>` listet die Regeln, `--accept ID...`, `--reject ID...`
  und `--accept-all` (nur wartende Regeln ohne Befund) entscheiden
- Backend: `GET /projects/{id}/mapping` und `POST /projects/{id}/mapping/status`
- GUI: `frontend/src/components/ReviewPanel.tsx` ist geschrieben, aber noch
  nicht eingebunden (nichts importiert die Datei). Sie bringt eigene Typen und
  Requests mit, damit sie für sich beurteilt werden kann

Offen:
- Entscheiden, ob CLI oder GUI der Hauptweg wird, dann `ReviewPanel` einbinden
  (dritter Tab neben "Unresolved" und "Drift", Hervorheben in der Karte, Belege
  im Code-Viewer öffnen)
- `discover` aus der GUI starten (Endpunkt und Knopf mit Rückfrage, weil der
  Lauf Geld kostet); vorerst synchron
- Regeln in der GUI bearbeiten: vorerst nur von Hand in `mapping.yaml`
- Was landet in `architecture.yaml`: Soll `inspect --save` auch unbestätigte
  (`inferred`) Elemente speichern oder nur angenommene?

Offene Entscheidungen:
- `mapping.yaml` statt generierter `inspect.py`? (Preis: Die Regelsprache muss
  bei ungewöhnlichen Architekturen wachsen)
- Bestätigte Regel als `declared`, oder `inferred` bis das AST sie belegt?
- Eigene LLM-Provider-Schnittstelle mit API-Key, oder zusätzlich ein
  Coding-Agent wie Claude Code, der die Regeldatei schreibt und den der
  Validator nur prüft?

## Testprojekte
Echte eigene Projekte, keine Platzhalter:
- Prozess-MAS (`agentic_process_optimization_system`), eigene ereignisgetriebene
  Architektur mit Phasen, Orchestrator und Gates
- ProtOsint (LangGraph)
- Fußball-Prognosesystem, um Architektur und Datenflüsse sichtbar zu machen
Erwartete Karten werden vorab von Hand geschrieben und dienen als Golden Files.

## Arbeitsweise
- Zuerst den bestehenden Code im Repo lesen, bevor etwas neu gebaut wird
- Vor größeren Codeänderungen nachfragen, wenn Annahmen über bestehenden Code,
  Projekte oder Daten nötig wären
- Keine Stellvertreter- oder Beispielprojekte auf Verdacht bauen
