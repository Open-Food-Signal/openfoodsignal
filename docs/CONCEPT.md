Demonstrator: Verfügbarkeits-Ampel Olivenöl
Arbeitstitel des Gesamtprojekts: FoodGrid – ein offenes Protokoll für Lebensmittel-Verfügbarkeitssignale
Zweck dieses Demonstrators: Mit einem einzigen Lebensmittel beweisen, dass sich aus offenen Daten ein verständliches, belastbares und maschinenlesbares Verfügbarkeitssignal erzeugen lässt.

1. Was der Demonstrator zeigt
Eine öffentlich zugängliche Website mit drei Elementen:

Die Ampel: Ein Verfügbarkeitsindex für Olivenöl (Skala 0–100, dargestellt als Grün/Gelb/Rot), wöchentlich aktualisiert.
Das "Warum": Ein automatisch erzeugter Erklärtext in einfacher Sprache, z. B. "Gelb: Die Lagerbestände in Spanien liegen 18 % unter dem Fünfjahresschnitt, aber die Niederschläge in Andalusien während der Blüte lassen eine gute Ernte 2026/27 erwarten."
Die offene API: Dasselbe Signal als JSON-Datei – der erste Entwurf des offenen Protokolls (food-availability-signal v0.1), das später jede App, jedes Händlersystem und jede Rezeptplattform konsumieren kann.

Als Kontrast wird ein Mini-Index für Rapsöl (regionales Substitut) daneben gestellt – damit ist die Substitutionslogik des späteren Copiloten bereits angedeutet: "Olivenöl gelb, Rapsöl grün."
2. Offene Datenquellen
QuelleWas sie liefertAktualisierungEU Agri-Food Data Portal (agridata.ec.europa.eu)Wöchentliche Erzeugerpreise in 20+ EU-Märkten (Daten ab 2010), Produktion und Jahresend-Lagerbestände je Mitgliedstaat, wöchentliches Olivenöl-DashboardwöchentlichEU-Marktbeobachtungsstelle Olivenöl (seit 11/2024)Marktberichte, Bilanzen, Kurzfristprognosen (3×/Jahr)laufendInternational Olive Council (IOC) Statistics DashboardWeltweite Produktion, Verbrauch, Export/Import seit 1990/91; Erzeugerpreise Jaén, Bari, ChaniamonatlichEurostat COMEXTAußenhandelsströme (Import/Export je Land)monatlichCopernicus (ERA5, Sentinel-2)Niederschlag, Bodenfeuchte, Vegetationsindex (NDVI) für die Anbauregionen Andalusien, Extremadura, Apulien, Kreta, Tunesientäglich/wöchentlichAICA (Spanien) / PoolredSpanische Produktions- und Bestandsdaten, Referenzpreisemonatlich/wöchentlich
Alle Quellen sind frei zugänglich. Genau das ist die These des Projekts: Das Signal existiert bereits in verstreuten Expertendaten – es wurde nur noch nie zu einem Bürgersignal verdichtet.
3. Berechnungslogik: Komposit-Index aus vier Säulen
Jede Säule wird auf 0–100 normalisiert (100 = beste Verfügbarkeit), dann gewichtet zusammengeführt.
Säule A – Bestandsdeckung (Gewicht ~35 %)
Stocks-to-Use-Ratio: Lagerbestände geteilt durch erwarteten Jahresverbrauch, verglichen mit dem Fünfjahresschnitt. Der wichtigste Fundamentalindikator für jede Rohstoffknappheit.
Säule B – Preissignal (Gewicht ~25 %)
Aktueller Erzeugerpreis (z. B. Jaén, extra vergine) als Abweichung vom inflationsbereinigten Fünfjahresschnitt (z-Score). Der Preis ist der schnellste Knappheitsproxy, aber bewusst nicht die einzige Säule – sonst wäre der Index nur ein Preisticker.
Säule C – Ernteprognose (Gewicht ~30 %)
Wetter- und Vegetationsdaten der Hauptanbauregionen, gewichtet nach Produktionsanteil und phänologischer Phase. Kritisch ist die Blüte im Mai/Juni: Niederschlag und Hitzetage in diesem Fenster bestimmen die Ernte des Folgejahres. Das macht den Index vorausschauend statt nur beschreibend – der eigentliche Mehrwert gegenüber dem Supermarktpreis.
Säule D – Handelsstress (Gewicht ~10 %)
Exportbeschränkungen, ungewöhnliche Sprünge in den Handelsströmen (COMEXT), Frachtkosten-Anomalien. Anfangs regelbasiert (manuell gepflegte Ereignisliste), später automatisiert.
Die Gewichte sind Startwerte und werden im Backtest kalibriert. Die gesamte Methodik ist offen dokumentiert und versioniert – die Glaubwürdigkeit des Index ist sein einziges Kapital, und Glaubwürdigkeit entsteht durch Nachrechenbarkeit.
4. Validierung: der Backtest als Herzstück
Der Index wird rückwirkend ab 2015 berechnet. Die zentrale Prüffrage: Hätte er die Olivenöl-Krise 2022–2024 (Dürre in Spanien, Preisverdreifachung) frühzeitig angezeigt – und wie viele Monate vor dem Regalpreis?
Die Datenlage erlaubt das: EU-Preisreihen ab 2010, IOC-Bilanzen ab 1990, Copernicus-Wetterdaten vollständig. Ein gelungener Backtest ("der Index sprang im Sommer 2022 auf Rot, sechs Monate bevor der Supermarktpreis explodierte") ist das stärkste Exponat für Förderanträge und Mitgliederwerbung.
Der Zeitpunkt ist zudem günstig: Der Markt hat sich 2025/26 entspannt – der Index stünde heute auf Grün/Gelb und rückblickend für 2023 auf Tiefrot. Das demonstriert beide Zustände. Und die Blüte Mai/Juni 2026 liefert ein Live-Ereignis, bei dem Säule C in Echtzeit arbeitet.
5. Technische Umsetzung (bewusst minimal)

