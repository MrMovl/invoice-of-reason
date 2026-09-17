# 6. Betriebsspezifischer Teil – Vorlage

**Diese Datei ist eine Vorlage.** Der ausgefüllte Teil 6 enthält Angaben zum Unternehmen und wird
**nicht** in diesem öffentlichen Repository geführt, sondern z. B. in einem privaten Repository
oder als versionierte Datei im Konfigurationsverzeichnis des Servers (dann in jede Sicherung
aufnehmen). Jede Fassung mit Datum versehen; alte Fassungen nicht überschreiben.

## 6.1 Unternehmen

- Name, Anschrift, Steuernummer, zuständiges Finanzamt
- Tätigkeit
- Kleinunternehmer nach § 19 UStG seit; Gewinnermittlung EÜR
- Verantwortlich für Buchführung und Verfahren (Rz. 21); ggf. Steuerberater und dessen Aufgaben

## 6.1a Inbetriebnahme und Neustart

- Datum der Inbetriebnahme des Programms
- Falls der Datenbestand vor der produktiven Nutzung neu begonnen wurde: Datum, Grund, was aus dem
  alten Bestand übernommen wurde (importierte Rechnungen mit Nummer, erneut hochgeladene Belege),
  wo die letzte Sicherung des alten Bestands aufbewahrt wird
- Vor dem Programm erstellte Rechnungen, die importiert wurden (Nummer, Datum des Imports)

## 6.2 Eingesetzte Systeme und Belegfluss

- Installation dieses Programms: Server (Gerät, Standort), Zugang (Domain, Cloudflare Access ja/nein),
  Speicherort der Installation, wer Zugriff auf den Server und seine Dateien hat und wie
- Geschäftskonto: Bank, Form der Kontoauszüge (PDF/CSV), wo und wie lange sie abgelegt werden
- Geschäftliche E-Mails mit Beleg- oder Geschäftsbrieffunktion (Rz. 121): Postfach, Ablage,
  Aufbewahrung
- Wie Rechnungen versandt werden (E-Mail als PDF, Papier)
- Wie Eingangsrechnungen eingehen (E-Mail, Portal-Download, Papier, E-Rechnung) und wer sie wann
  hochlädt
- Bargeschäfte: kommen sie vor, wie werden sie erfasst
- Erstellung der EÜR und Steuererklärungen (Programm, Steuerberater), wie die Summen aus diesem
  Programm übernommen werden, Zuordnung der Kategorien zu Zeilen der Anlage EÜR (Rz. 97)
- Gründungsjahr des Unternehmens (Einstellung `INVOICES_FOUNDING_YEAR`) und ob es Umsätze außerhalb
  dieses Programms gibt, die für die Kleinunternehmergrenzen (§ 19 UStG) zählen könnten, und wie
  sie überwacht werden
- Anlagenverzeichnis (§ 4 Abs. 3 Satz 5 EStG): wo und wie es geführt wird, wer die AfA berechnet,
  wie es mit den als „Anlagegut“ markierten Belegen abgeglichen wird
- Umsatzsteuer nach § 13b UStG auf Eingangsleistungen: in welchen Zeiträumen gemeldet wird
  (Voranmeldung oder nur Jahreserklärung), wer die Meldung erstellt, wie die Summen aus dem
  Programm übernommen werden
- Sonstige Unterlagen (Verträge, Bescheide) und ihre Ablage

## 6.3 Organisationsanweisung Scannen/Fotografieren von Papierbelegen (Rz. 136–140)

- Wer erfasst
- Wann (z. B. am Tag des Eingangs, spätestens nach 10 Tagen)
- Welche Belege
- Gerät, Auflösung, Farbe (vollständige Farbwiedergabe, wenn Farbe Bedeutung hat)
- Qualitätskontrolle: Lesbarkeit und Vollständigkeit am Bildschirm vor dem Hochladen prüfen
- Umgang mit Fehlern (erneut erfassen, fehlerhaften Upload „verwerfen“ mit Grund)
- Werden die Papierbelege danach vernichtet? Ausnahmen (Belege, die im Original aufzubewahren sind)
- Keine Vermerke auf dem Papier nach der Erfassung; sonst erneut erfassen

## 6.4 Datensicherung außerhalb des Servers

- Ziel (Anbieter, Standort), Verfahren (Werkzeug), Häufigkeit
- Schutz vor Löschen/Überschreiben (Versionierung, Aufbewahrungssperre)
- Verschlüsselung, wo der Schlüssel liegt und wie er für die gesamte Aufbewahrungsfrist verfügbar
  bleibt (Rz. 134)
- Wo das Passwort bzw. der Zugang zum Programm hinterlegt ist, falls der Unternehmer ausfällt

## 6.5 Durchgeführte organisatorische Kontrollen

| Datum | Kontrolle (Teil 5.2) | Ergebnis | Maßnahmen |
|---|---|---|---|
| | | | |

## 6.6 Änderungshistorie dieses Teils

| Datum | Änderung |
|---|---|
| | |
