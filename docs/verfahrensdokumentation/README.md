# Verfahrensdokumentation

Verfahrensdokumentation nach GoBD Rz. 151–155 (GoBD in der Fassung des BMF-Schreibens vom
14.07.2025, BStBl 2025 I S. 1502) für das Programm **invoice-of-reason**, mit dem
Ausgangsrechnungen erstellt und archiviert sowie Eingangsbelege erfasst, geprüft und archiviert
werden. Sie richtet sich an einen sachverständigen Dritten (Rz. 148) und beschreibt, wie die
Ordnungsvorschriften der §§ 145–147 AO im Verfahren umgesetzt sind.

> **Hinweis:** Diese Dokumentation beschreibt das Verfahren eines einzelnen Betriebs. Sie ist von
> keiner Steuerberatung, Prüfstelle oder Finanzbehörde geprüft und keine Vorlage mit
> Gewähr für andere Nutzer. Siehe Haftungsausschluss in der [README](../../README.md#disclaimer).

## Gliederung (Rz. 153)

| Teil | Inhalt | Ort |
|---|---|---|
| 1. [Allgemeine Beschreibung](1-allgemein.md) | Zweck, Geltungsbereich, Systemübersicht, Abgrenzung | öffentlich |
| 2. [Anwenderdokumentation](2-anwender.md) | Arbeitsabläufe: Rechnung, Zahlung, Storno, Belege, Fristen | öffentlich |
| 3. [Technische Systemdokumentation](3-technik.md) | Datenmodell, Feldbedeutungen, Unveränderbarkeit, Hash-Kette, Formate | öffentlich |
| 4. [Betriebsdokumentation](4-betrieb.md) | Installation, Versionen, Datensicherung, Wiederherstellung, Datenzugriff | öffentlich |
| 5. [Internes Kontrollsystem](5-iks.md) | Kontrollen, Häufigkeit, Protokollierung | öffentlich |
| 6. Betriebsspezifischer Teil | Unternehmen, Verantwortliche, weitere Systeme, Belegfluss außerhalb des Programms, Scan-Anweisung, Sicherungsziel, durchgeführte Kontrollen | **nicht öffentlich**, siehe [Vorlage](6-betrieb-vorlage.md) |

Teile 1–5 beschreiben das Programm und gelten für jede Installation. Sie liegen im öffentlichen
Quellcode-Repository. Teil 6 enthält Angaben zum konkreten Unternehmen und wird getrennt und nicht
öffentlich geführt (privates Repository oder Konfigurationsverzeichnis auf dem Server). Die Vorlage
nennt die Punkte, die dort beantwortet werden müssen.

## Versionierung und Aufbewahrung (Rz. 154)

- Teile 1–5 sind mit git versioniert; jede Änderung ist mit Datum, Autor und Inhalt nachvollziehbar
  (`git log -p docs/verfahrensdokumentation`). Die Dokumentation wird im selben Commit geändert wie
  das Programm, dessen Verhalten sie beschreibt.
- Welche Programmversion (git-Commit) wann in Betrieb war, protokolliert das Programm selbst
  (Tabelle `system_events`, Eintrag `version`, siehe Teil 3 und 4). Zu jeder dort genannten Version
  gehört der Stand dieser Dokumentation im selben Commit.
- Teil 6 wird ebenfalls versioniert (git oder datierte, nicht überschriebene Fassungen).
- Beide Teile werden aufbewahrt, solange Unterlagen aufbewahrt werden, zu deren Verständnis sie
  nötig sind.

## Stand

Diese Fassung beschreibt den Stand nach Umsetzung der Punkte 1–5 und 7–10 sowie 16–23 aus
`docs/GOBD.md`: Änderungsprotokoll, Programmidentität, Kontrollprotokoll, Datenexport, Empfang von
E-Rechnungen, Vollständigkeits- und Fristenprüfungen, Hash-Kette, Stornorechnung, Umsatzgrenzen
des § 19 UStG mit außerhalb erfassten Umsätzen, § 13b-Kennzeichnung und Anlagegüter.

Dort offene Punkte sind hier nicht beschrieben, solange sie nicht umgesetzt sind: die Auslagerung
der Datensicherung (Teil 4.4 nennt sie als offen), das Ausstellen von E-Rechnungen, ein eigener
Nur-Lese-Zugang für Prüfer und die Festschreibung eines Jahres nach Abgabe der EÜR.