Datenpipeline: Python-Skripte, die die Quellen wöchentlich abrufen und den Index berechnen
Scheduler: GitHub Actions (kostenlos, transparent, jeder Lauf öffentlich einsehbar)
Veröffentlichung: Statische Website (GitHub Pages o. ä.) + JSON-Dateien als API – kein Server, keine Datenbank, keine laufenden Kosten
Repository: Alles offen (Code: AGPL, Daten/Index: ODbL), reproduzierbar mit einem Befehl
Aufwand: Realistisch 6–10 Wochenenden für eine Person mit Python-Grundkenntnissen; als Open-Source-Projekt ausgeschrieben auch gut auf 3–4 Mitstreiter aufteilbar

6. Das Protokoll-Schema (v0.1, Entwurf)
json{
  "signal_version": "0.1",
  "commodity": "olive-oil",
  "scope": "EU",
  "timestamp": "2026-06-08",
  "availability_index": 61,
  "traffic_light": "yellow",
  "trend_30d": "improving",
  "pillars": {
    "stocks": 48,
    "price": 55,
    "harvest_outlook": 78,
    "trade_stress": 90
  },
  "explanation_de": "Bestände unter Fünfjahresschnitt, gute Blütebedingungen 2026.",
  "substitutes": [
    { "commodity": "rapeseed-oil", "scope": "DE", "availability_index": 88 }
  ],
  "methodology_url": "...",
  "license": "ODbL-1.0"
}
Dieses Schema ist der Keim des offenen Standards – das, was im Stromsektor die Netzfrequenz und im ÖPNV GTFS ist.
7. Fahrplan

Repo anlegen, Methodik-Dokument v0.1 schreiben (die vier Säulen)
Pipeline für zwei Quellen (EU-Preise + IOC-Bilanzen) → erster grober Index
Copernicus-Säule ergänzen → Backtest 2015–2026 rechnen
Dashboard + API veröffentlichen, Rapsöl-Kontrastindex ergänzen
Förderantrag (z. B. Prototype Fund) mit Backtest als Exponat; parallel erste Mitstreiter über das offene Repo gewinnen
Danach: zweites Lebensmittel (Kakao oder Reis – beide mit bekannten Knappheitsgeschichten), Schema zu v0.2 verallgemeinern
