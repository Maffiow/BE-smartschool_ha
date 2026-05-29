# Smartschool Results — Home Assistant integratie

Een onofficiële Home Assistant custom integratie om toetsresultaten op te halen van het [Smartschool](https://www.smartschool.be) schoolplatform.

## Functies

- **Sensor per vak** — automatisch aangemaakt voor elk vak met beschikbare resultaten
- **Volledige scorehistory** als attribuut, inclusief toetsnaam, datum en feedback
- **Gemiddelde score** per vak over het volledige schooljaar
- **Laatste 5 resultaten** (alle vakken) op de overzichtssensor
- Grafiekklaar voor [ApexCharts Card](https://github.com/RomRider/apexcharts-card) — kleuren per drempelwaarde (rood/geel/groen)
- Automatische herauthenticatie bij verlopen sessie

## Vereisten

- Een actief Smartschool-account
- De **geboortedatum** van de leerling (gebruikt voor de verplichte account-verificatiestap van Smartschool)
- Home Assistant 2023.1 of nieuwer

## Installatie

### Via HACS (aanbevolen)

1. Ga in Home Assistant naar **HACS → Integraties**
2. Klik op de drie puntjes rechtsboven → **Aangepaste repository toevoegen**
3. Voer de repository-URL in en kies categorie **Integratie**
4. Klik op **Installeren**
5. Herstart Home Assistant

### Handmatig

1. Kopieer de map `smartschool_ha` naar `<config>/custom_components/` op je Home Assistant installatie
2. Herstart Home Assistant

## Configuratie

1. Ga naar **Instellingen → Apparaten & diensten → Integratie toevoegen**
2. Zoek naar **Smartschool Results**
3. Vul de gegevens in:

| Veld | Beschrijving | Voorbeeld |
|:-----|:-------------|:----------|
| **Smartschool URL** | De basis-URL van de school | `https://mijnschool.smartschool.be` |
| **Gebruikersnaam** | Smartschool-login van de leerling | `jan.peeters` |
| **Wachtwoord** | Bijhorend wachtwoord | |
| **Geboortedatum** | Geboortedatum van de leerling (JJJJ-MM-DD) | `2010-03-15` |

> De geboortedatum is nodig omdat Smartschool bij elke nieuwe sessie een verificatiestap uitvoert. Dit is geen 2FA maar een vaste beveiligingsvraag van het platform.

## Beschikbare sensoren

Na configuratie maakt de integratie automatisch sensoren aan op basis van de gevonden vakken.

### Overzichtssensor

`sensor.smartschool_laatste_resultaat_<gebruikersnaam>`

| Attribuut | Beschrijving |
|:----------|:-------------|
| `vak` | Vaknaam van het laatste resultaat |
| `score` | Score als beschrijving (bv. `9/12`) |
| `score_percentage` | Numerieke score (bv. `75.0`) |
| `datum` | Datum van de toets |
| `beschikbaar_sinds` | Datum waarop het resultaat zichtbaar werd |
| `feedback` | Eventuele feedback van de leerkracht |
| `recente_resultaten` | Lijst van de laatste 5 resultaten (alle vakken) |

### Vaksensoren

`sensor.smartschool_<vaknaam>_<gebruikersnaam>`

De sensorwaarde (state) is de meest recente numerieke score (in %) of de scorebeschrijving als er geen percentage beschikbaar is.

| Attribuut | Beschrijving |
|:----------|:-------------|
| `toets` | Naam van de meest recente toets |
| `score` | Score als beschrijving |
| `score_percentage` | Numerieke score |
| `datum` | Datum van de toets |
| `beschikbaar_sinds` | Beschikbaarheidsdatum |
| `feedback` | Feedback van de leerkracht |
| `gemiddelde_score` | Gemiddelde van alle numerieke scores dit schooljaar |
| `aantal_toetsen` | Aantal toetsen met numerieke score |
| `score_history` | Volledige lijst van alle toetsen (gesorteerd van oud naar nieuw) |

#### Structuur van `score_history`

```json
[
  {
    "datum": "2025-10-14",
    "toets": "Hoofdstuk 3 — algebraïsche uitdrukkingen",
    "score": 82.5,
    "score_omschrijving": "33/40",
    "feedback": null
  }
]
```

## Dashboard

De vaksensoren zijn direct bruikbaar met [ApexCharts Card](https://github.com/RomRider/apexcharts-card) (via HACS).

### Voorbeeld: balkgrafiek per vak met kleurdrempels

```yaml
type: custom:apexcharts-card
header:
  title: Wiskunde
  show: true
graph_span: 10months
yaxis:
  - min: 0
    max: 100
    decimals: 0
series:
  - entity: sensor.smartschool_wiskunde_jan_peeters
    type: column
    data_generator: |
      const now = new Date();
      const syYear = now.getMonth() >= 8 ? now.getFullYear() : now.getFullYear() - 1;
      const syStart = new Date(syYear, 8, 1).getTime();
      return entity.attributes.score_history
        .map(h => [new Date(h.datum).getTime(), parseFloat(h.score)])
        .filter(p => !isNaN(p[1]) && p[0] >= syStart);
    color_threshold:
      - value: 0
        color: "#e53935"
      - value: 50
        color: "#fdd835"
      - value: 60
        color: "#43a047"
    show:
      in_header: true
```

- Rood: score onder 50%
- Geel: score 50–59%
- Groen: score 60% of hoger

### Voorbeeld: tabel met laatste 5 resultaten

```yaml
type: markdown
content: |-
  ## Laatste 5 resultaten
  {% set r = state_attr('sensor.smartschool_laatste_resultaat_jan_peeters', 'recente_resultaten') %}
  {% if r and r | length > 0 %}
  {% set ns = namespace(rijen=['| Datum | Vak | Toets | Score |', '|:------|:----|:------|------:|']) %}
  {% for item in r %}
  {% set ns.rijen = ns.rijen + ['| ' + (item.availability_date or item.date or '?')[:10] + ' | ' + (item.course or '?') + ' | ' + (item.name or '?') + ' | **' + (item.score_description or (item.score_value | string) or '?') + '** |'] %}
  {% endfor %}
  {{ ns.rijen | join('\n') }}
  {% else %}
  _Geen recente resultaten beschikbaar._
  {% endif %}
```
<img width="1503" height="1000" alt="image" src="https://github.com/user-attachments/assets/b43306e5-e990-4830-847d-e89a8fb67b56" />

## Opmerkingen

- De integratie pollt standaard elke **15 minuten**
- Smartschool vergt een account-verificatie (geboortedatum) bij elke nieuwe sessie; dit is ingebouwd en automatisch
- Per API-aanroep worden maximaal 50 resultaten per pagina opgehaald; alle pagina's worden doorlopen
- Deze integratie is **niet officieel** en heeft geen band met Smartschool/Scala BVBA

## Licentie

MIT License
