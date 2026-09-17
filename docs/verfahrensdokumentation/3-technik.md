# 3. Technische Systemdokumentation

Quellcode: `src/invoices/`. Datenbankschema: `src/invoices/db.py` (Grundschema `SCHEMA` plus
nummerierte Migrationen `MIGRATIONS`).

## 3.1 Komponenten

| Modul | Aufgabe |
|---|---|
| `web.py`, `templates/` | Weboberfläche, Formulare, Anzeige |
| `auth.py` | Anmeldung, CSRF-Schutz, Sperre nach Fehlversuchen |
| `archive.py` | Rechnungen: Nummernkreis, Erstellen, Status, Notizen, Prüfsummen |
| `pdf.py` | Erzeugung des Rechnungs-PDF |
| `expenses.py` | Belege: Hochladen, Prüfen, Änderungsprotokoll, Summen |
| `extract.py`, `einvoice.py` | Wertevorschläge aus PDF-Text, XRechnung/UBL/CII und ZUGFeRD |
| `db.py`, `chain.py` | Schema, Migrationen, Protokolle, Hash-Kette und deren Prüfung |
| `system.py` | Programmversion, Konfigurationshistorie, Kontrollprotokoll |
| `backup.py` | Datensicherung, Prüfung, Wiederherstellung, Wiederherstellungstest |
| `export.py` | Datenexport (Z3) |
| `invoice_import.py` | Import vor dem Programm erstellter Rechnungen (nur Kommandozeile) |
| `cli.py` | Kommandozeile: `verify`, `backup`, `verify-backup`, `restore-test`, `restore`, `export`, `import-invoice` |

## 3.2 Datenmodell

Alle Zeitstempel (`created_at`, `updated_at`, `at`) sind UTC im Format ISO 8601
(`2026-09-17T08:30:00+00:00`). Datumsfelder sind `JJJJ-MM-TT`. Beträge sind ganze Cent
(`amount_cents`, 70000 = 700,00 €); als Kleinunternehmer ohne Umsatzsteuer sind es Endbeträge.

### invoices – Ausgangsrechnungen (ein Datensatz je Rechnung)

| Feld | Bedeutung | änderbar |
|---|---|---|
| `id` | interne laufende Nummer | nein |
| `number` | Rechnungsnummer, eindeutig, Schema `JJJJ-NNN` | nein |
| `issue_date` | Rechnungsdatum | nein |
| `service_date` | Leistungsdatum/-zeitraum wie gedruckt (`16.09.2026` oder `01.09.2026 bis 15.09.2026`) | nein |
| `due_date` | Fälligkeit | nein |
| `customer_name`, `customer_street`, `customer_city` | Kunde wie auf der Rechnung | nein |
| `title`, `description` | Leistung | nein |
| `amount_cents` | Rechnungsbetrag in Cent | nein |
| `status` | `open` offen, `paid` bezahlt, `cancelled` storniert | ja, protokolliert |
| `paid_date` | Zahlungseingang | ja, protokolliert |
| `payment_method` | `bank` Überweisung/Karte, `cash` bar, leer = nicht erfasst (vor Einführung des Feldes) | ja, protokolliert |
| `notes` | interne Notiz | ja, protokolliert |
| `source` | `generated` im Programm erstellt, `imported` vor Einführung des Programms erstellt, Original-PDF unverändert übernommen (Teil 2.9) | nein |
| `pdf_path` | Pfad unter `data/archive/` | nein |
| `pdf_sha256`, `pdf_size` | Prüfsumme und Größe der PDF | nein |
| `payload_json` | erstellt: vollständige Eingabedaten, Absenderdaten (Name, Anschrift, Steuernummer, Bankverbindung) und die fest gedruckten Texte (`texts.small_business_note`, ab 18.09.2026) zum Erstellungszeitpunkt; importiert: Grund, ursprünglicher Dateiname, bestätigte Hinweise, Programmversion (Absenderdaten stehen nur in der Original-PDF) | nein |
| `retain_until` | Ende der Aufbewahrung (31.12. des Rechnungsjahres + eingestellte Jahre) | nein |
| `created_at`, `updated_at` | Erfassung, letzte Änderung | `updated_at` ja |

### expenses – Eingangsbelege (ein Datensatz je hochgeladener Datei)

