# 5. Internes Kontrollsystem (Rz. 100–102)

Einzelunternehmen ohne Mitarbeiter: Funktionstrennung ist nicht möglich. Die Kontrollen sind
deshalb überwiegend technisch und werden, wo möglich, automatisch protokolliert. Umfang
entsprechend der Unternehmensgröße (Rz. 15).

## 5.1 Technische Kontrollen im Programm

| Kontrolle | Zweck | Wann | Nachweis |
|---|---|---|---|
| Zugangskontrolle | nur berechtigter Zugriff | jede Anfrage | Anmeldung erforderlich (Teil 4.2) |
| Eingabeprüfung | plausible Daten | bei jeder Eingabe | Fehlermeldung; ungültige Daten werden nicht gespeichert (Beträge > 0, max. 2 Nachkommastellen, gültige Daten, Pflichtfelder, Leistungszeitraum) |
| Storno mit Dokument | Der Kunde erhält ein Dokument, das eindeutig auf die Rechnung verweist (Rz. 64) | bei jedem Storno einer versandten Rechnung | Stornorechnung mit Nummer, Verweis und Grund; Verlaufseinträge in beiden Richtungen |
| Eindeutigkeit des Stornos | Keine doppelte oder widersprüchliche Stornierung | bei jedem Storno | Eindeutiger Index und Trigger in der Datenbank; Statusänderungen an stornierten Rechnungen gesperrt |
| Offene Erstattungen | Erstattungen werden ausgezahlt und erfasst | laufend in der Rechnungsliste | Summe „Erstattung offen“; Stand je Stornorechnung |
| Storno bezahlter Rechnungen | Zahlungseingänge bleiben erhalten | bei jeder Statusänderung; laufend in der Rechnungsliste | Sperre „bezahlt → storniert“ mit Meldung; Hinweis auf stornierte Rechnungen, die direkt vor dem Storno bezahlt waren (nur Anzeige) |
| Nummernkreis | Vollständigkeit, keine Doppelvergabe | beim Erstellen, laufend in der Rechnungsliste | Nummer eindeutig (Datenbank); abweichende Nummer nur mit Grund im Verlauf; Lückenanzeige |
| Duplikaterkennung | keine Doppelerfassung von Belegen | beim Hochladen | identische Datei (SHA-256) wird abgelehnt |
| Prüfpflicht | Richtigkeit automatisch gelesener Werte | jeder Beleg | Status „zu prüfen“ bis zum Speichern; Verlaufseintrag `reviewed` |
| Fristüberwachung | zeitgerechte Erfassung | laufend | Markierung „über 10 Tage ungeprüft“, überfällige Rechnungen |
| GWG-Hinweis | Anlagegüter nicht als sofort abziehbare Ausgabe erfassen | bei jeder Anzeige eines Belegs mit Betrag über 800 € ohne Markierung | Hinweis im Formular; Entscheidung im Verlauf (`Anlagegut (AfA)`); getrennte Summe in der Übersicht |
| § 13b-Hinweis | Erkennen von Eingangsleistungen mit Steuerschuldnerschaft des Leistungsempfängers | beim Hochladen, Anzeige bei der Prüfung | Hinweis mit Grund in `suggestion_json`; Entscheidung im Verlauf (`Steuerschuldnerschaft § 13b UStG`) |
| § 13b-Übersicht | Vollständigkeit der zu meldenden § 13b-Beträge | laufend auf der Ausgabenseite | Summen je Quartal und Jahr; Abgleich mit der abgegebenen Meldung (5.2) |
| Pflichtangaben bei Storno/Verwerfen | Nachvollziehbarkeit | bei jeder Stornierung/Verwerfung | Grund im Verlauf |
| Unveränderbarkeit | Schutz vor Verfälschung | ständig | Trigger, Schreibschutz, Protokoll (Teil 3.3) |
| Integritätsprüfung | Erkennen von Verfälschung oder Verlust | bei jeder Detailansicht (Datei), Backups-Seite, jeder Sicherung, jedem Export, manuell | Kontrollprotokoll (`verify`, `backup`, `export`) |
| Datensicherung | Schutz vor Verlust | täglich automatisch | Kontrollprotokoll (`backup`), Sicherungsdateien |
| Wiederherstellungstest | Nachweis der Wiederherstellbarkeit | mindestens jährlich, zusätzlich nach Programm- oder Serverwechsel | Kontrollprotokoll (`restore_test`) |
| Programmidentität | nur freigegebene, versionierte Programmstände | jede Auslieferung | `deploy.sh` (nur eingecheckte Stände, Tests), `system_events` (`version`) |
| Vollständigkeit der Umsatzgrenze | Auch Umsätze außerhalb des Programms werden berücksichtigt | bei jeder Erfassung, laufend in der Übersicht | Nur anfügbare Einträge mit Herkunft und Datum, Teil der Hash-Kette; Korrekturen als Gegenbuchung mit Begründung |
| Umsatzgrenzen § 19 UStG | Keine § 19-Rechnungen nach Verlust der Kleinunternehmerregelung | laufend (Rechnungsliste, Formular), bei jeder neuen Rechnung | Anzeige und Warnungen; Sperre mit protokolliertem Grund im Verlauf (`created`, „Umsatzgrenze § 19 UStG“) |
| Konfigurationshistorie | Nachvollziehbarkeit von Einstellungen | Start, vor jeder Rechnung | `system_events` (`config_changed`) |

Das Kontrollprotokoll (`control_runs`) ist nur anfügbar, Teil der Hash-Kette und auf der
Backups-Seite einsehbar.

## 5.2 Organisatorische Kontrollen des Unternehmers

| Kontrolle | Häufigkeit | Nachweis |
|---|---|---|
| Belege hochladen und prüfen | laufend, spätestens 10 Tage nach Eingang | Erfassungs- und Prüfzeitpunkt im Verlauf |
| Zahlungseingänge und -ausgänge mit dem Kontoauszug abgleichen, Status und Zahlungsdatum pflegen | monatlich | Verlaufseinträge `status:paid` bzw. `updated` |
| Warnungen prüfen (Nummernkreis, ungeprüfte Belege, überfällige Rechnungen, Integritätsprüfung) | monatlich | – |
| Kontrollprotokoll auf fehlgeschlagene Sicherungen prüfen | monatlich | Einträge `backup` |
| Wiederherstellungstest einer auswärtigen Sicherung | jährlich | Eintrag `restore_test` |
| Verfahrensdokumentation mit dem eingesetzten Programm abgleichen (Rz. 101) | bei jeder Programmänderung und jährlich | git-Historie dieser Dokumentation |
| Jahresabschluss: Einnahmen und Ausgaben des Jahres exportieren und mit der EÜR abstimmen | jährlich | Export im Kontrollprotokoll |
| Einnahmen der Tätigkeiten außerhalb des Programms erfassen und mit deren Aufzeichnungen abgleichen | vierteljährlich und zum Jahresende | Einträge unter „Extern“, Aufzeichnungen nach Teil 6 |
| Als Anlagegut markierte Belege mit dem Anlagenverzeichnis abgleichen | jährlich vor der EÜR | Filter „Nur Anlagegüter“, Anlagenverzeichnis (Teil 6) |
| Belege ausländischer Anbieter auf § 13b prüfen, § 13b-Summen mit der Umsatzsteuermeldung abstimmen | je Meldezeitraum, mindestens jährlich | Markierung und Verlauf je Beleg |

Die tatsächlich durchgeführten organisatorischen Kontrollen, soweit nicht automatisch
protokolliert, werden in Teil 6 festgehalten.
