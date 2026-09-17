# 2. Anwenderdokumentation

Beschreibt die Arbeitsabläufe in der Weboberfläche. Bezeichnungen in Anführungszeichen sind
Beschriftungen im Programm.

## 2.1 Ausgangsrechnung erstellen

1. „Neue Rechnung“. Das Programm schlägt die nächste Nummer im Schema `JJJJ-NNN` vor
   (z. B. 2026-004), das Rechnungsdatum (heute) und ein Zahlungsziel von 14 Tagen.
2. Kunde (Name, Straße, PLZ/Ort), Leistungsdatum oder -zeitraum, Leistungstitel, optionale
   Beschreibung und Betrag eintragen. Bekannte Kunden werden vorgeschlagen; „Als Vorlage“ übernimmt
   Kunde und Leistung einer früheren Rechnung.
3. Optional „Vorschau“: erzeugt das PDF zur Ansicht. Vorschauen werden nicht gespeichert.
4. „Erstellen“: Das PDF wird erzeugt, im Archiv abgelegt und mit Prüfsumme, allen Eingaben und den
   zu diesem Zeitpunkt gültigen Absenderdaten gespeichert. Ab jetzt sind Nummer, Daten, Kunde,
   Leistung, Betrag und PDF unveränderbar.

**Hinweis auf die Kleinunternehmerregelung (§ 34a Nr. 5 UStDV):** Jede Rechnung trägt den Satz
„Für diese Leistung gilt die Steuerbefreiung für Kleinunternehmer (§ 19 UStG). Es wird keine
Umsatzsteuer berechnet.“ Rechnungen, die vor dem 18.09.2026 erstellt wurden, tragen den bis dahin
verwendeten Satz „Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.“ Archivierte PDFs werden nicht
neu erzeugt; der jeweils gedruckte Satz steht zusätzlich in `payload_json` (Teil 3.2).

**Nummernkreis (Rz. 40, 50):** Eine Nummer, die das Schema verlässt, nicht zum Jahr des
Rechnungsdatums passt oder eine Lücke erzeugt, wird abgelehnt. Nur mit „Abweichende Nummer bewusst
verwenden“ und einem Grund wird sie angenommen; der Grund steht im Verlauf der Rechnung. Die
Rechnungsliste zeigt Lücken und Abweichungen im Nummernkreis als Warnung an.

**Fehlerhafte Rechnung:** Eine erstellte Rechnung wird nie geändert oder gelöscht. Sie wird
storniert (2.3) und eine neue, korrekte Rechnung erstellt.

## 2.2 Zahlungseingang erfassen

Auf der Detailseite der Rechnung „Als bezahlt markieren“ mit Zahlungsdatum und Zahlungsart
(„Überweisung/Karte“ oder „Bar“). Barzahlungen sind am Tag des Eingangs zu erfassen (Rz. 48).
„Wieder auf offen setzen“ nimmt eine irrtümliche Erfassung zurück. Jede Änderung steht mit altem
und neuem Wert im Verlauf.

## 2.3 Rechnung stornieren

„Stornieren“ mit Pflichtangabe eines Grundes. Die Rechnung bleibt mit Nummer und PDF im Archiv, hat
den Status „Storniert“ und zählt nicht zu den Einnahmen. Die Nummer bleibt vergeben (keine Lücke).

## 2.4 Notizen

Freitext zu einer Rechnung. Jede Änderung wird mit altem und neuem Text protokolliert.

## 2.5 Eingangsbeleg erfassen

1. „Ausgaben“ → Dateien auswählen (PDF, JPEG, PNG, XRechnung-XML; mehrere möglich, je max. 20 MB)
   → hochladen. Der Beleg sollte zeitnah nach Eingang hochgeladen werden.
2. Das Programm speichert die Datei unverändert im Archiv (Prüfsumme, schreibgeschützt), vergibt
   die Belegnummer (fortlaufende id) und lehnt eine bereits hochgeladene identische Datei ab.
3. Werte werden automatisch vorgeschlagen, in dieser Reihenfolge:
   - E-Rechnung als XML (XRechnung, UBL oder CII): Werte aus den strukturierten Daten.
   - PDF mit eingebetteter E-Rechnung (ZUGFeRD/Factur-X): Werte aus dem eingebetteten XML.
   - Sonstiges PDF mit Textebene: Werte aus dem Text (heuristisch).
   - Scan oder Foto ohne Text: keine Vorschläge.
   Die Quelle wird unter „Automatisch erkannt“ angezeigt.
