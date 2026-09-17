# 1. Allgemeine Beschreibung

## 1.1 Zweck

Das Programm ist das Rechnungs- und Belegsystem eines Einzelunternehmers, der Kleinunternehmer nach
§ 19 UStG ist und den Gewinn durch Einnahmen-Überschuss-Rechnung (§ 4 Abs. 3 EStG) ermittelt. Es
erfüllt folgende Funktionen:

- **Fakturierung:** Erstellen von Ausgangsrechnungen als PDF mit fortlaufender Nummer.
- **Grundaufzeichnung der Einnahmen:** Jede Rechnung ist ein Datensatz mit Betrag, Datum, Kunde,
  Leistung und Zahlungsstatus; Zahlungseingänge werden mit Datum und Zahlungsart erfasst.
- **Belegsicherung und Grundaufzeichnung der Ausgaben:** Eingangsrechnungen und Quittungen werden
  als Datei hochgeladen, unverändert archiviert und mit Buchungsdaten (Lieferant, Datum, Betrag,
  Kategorie, Zahlungsart, Zahlungsdatum) versehen.
- **Archiv:** Unveränderbare Aufbewahrung aller Rechnungen und Belege mit Prüfsummen und
  lückenloser Änderungshistorie.
- **Übersicht:** Einnahmen und Ausgaben je Jahr nach Zahlungsdatum (Zufluss-/Abflussprinzip) als
  Grundlage der Einnahmen-Überschuss-Rechnung. Als Anlagegut markierte Belege sind getrennt
  ausgewiesen; die Übersicht zeigt den Überschuss vor Abschreibungen (AfA).

Die Einnahmen-Überschuss-Rechnung selbst (Anlage EÜR) und die Steuererklärungen werden außerhalb
des Programms erstellt (siehe Teil 6).

## 1.2 Geltungsbereich und Abgrenzung

Im Programm geführt:

| Unterlage | Form | Aufbewahrung |
|---|---|---|
| Ausgangsrechnungen | PDF, im Programm erzeugt | Archiv, unveränderbar |
| Rechnungsdaten (Grundaufzeichnung) | Datensätze in SQLite | Datenbank, Änderungen protokolliert |
| Eingangsrechnungen, Quittungen | PDF, JPEG, PNG, E-Rechnung (XRechnung-XML, ZUGFeRD-PDF) im Empfangsformat | Archiv, unveränderbar |
| Buchungsdaten der Ausgaben | Datensätze in SQLite | Datenbank, Änderungen protokolliert |
| Änderungs-, System- und Kontrollprotokolle | Datensätze in SQLite, Hash-Kette | Datenbank, nur anfügbar |
| Außerhalb erfasste Umsätze (nur Beträge, für die Umsatzgrenzen des § 19 UStG) | Datensätze in SQLite, Hash-Kette | Datenbank, nur anfügbar |

Die außerhalb erfassten Umsätze (2.10) sind ausschließlich eine Hilfsaufzeichnung für die
Umsatzgrenzen. Die Aufzeichnungen, Belege und Rechnungen der betreffenden Tätigkeiten werden
außerhalb dieses Programms geführt (Teil 6).

Nicht im Programm geführt (Ablage siehe Teil 6): Kontoauszüge, geschäftliche E-Mails und sonstige
Handels- und Geschäftsbriefe, Verträge, Steuerbescheide, Papierbelege vor der Erfassung, eine Kasse
(Bargeschäfte kommen allenfalls vereinzelt vor und werden als Zahlungsart „Bar“ erfasst).

## 1.3 Systemübersicht

```
Browser ──HTTPS──> Cloudflare Tunnel ──> Server (Docker)
                                          ├─ app     Flask-Webanwendung, Anmeldung, Rechnungen, Belege
                                          ├─ backup  tägliche Datensicherung mit Prüfung
                                          └─ Daten   data/invoices.sqlite3   Datenbank
                                                     data/archive/<Jahr>/    Rechnungs-PDFs
                                                     data/expenses/<Jahr>/   Belegdateien
                                                     backups/                Sicherungen, Exporte
```

- Ein Benutzer (der Unternehmer), Anmeldung mit Benutzername und Passwort.
- Programmiersprache Python, Datenbank SQLite, PDF-Erzeugung mit reportlab, Textauslese aus PDFs
  mit poppler (`pdftotext`, `pdfdetach`). Keine Cloud-Dienste für die Verarbeitung; Daten
  verlassen den Server nur als Datensicherung.
- Der Quellcode ist öffentlich und versioniert; die eingesetzte Version ist nachweisbar (Teil 4).

## 1.4 Grundsätze der Umsetzung (Überblick)

| GoBD | Umsetzung | Teil |
|---|---|---|
| Nachvollziehbarkeit (Rz. 30–35) | Jede Rechnung und jeder Beleg hat eine eindeutige Nummer; Verlauf je Datensatz; diese Dokumentation | 2, 3 |
| Vollständigkeit (Rz. 36–43) | Fortlaufende Rechnungsnummern mit Lückenanalyse; Belege und Rechnungen nicht löschbar; Duplikaterkennung | 2, 3 |
| Richtigkeit (Rz. 44) | Automatisch gelesene Werte gelten erst nach manueller Prüfung | 2 |
| Zeitgerechtheit (Rz. 45–52) | Warnung bei Belegen, die länger als 10 Tage ungeprüft sind; Erfassungszeitpunkte protokolliert | 2, 5 |
| Ordnung (Rz. 53–57) | Ablage nach Jahr, Suche, Filter, Kategorien | 2, 3 |
| Unveränderbarkeit (Rz. 58–60, 107–112) | Datenbank-Trigger, schreibgeschützte Dateien, SHA-256, Änderungsprotokoll mit alt/neu, Hash-Kette | 3 |
| Belegwesen (Rz. 61–81) | Beleg und Buchungsdaten über die Belegnummer (id) verknüpft | 3 |
| IKS (Rz. 100–102) | Automatische und manuelle Kontrollen, Kontrollprotokoll | 5 |
| Datensicherheit (Rz. 103–106) | Zugriffsschutz, tägliche geprüfte Sicherung, Wiederherstellungstest | 4 |
| Aufbewahrung (Rz. 113–144) | Empfangsformat, keine Konvertierung, E-Rechnungen vollständig, Mindestfrist je Datensatz, keine Löschung | 3, 4 |
| Datenzugriff (Rz. 158–178) | Z1 am Bildschirm, Z3-Export als CSV mit index.xml | 4 |
