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

**Umsatzgrenzen der Kleinunternehmerregelung (§ 19 UStG, Regeln seit 2025):** Das Formular und die
Rechnungsliste zeigen für das laufende Jahr die Zahlungseingänge (bezahlte Rechnungen nach
Zahlungsdatum), die offenen Rechnungen, die maßgebliche Grenze und den verbleibenden Spielraum
einschließlich offener Rechnungen, außerhalb des Gründungsjahrs auch den Vorjahresumsatz.

- Maßgebliche Grenze: im Gründungsjahr (Einstellung `INVOICES_FOUNDING_YEAR`) 25.000 € für das
  Gründungsjahr selbst, sonst 100.000 € für das laufende Jahr. Wird sie überschritten, endet die
  Kleinunternehmerregelung sofort; der Zahlungseingang, mit dem die Grenze überschritten wird, und
  alle späteren sind steuerpflichtig. Das Programm nennt diese Rechnung.
- Lag der Vorjahresumsatz über 25.000 €, gilt die Regelung im laufenden Jahr nicht.
- Warnungen: ab 80 % der maßgeblichen Grenze (offene Rechnungen eingerechnet), sobald das laufende
  Jahr 25.000 € übersteigt (dann gilt die Regelung im Folgejahr nicht), und wenn das Gründungsjahr
  nicht eingestellt ist.
- Sperre: Würden Eingänge, offene Rechnungen und die neue Rechnung zusammen die maßgebliche Grenze
  übersteigen, oder lag das Vorjahr über 25.000 €, wird die Rechnung abgelehnt. Eine Rechnung mit
  Hinweis auf § 19 UStG wäre für eine steuerpflichtige Leistung falsch, und das Programm kann noch
  keine Rechnungen mit Umsatzsteuer erstellen. Nur mit „Trotz Umsatzgrenze erstellen“ und einem Grund
  wird sie erstellt; Prüfergebnis und Grund stehen im Verlauf der Rechnung.
- Es zählen nur die im Programm erfassten Zahlungseingänge. Importierte Rechnungen (2.9) werden nicht
  gesperrt, zählen aber mit, sobald sie als bezahlt erfasst sind.

## 2.2 Zahlungseingang erfassen

Auf der Detailseite der Rechnung „Als bezahlt markieren“ mit Zahlungsdatum und Zahlungsart
(„Überweisung/Karte“ oder „Bar“). Barzahlungen sind am Tag des Eingangs zu erfassen (Rz. 48).
„Wieder auf offen setzen“ nimmt eine irrtümliche Erfassung zurück. Jede Änderung steht mit altem
und neuem Wert im Verlauf.

## 2.3 Rechnung stornieren

„Stornieren“ mit Pflichtangabe eines Grundes. Die Rechnung bleibt mit Nummer und PDF im Archiv, hat
den Status „Storniert“ und zählt nicht zu den Einnahmen. Die Nummer bleibt vergeben (keine Lücke).

Es gibt zwei Wege, je nachdem, ob der Kunde die Rechnung erhalten hat. Beide verlangen einen Grund
und sind nicht umkehrbar.

**Nicht versandt** (nur bei offenen Rechnungen): „Stornieren – wurde nicht versandt“. Die Rechnung
bekommt den Status „Storniert“, der Grund steht im Verlauf, es entsteht kein weiteres Dokument.

**Versandt: Stornorechnung erstellen.** Das Programm erzeugt ein eigenes Dokument:

- Es erhält die nächste Nummer desselben Nummernkreises, trägt den Titel „Stornorechnung“, verweist
  auf Nummer und Datum der Rechnung, zeigt den Betrag negativ und denselben Hinweis auf die
  Kleinunternehmerregelung wie die ursprüngliche Rechnung.
- Es wird wie eine Rechnung unveränderbar archiviert (Prüfsumme, Verlauf); beide Dokumente sind in
  beide Richtungen verlinkt, im Verlauf und in der Übersicht.
- Die ursprüngliche Rechnung erhält den Status „Storniert“ und behält Zahlungsdatum und
  Zahlungsart, falls sie bezahlt war. Ihr Status kann danach nicht mehr geändert werden.
- War die Rechnung **nicht** bezahlt, steht auf der Stornorechnung „Keine Erstattung“ und im PDF,
  dass die Rechnung gegenstandslos ist.
- War sie **bezahlt**, steht dort „Erstattung offen“ und im PDF, dass der Betrag erstattet wird.
  Nach der Auszahlung wird sie mit „Erstattung erfassen“ (Datum, Zahlungsart) auf „Erstattet“
  gesetzt. Die Erstattung mindert die Einnahmen im Jahr der Auszahlung; der ursprüngliche
  Zahlungseingang bleibt im Jahr seines Eingangs.
- Für die Umsatzgrenzen (§ 19 UStG) zählt der ursprüngliche Zahlungseingang weiter; Erstattungen
  werden nicht abgezogen, solange nicht geklärt ist, in welchem Jahr sie den Umsatz mindern (2.1).