| Feld | Bedeutung | änderbar |
|---|---|---|
| `id` | Belegnummer, fortlaufend | nein |
| `vendor` | Lieferant | ja, protokolliert |
| `invoice_number` | Rechnungsnummer des Lieferanten | ja, protokolliert |
| `expense_date` | Rechnungs-/Belegdatum | ja, protokolliert |
| `amount_cents` | Betrag brutto in Cent | ja, protokolliert |
| `category` | Kategorie (sachliche Zuordnung) | ja, protokolliert |
| `status` | `paid` bezahlt, `open` offen, `void` verworfen | ja, protokolliert |
| `paid_date` | Zahlungsdatum; leer = Rechnungsdatum | ja, protokolliert |
| `payment_method` | `bank`, `cash`, `private` privat bezahlt (Einlage), leer = nicht erfasst | ja, protokolliert |
| `notes` | Notiz, bei `void` Grund | ja, protokolliert |
| `reviewed` | 1 = Werte manuell geprüft | wird beim ersten Speichern 1 |
| `doc_path` | Pfad unter `data/expenses/`: `<Upload-Jahr>/<JJJJMMTT>_<Dateiname>_<8 Zeichen Prüfsumme>.<Typ>` | nein |
| `doc_sha256`, `doc_size` | Prüfsumme (eindeutig, verhindert Doppelerfassung) und Größe | nein |
| `doc_type` | `pdf`, `jpg`, `png`, `xml` | nein |
| `original_filename` | Dateiname beim Hochladen | nein |
| `doc_text` | Textebene der PDF bzw. lesbare Fassung der E-Rechnung, für die Suche | nein |
| `suggestion_json` | beim Hochladen gelesene Werte und deren Quelle (`xml`, `zugferd`, `text`, `none`) | nein |
| `retain_until` | Ende der Aufbewahrung (31.12. des Upload-Jahres + eingestellte Jahre) | nein |
| `created_at`, `updated_at` | Erfassung (Hochladen), letzte Änderung | `updated_at` ja |

### events, expense_events – Verlauf je Rechnung bzw. Beleg

| Feld | Bedeutung |
|---|---|
| `id` | laufende Nummer, lückenlos |
| `invoice_id` / `expense_id` | Verweis auf `invoices.id` / `expenses.id` |
| `at` | Zeitpunkt (UTC) |
| `action` | Art, siehe unten |
| `detail` | Inhalt; bei Änderungen `Feld: alt → neu`, mehrere durch `; ` getrennt; Texte in „…“, leer als `–` |
| `hash` | Glied der Hash-Kette (3.4) |
| `state_hash` | Hash des Datensatzes nach dieser Änderung (3.4) |

Aktionen `events`: `created` erstellt (Detail: `sha256=<PDF-Prüfsumme>`, ggf. Grund für abweichende
Nummer), `imported` übernommen (Detail: Prüfsumme, Grund, Originaldatei, ggf. Abweichung vom
Nummernkreis und bestätigte Hinweise), `status:open`, `status:paid`, `status:cancelled` Statusänderung,
`notes` Notiz geändert, `sealed` Zustand beim Einführen der Hash-Kette festgehalten.

Aktionen `expense_events`: `uploaded` hochgeladen (`sha256=…`), `reviewed` erstmals geprüft und
gespeichert, `updated` später geändert, `sealed` wie oben.

### system_events – Systemprotokoll

| `action` | `detail` |
|---|---|
| `version` | Programmversion bei Inbetriebnahme: git-Commit und Commit-Datum, `-dirty` bei nicht eingechecktem Stand, `dev` bei Entwicklung |
| `schema_migration` | `<Nummer>: <Beschreibung>` der ausgeführten Schemaänderung |
| `config_changed` | vollständige neue Konfiguration als JSON: Aufbewahrungsjahre, Anzahl Sicherungen, Absenderdaten |
| `trigger_missing` | beim Start fehlende Schutz-Trigger (werden wiederhergestellt; der Eintrag bleibt als Hinweis auf einen Eingriff) |

### control_runs – Kontrollprotokoll

`at`, `kind` (`verify`, `backup`, `restore_test`, `export`), `ok` (1/0), `detail` (Ergebnis bzw.
Fehler), `app_version`, `hash`. Siehe Teil 5.

## 3.3 Unveränderbarkeit (Rz. 58, 107–111)

1. **Datenbank-Trigger** brechen jede Änderung der mit „nein“ markierten Felder, jedes Löschen von
   Rechnungen und Belegen und jede Änderung oder Löschung in den Protokolltabellen ab.
2. **Archivdateien** werden exklusiv neu angelegt (bestehende Dateien können nicht überschrieben
   werden), auf Datenträger geschrieben (fsync) und schreibgeschützt (0444). Datensatz und Datei
   werden gemeinsam gespeichert; scheitert eins, wird beides verworfen.
3. **Prüfsummen:** SHA-256 jeder Datei steht unveränderbar im Datensatz und wird bei jeder Anzeige,
   jeder Sicherung und jeder Prüfung verglichen.
4. **Änderungsprotokoll:** Jede Änderung eines änderbaren Feldes erzeugt im selben
   Datenbankvorgang einen Verlaufseintrag mit altem und neuem Wert. Texte werden vollständig
   protokolliert.
5. **Keine Neuerzeugung:** Rechnungs-PDFs werden nie neu erzeugt. Änderungen der Absenderdaten
   wirken nur auf künftige Rechnungen; die Daten zum Erstellungszeitpunkt stehen in `payload_json`
   (Stammdatenhistorie, Rz. 59 Beispiel 4).