4. Der Beleg hat den Hinweis „Zu prüfen“, bis er einmal gespeichert wurde.

## 2.6 Beleg prüfen und kontieren

Auf der Detailseite Lieferant, Betrag (brutto, wie bezahlt), Rechnungsdatum, Rechnungsnummer des
Lieferanten, Kategorie, Status und – bei „Bezahlt“ – Zahlungsdatum und Zahlungsart prüfen oder
eintragen, dann „Geprüft, speichern“. „Speichern und nächster“ führt durch alle ungeprüften Belege.

- **Kategorie** ist Pflicht; sie ist die sachliche Zuordnung des Belegs (Rz. 50).
- **Zahlungsart:** „Überweisung/Karte“, „Bar“ oder „Privat bezahlt (Einlage)“ für Ausgaben, die aus
  privaten Mitteln bezahlt wurden.
- **Zahlungsdatum** leer bedeutet: bezahlt am Rechnungsdatum.
- **E-Rechnungen** werden auf der Detailseite als lesbare Rechnung angezeigt (Rz. 157);
  „Herunterladen“ liefert die Originaldatei. Gutschriften und Fremdwährungen sind gekennzeichnet;
  bei Fremdwährung ist der Euro-Betrag von Hand einzutragen.

**Frist (Rz. 47):** Ein Beleg, der mehr als 10 Tage nach dem Hochladen noch ungeprüft ist, wird in
der Liste und auf der Detailseite als „über 10 Tage ungeprüft“ markiert.

**Korrektur:** Buchungsdaten bleiben korrigierbar. Jede Änderung wird mit Feld, altem und neuem
Wert im Verlauf protokolliert; die Belegdatei selbst ist unveränderbar.

## 2.7 Fehl-Upload

Ein irrtümlich hochgeladener Beleg (falsche Datei, privater Beleg, Duplikat mit anderer Datei) wird
nicht gelöscht, sondern auf Status „Verworfen“ gesetzt. Ein Grund in der Notiz ist Pflicht. Der
Beleg bleibt archiviert und zählt nicht zu den Ausgaben.

## 2.8 Auswertungen und Suche

- Rechnungsliste: Filter nach Jahr, Status, Suche in Nummer, Kunde, Leistung; Summen; überfällige
  Rechnungen markiert; Einnahmen und Ausgaben je Jahr nach Zahlungsdatum.
- Ausgabenliste: Filter nach Jahr (Zahlungsdatum, sonst Rechnungsdatum, sonst Upload), Status,
  Kategorie, „zu prüfen“; Volltextsuche in Lieferant, Nummer, Kategorie, Notiz, Dateiname und
  Belegtext.

## 2.9 Import einer vor dem Programm erstellten Rechnung

Nur für Rechnungen, die vor Einführung des Programms erstellt und versandt wurden. Kein Weg über
die Weboberfläche; auf dem Server per Kommandozeile `invoices import-invoice` (Aufruf in
`README.md`).

1. Original-PDF, wie versandt, und alle Angaben der Rechnung (Nummer, Datum, Leistungsdatum,
   Fälligkeit, Kunde, Leistung, Betrag) sowie ein Grund für den Import werden angegeben.
2. Das Programm prüft die Angaben wie beim Erstellen, lehnt eine bereits vergebene Nummer, ein
   Rechnungsdatum in der Zukunft und eine bereits archivierte Datei (als Rechnung oder Beleg) ab
   und gleicht Rechnungsnummer und Betrag mit dem Text der PDF ab.
3. Alle Angaben und etwaige Abweichungen werden angezeigt. Abweichungen müssen ausdrücklich
   bestätigt werden (`--accept-warnings`); zum Import wird die Rechnungsnummer eingetippt.
4. Die PDF wird unverändert archiviert (keine Neuerzeugung, keine Konvertierung), mit denselben
   Regeln wie eine im Programm erstellte Rechnung (Teil 3.3). Herkunft `imported`; der
   Verlaufseintrag enthält Grund, ursprünglichen Dateinamen, eine etwaige Abweichung vom
   Nummernkreis und bestätigte Hinweise. Danach ist die Rechnung unveränderbar und kann wie jede
   andere als bezahlt markiert oder storniert werden.

## 2.10 Backups-Seite

Zeigt das Ergebnis der Integritätsprüfung (Prüfsummen, Hash-Kette, Trigger), die vorhandenen
Sicherungen, das Kontrollprotokoll und erzeugt den Datenexport für die Betriebsprüfung
(Teil 4.6).