Eine **bezahlte** Rechnung kann nicht ohne Stornorechnung storniert werden: Der Zahlungseingang ist
eine Tatsache und bleibt erhalten; rückgängig gemacht wird er nur durch eine Erstattung. War die
Zahlung irrtümlich erfasst, wird sie mit „Wieder auf offen setzen“ zurückgenommen (protokolliert).
Für eine früher ohne Dokument stornierte Rechnung kann eine Stornorechnung nachträglich erstellt
werden. Die Rechnungsliste meldet stornierte Rechnungen, die unmittelbar vor dem Stornieren als
bezahlt erfasst waren (Stand vor dieser Sperre), weil ihr Zahlungseingang in Übersicht und
Umsatzgrenze fehlt; die Meldung ändert nichts an den Daten.

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
4. Hinweis auf § 13b UStG: Das Programm merkt einen möglichen Fall der Steuerschuldnerschaft des
   Leistungsempfängers vor, wenn eine E-Rechnung die Steuerkategorie AE enthält, der
   Rechnungssteller laut E-Rechnung nicht in Deutschland sitzt oder der Belegtext Formulierungen
   wie „Reverse charge“ oder „Steuerschuldnerschaft des Leistungsempfängers“ enthält. Der Hinweis
   wird bei der Prüfung angezeigt; das Feld selbst wird nie automatisch gesetzt.
5. Der Beleg hat den Hinweis „Zu prüfen“, bis er einmal gespeichert wurde.

## 2.6 Beleg prüfen und kontieren

Auf der Detailseite Lieferant, Betrag (brutto, wie bezahlt), Rechnungsdatum, Rechnungsnummer des
Lieferanten, Kategorie, Status und – bei „Bezahlt“ – Zahlungsdatum und Zahlungsart prüfen oder
eintragen, dann „Geprüft, speichern“. „Speichern und nächster“ führt durch alle ungeprüften Belege.

- **Kategorie** ist Pflicht; sie ist die sachliche Zuordnung des Belegs (Rz. 50).
- **Zahlungsart:** „Überweisung/Karte“, „Bar“ oder „Privat bezahlt (Einlage)“ für Ausgaben, die aus
  privaten Mitteln bezahlt wurden.
- **Zahlungsdatum** leer bedeutet: bezahlt am Rechnungsdatum.
- **Anlagegut (AfA):** ankreuzen, wenn der Beleg ein abnutzbares Wirtschaftsgut betrifft, das nicht
  sofort abgezogen werden darf, weil der Nettopreis über 800 € liegt (GWG-Grenze, § 6 Abs. 2 EStG).
  Maßgeblich ist der Nettopreis, auch wenn keine Vorsteuer abgezogen wird. Ist der eingetragene
  (Brutto-)Betrag höher als 800 € und das Feld leer, zeigt das Formular einen Hinweis; speichern ist
  trotzdem möglich. Anlagegüter zählen nicht zu den Ausgaben der Übersicht (2.8). Das
  Anlagenverzeichnis und die Abschreibung werden außerhalb des Programms geführt (§ 4 Abs. 3
  Satz 5 EStG, Teil 6). Änderungen werden protokolliert.
- **Steuerschuldnerschaft des Leistungsempfängers (§ 13b UStG):** ankreuzen, wenn der Leistende die
  Umsatzsteuer nicht selbst berechnet und sie deshalb der Unternehmer schuldet, typischerweise bei
  Leistungen von Unternehmen im Ausland (Cloud-Dienste, Software, APIs). Das gilt auch für
  Kleinunternehmer (§ 13b Abs. 5 UStG). Ob ein Fall vorliegt, entscheidet der Unternehmer; ein
  Hinweis aus dem Hochladen (2.5) ist nur ein Anlass zur Prüfung. Änderungen werden protokolliert.
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

- Stornorechnungen stehen in der Rechnungsliste mit dem Kennzeichen „Storno“, negativem Betrag und
  dem Stand der Erstattung; der Filter „Stornorechnungen“ zeigt nur sie. Summen wie „Umsatz“,
  „Bezahlt“ und „Offen“ enthalten nur Ausgangsrechnungen; offene Erstattungen stehen getrennt.
- Rechnungsliste: Filter nach Jahr, Status, Suche in Nummer, Kunde, Leistung; Summen; überfällige
  Rechnungen markiert; Einnahmen und Ausgaben je Jahr nach Zahlungsdatum. Als Anlagegut markierte
  Belege sind nicht in den Ausgaben enthalten, sondern werden getrennt als „Anlagegüter“ gezeigt;
  der Überschuss heißt deshalb „Überschuss vor AfA“.
- Ausgabenliste: Filter „Nur Anlagegüter“; markierte Belege tragen das Kennzeichen „Anlagegut“.
- § 13b UStG (Ausgabenseite): Für das gewählte Jahr (ohne Auswahl das laufende) je Quartal und für
  das Jahr die Summe der als § 13b markierten, nicht verworfenen Belege als Bemessungsgrundlage,
  zugeordnet nach Rechnungsdatum als Näherung für den Leistungszeitraum, dazu 19 % als
  gekennzeichneter Orientierungswert. Das Programm berechnet keine Steuer und erstellt keine
  Anmeldung. Die an das Finanzamt gezahlte Steuer wird anschließend als gewöhnliche Ausgabe erfasst.
- Ausgabenliste: Filter „Nur § 13b UStG“; markierte Belege tragen das Kennzeichen „§ 13b“.
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