6. **Hash-Kette und Trigger-Prüfung** (3.4) erkennen Eingriffe unter Umgehung der Trigger.

## 3.4 Hash-Kette (Rz. 110)

Wer Zugriff auf die Datenbankdatei hat, könnte Trigger entfernen. Deshalb gilt zusätzlich:

- **Kette:** In jeder der vier Protokolltabellen ist
  `hash = SHA-256(hash des Eintrags mit id−1 als Hex-Text, beim ersten Eintrag leer || kanonisch(Eintrag ohne hash))`.
- **Kanonische Form:** JSON-Objekt aller Spalten, Schlüssel sortiert, ohne Felder mit NULL oder
  leerem Text, ohne Leerzeichen zwischen Elementen, UTF-8 ohne Escape von Nicht-ASCII-Zeichen.
- **Zustand:** `state_hash` in `events`/`expense_events` = SHA-256(kanonisch(Datensatz aus
  `invoices`/`expenses` nach der Änderung, ohne `updated_at`)).
- **Prüfung** (`invoices verify`, jede Sicherung, Backups-Seite, Export, Wiederherstellungstest):
  - lückenlose ids und korrekte Hashes in jeder Kette,
  - für jede Rechnung und jeden Beleg stimmt der `state_hash` des letzten Verlaufseintrags mit dem
    aktuellen Datensatz überein (erkennt Änderungen ohne Protokoll),
  - alle Schutz-Trigger des aktuellen Schemastands sind vorhanden.
- **Anker:** Jede Sicherung speichert im Manifest den letzten Eintrag (id, hash) jeder Kette. Der
  Wiederherstellungstest prüft, dass die laufende Datenbank diese Einträge unverändert enthält.
  Damit wird auch das Entfernen der neuesten Einträge erkannt, sofern eine ältere Sicherung
  außerhalb des Servers liegt.
- **Einführung:** Beim Einführen der Hash-Kette (Migration) wurden bestehende Protokolleinträge
  ohne inhaltliche Änderung mit Hashes versehen und für jeden Datensatz ein `sealed`-Eintrag
  angelegt.
- **Schemaänderungen:** Neue Spalten erhalten NULL oder leeren Text als Vorgabe und lassen so alle
  Hashes gültig.

## 3.5 Belegfunktion und Verknüpfung (Rz. 64, 71, 122)

- Index eines Belegs: `expenses.id` (Belegnummer); einer Rechnung: `invoices.number`.
- Buchungsdaten und Beleg bilden einen Datensatz; die Datei ist über `doc_path`/`pdf_path` und
  Prüfsumme dauerhaft zugeordnet. Der Verlauf verweist über `expense_id`/`invoice_id`.
- Buchungsdatum im Sinne der Einnahmen-Überschuss-Rechnung ist das Zahlungsdatum (`paid_date`);
  bei Belegen ohne Zahlungsdatum das Rechnungsdatum, ohne beides der Tag des Hochladens.

## 3.6 Formate und Aufbewahrung (Rz. 118–135)

- Ausgangsrechnungen: PDF mit Textebene, wie versandt (Ursprungsformat).
- Eingangsbelege: im Empfangsformat, byteweise unverändert. Keine Konvertierung, keine
  Bildbearbeitung, keine OCR. E-Rechnungen als XML bleiben XML; ZUGFeRD-PDFs behalten die
  eingebettete XML-Datei. Die lesbare Anzeige wird bei jedem Aufruf aus dem Original erzeugt.
- Daten: SQLite-Datenbank. Maschinelle Auswertbarkeit über den Export (Teil 4.6).
- Aufbewahrungsfrist je Datensatz in `retain_until`, standardmäßig 10 Jahre ab Jahresende (deckt
  10 Jahre für Aufzeichnungen und 8 Jahre für Buchungsbelege ab). Es wird nichts automatisch
  gelöscht.
- Kryptografie: Keine Verschlüsselung der Daten im Programm. Verschlüsselung der auswärtigen
  Sicherung siehe Teil 6 (Schlüssel für die gesamte Frist verfügbar halten, Rz. 134).

## 3.7 Schemaänderungen (Rz. 142)

Das Grundschema wird nie geändert. Jede Änderung ist eine neue, nummerierte Migration in
`db.MIGRATIONS`, läuft beim Start einmalig in einem Datenbankvorgang, prüft danach die
Fremdschlüssel und wird in `system_events` protokolliert. `PRAGMA user_version` enthält die Anzahl
ausgeführter Migrationen. Migrationen ändern nur Struktur oder Format, nie Inhalte.

| Nr. | Beschreibung |
|---|---|
| 1 | Tabellen `system_events` und `control_runs` |
| 2 | Hash-Kette über alle Protokolle, Versiegelung bestehender Datensätze |
| 3 | `expenses` neu aufgebaut, damit `doc_type` = `xml` zulässig ist (alle Zeilen, ids, Indizes und Trigger unverändert übernommen) |
| 4 | Spalte `payment_method` in `invoices` und `expenses` |
