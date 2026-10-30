# pdf-tagfix

Et værktøj, der retter tilgængelighedsfejl i PDF'er, som **allerede har tags**.
Det laver ikke tags fra bunden. Det analyserer den eksisterende tagstruktur, foreslår
rettelser (fx tekst, der ser ud som en overskrift, men er tagget som `P`), lader et
menneske gennemgå forslagene og skriver de godkendte rettelser tilbage i PDF'en.
Til sidst valideres resultatet med veraPDF (PDF/UA).

Status: første trin. Indtil videre kan værktøjet kun udskrive tag-træet.

## Installation

Kræver Python 3.11+.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install --only-binary=pikepdf -e ".[dev]"
```

`--only-binary=pikepdf` sikrer, at pip bruger en færdigbygget wheel. Uden flaget
kan pip vælge en nyere version, der kun findes som kildekode, og så fejler
byggeriet, fordi qpdf-headerne mangler. Det sker fx på Intel-Macs med Python 3.14.

## Brug

### Udskriv tag-træet

```sh
pdf-tagfix-tree samples/rapport.pdf
# eller
python -m pdf_tagfix.dump_tree samples/rapport.pdf
```

Eksempel på output:

```
rapport.pdf  (1 sider, sprog: da)

Document  lang="da"
  H1
    NonStruct  s.1  "Årsrapport for æblehaven"  [24pt, fed]
  P
    NonStruct  s.1  "Dette er brødtekst med"  [12pt, ikke-fed]
    Strong
      NonStruct  s.1  "fed tekst"  [12pt, fed]
  NonStruct
    NonStruct  s.1  "Falsk overskrift som bare er fed tekst"  [16.5pt, fed]
  Figure  s.1  alt="Et billede"

MCID'er uden indhold: 0   indhold uden tag (ikke refereret): 0
```

Hver linje viser:

- **tag-type**. Ved brugerdefinerede typer vises også standardtypen fra RoleMap, fx `Overskrift → H1`.
- **side**
- **tekst** fra elementets egne marked-content-sekvenser (MCID'er). Tekst i
  underelementer vises på underelementets egen linje.
- **skriftstørrelse** i punkter, som den faktisk vises (`Tf`-størrelsen ganget med
  tekst- og transformationsmatricen). Ved blandede størrelser kommer den
  dominerende først, fx `12pt/9pt`.
- **fed / ikke-fed / delvis fed**. Det afgøres ud fra fontnavnet (Bold, Black,
  Semibold …), `/FontWeight`, ForceBold-flaget og "falsk fed" (render mode 2).
- `alt`, `actualtext`, `lang` og `titel`, hvis de er sat.

Linjer der starter med `!` er MCID'er, som tag-træet peger på, men som ikke findes
i sidens indhold.

Flag:

| Flag | Betydning |
|------|-----------|
| `--max-text N` | afkort tekst til N tegn (0 = ingen afkortning, standard 80) |
| `--show-mcid` | vis hver MCID-sekvens for sig, med egen størrelse og vægt |
| `--json` | udskriv træet som JSON (til videre behandling) |

### Validering med veraPDF

```sh
~/verapdf/verapdf --flavour ua1 --format text fil.pdf
```

## Test

```sh
pytest
```

Testene bygger deres egne PDF'er og har ikke brug for filer i `samples/`.

## Mapper

- `src/pdf_tagfix/` er koden.
- `tests/` er testene.
- `samples/` er dine egne test-PDF'er. Mappen er i `.gitignore`, fordi PDF'erne kan indeholde fortrolige data.
