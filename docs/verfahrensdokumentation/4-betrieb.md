# 4. Betriebsdokumentation

Technische Details zu Befehlen: `README.md`, `docs/BACKUP.md`, `docs/EXPORT.md` im Repository.

## 4.1 Installation und Betrieb

- Betrieb als Docker-Stack auf einem eigenen Server (Dienste `app` und `backup`), erreichbar nur
  über einen Cloudflare-Tunnel. Der Port ist nur lokal gebunden; es gibt keine offenen Ports.
- Persistente Daten im Installationsverzeichnis: `data/` (Datenbank, Archiv), `backups/`
  (Sicherungen, Exporte), `config/sender.toml` (Absenderdaten, nur lesend eingebunden), `.env`
  (Anmeldedaten als scrypt-Hash, Sitzungsschlüssel).
- Konkreter Server, Standort und Zugänge: Teil 6.

## 4.2 Zugriffsschutz (Rz. 103)

- Ein Benutzerkonto; Passwort mindestens 12 Zeichen, gespeichert nur als scrypt-Hash.
- Sitzung 12 Stunden, Cookies nur über HTTPS, CSRF-Token bei jeder Änderung, Sperre für 15 Minuten
  nach 5 Fehlversuchen je Client.
- Empfohlen und in Teil 6 festzuhalten: vorgeschaltete Zugangskontrolle (Cloudflare Access) als
  zweiter Faktor.
- Wer Zugriff auf den Server und seine Dateien hat und wie (z. B. SSH mit Schlüssel): Teil 6.

## 4.3 Programmänderungen und Programmidentität (Rz. 80, 153–154)

1. Änderungen am Programm werden im git-Repository vorgenommen und eingecheckt; Tests laufen
   automatisch.
2. `deploy.sh` verweigert die Auslieferung nicht eingecheckter Änderungen. Das Image wird gebaut,
   die Testsuite läuft im Zielformat, die Version (git-Commit und Datum) wird in das Image
   geschrieben.
3. Beim Start trägt das Programm jede neue Version in `system_events` (`version`) ein, führt
   ausstehende Schemaänderungen aus (`schema_migration`) und protokolliert geänderte Konfiguration
   (`config_changed`). Die Version steht in der Fußzeile jeder Seite und in jedem Eintrag des
   Kontrollprotokolls.
4. Damit ist für jeden Zeitpunkt nachweisbar, welcher Programmstand (Quellcode im git-Repository)
   und welche Konfiguration im Einsatz waren.

## 4.4 Datensicherung (Rz. 103–106)

- Der Dienst `backup` erstellt beim Start und danach alle 24 Stunden eine Sicherung; zusätzlich
  manuell über die Backups-Seite.
- Vor jeder Sicherung: Prüfung aller Dateien gegen ihre Prüfsummen, der Hash-Ketten und der
  Trigger. Bei einem Fehler wird keine Sicherung erstellt und der Fehler protokolliert.
- Inhalt: konsistente Kopie der Datenbank, alle Rechnungs-PDFs, alle Belegdateien, Manifest mit
  SHA-256 jeder Datei und den Kettenenden (Teil 3.4).
- Aufbewahrung: die letzten 30 Sicherungen und dauerhaft die jeweils letzte Sicherung jedes Monats.
- Kopie außerhalb des Servers: Ziel, Häufigkeit, Verschlüsselung und Schlüsselaufbewahrung in
  Teil 6.

## 4.5 Wiederherstellung

- `invoices verify-backup <Datei>` prüft eine Sicherung gegen ihr Manifest.
- `invoices restore-test <Datei>` stellt eine Sicherung in ein temporäres Verzeichnis wieder her,
  prüft alle Dateien, Ketten und Trigger der wiederhergestellten Daten und vergleicht die
  Kettenenden mit der laufenden Datenbank. Das Ergebnis wird im Kontrollprotokoll festgehalten.
- `invoices restore <Datei> <leeres Verzeichnis>` stellt produktiv wieder her; danach
  `invoices verify`. Ablauf in `docs/BACKUP.md`.
- Eine Sicherung ist ein gewöhnliches tar.gz-Archiv und auch ohne das Programm lesbar.

## 4.6 Datenzugriff der Finanzbehörde (Rz. 158–178)

- **Z1, unmittelbarer Zugriff:** Der Prüfer erhält am Bildschirm Einsicht in die Weboberfläche
  (Listen, Filter, Suche, Detailseiten mit Verlauf, Anzeige aller Rechnungen und Belege). Der
  Unternehmer bedient das Programm; ein eigenes Nur-Lese-Konto gibt es derzeit nicht.
- **Z2, mittelbarer Zugriff:** Auswertungen mit den vorhandenen Filtern und Suchen nach Vorgabe des
  Prüfers.
- **Z3, Datenüberlassung:** `invoices export [--year JJJJ]` bzw. Backups-Seite, Abschnitt
  „Datenexport für die Betriebsprüfung“. Ergebnis: ZIP-Datei mit
  - je einer CSV-Datei pro Tabelle (Kopfzeile, Semikolon, Dezimalkomma, CR LF, UTF-8, alle Spalten
    ungefiltert),
  - `index.xml` nach dem Beschreibungsstandard (GDPdU-DTD) mit Feldbeschreibungen und Verknüpfungen,
  - allen zugehörigen Rechnungs-PDFs und Belegdateien im Originalformat,
  - `README.txt` mit Erläuterungen zu Format, Verknüpfungen, Statuswerten, Protokollaktionen und
    der Hash-Kette.
  Vor dem Export werden alle Dateien, Ketten und Trigger geprüft; bei einem Fehler wird nicht
  exportiert. Jeder Export steht im Kontrollprotokoll.
- Die Verfahrensdokumentation Teile 1–5 wird auf Verlangen mit übergeben, Teil 6 soweit für das
  Verständnis erforderlich.

## 4.7 Systemwechsel (Rz. 142–144)

Bei Ablösung des Programms werden Datenbank und Archiv vollständig über den Export (4.6) oder die
Sicherung (4.4) in das neue System übernommen. Die letzte Sicherung, der letzte Export und der
Quellcode des letzten Programmstands werden bis zum Ende der Aufbewahrungsfrist aufbewahrt.
