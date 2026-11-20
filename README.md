# fhir-portal-test

Testwebbrot för Ineras FHIR-IG:er (serveras på https://inera-ab.github.io/fhir-portal-test/) och
verktyg för att testa publicering innan något når [fhir.inera.se](https://fhir.inera.se).
Under rubriken *fhir-portal* nedan beskrivs webbroten som den ser ut i `inera-ab/fhir-portal`.

## Testverktyg

Tre workflows som startas manuellt under **Actions → Run workflow**. Rapporterna hamnar i
körningens sammanfattning, och för jämförelserna även som sidor på gh-pages här.

| Workflow | Vad den testar | Resultat |
|---|---|---|
| **Test: länkar mellan IG:er** (`test-cross-ig-links.yml`) | Bygger en vald IG (repo + branch) och kontrollerar att länkar till `fhir.inera.se/ig/…` – till IG:ns egna publicerade sidor, till andra IG:er och till katalogen – och Inera-beroenden i `dependsOn` finns i den valda webbroten (`fhir-portal` eller `fhir-portal-test`). Länkar till IG:ns rot utan versionsmapp flaggas om publiceringen inte är en milstolpe (då flyttas inte roten). | Sammanfattning; röd körning om något saknas |
| **Test: jämför IG-versioner** (`test-compare-versions.yml`) | Jämför två byggen sida för sida. Varje sida är en mapp på en gh-pages-branch: en publicerad version (`inera-ab/fhir-portal`, `ig/tjanstekatalog/0.2.0`) eller ett branchbygge (`inera-ab/tjanstekatalog`, tom sökväg = main, `branches/<namn>`). | `…/fhir-portal-test/compare/<namn>/`: index över ändrade, nya och borttagna sidor; varje ändrad sida visas i den nya versionens layout med tillagd text grön och borttagen text överstruken, som spåra ändringar |
| **Test: ny IG Publisher-version** (`test-publisher-version.yml`) | Bygger en test-IG (standard `Inera-AB/IneraFHIRTemplate`) två gånger från samma commit: med versionen som är låst i `fhir-ig-workflows@v1` och med en ny (`latest` eller t.ex. `3.0.6`). Jämför QA-rapporterna (antal och nya/borttagna meddelanden) och alla sidor. | `…/fhir-portal-test/publisher-test/<ig>-<gammal>-vs-<ny>/`: QA-jämförelse (`qa.md`), spåra ändringar-sidor, byggloggar; röd körning om bygget med den nya versionen misslyckas |

Bra att veta:

- Båda byggena i publisher-testet använder samma IG-mall (`fhir2.base.template#current`), så en
  ändring i mallen syns inte där. Den fångas i stället av *jämför IG-versioner*, t.ex. ett
  branchbygge från i dag mot ett från i går eller mot senaste publicerade version.
- Branchbyggen på ett IG-repos gh-pages publiceras med `keep_files`, så sidor som tagits bort ur
  IG:n ligger kvar där och visas som *nya* i en jämförelse mot en publicerad version.
- IG-repon och webbrötter läses anonymt, så de måste vara publika.
- Skripten finns i `scripts/` (Python 3, `lxml` för sidjämförelsen) och testas av
  `test-scripts.yml`: `python3 -m unittest discover -s scripts`.

# fhir-portal

Hub-repo för Ineras FHIR Implementation Guides, publicerade på **[fhir.inera.se](https://fhir.inera.se)**.

## Arkitektur

```
inera-ab/fhir-portal   (gh-pages branch)
  index.html             ← Ineras huvudlandningssida (hanteras manuellt)
  ig/
    index.html           ← IG-katalog, auto-genererad från registry.json
    registry.json        ← maskinläsbar källa, uppdateras av CI-dispatch
    IneraFHIRTemplate/
      history.html
      1.0.0/             ← IG-output publicerad av IG-repots CI
    inera-laboratory/
      history.html
      1.0.0/
```

Varje IG-repo bygger sin guide och pushar output till `gh-pages/ig/{ig-namn}/{version}/`.
`registry.json` uppdateras via `repository_dispatch`, och `ig/index.html` regenereras automatiskt.

## Hur ett IG-repo publicerar hit

### 1. Skapa en PAT med rätt behörigheter

Skapa en [Fine-grained PAT](https://github.com/settings/personal-access-tokens/new) för kontot/organisationen med:
- **Repository access:** `inera-ab/fhir-portal`
- **Permissions → Contents:** Read and write

Spara PAT:en som en Actions-hemlighet i IG-repot, t.ex. `FHIR_PORTAL_TOKEN`.

### 2. Lägg till dessa steg i IG-repots workflow

```yaml
- name: Publicera IG till fhir-portal
  uses: peaceiris/actions-gh-pages@v4
  with:
    personal_token: ${{ secrets.FHIR_PORTAL_TOKEN }}
    external_repository: inera-ab/fhir-portal
    publish_branch: gh-pages
    publish_dir: ./output
    destination_dir: ig/${{ github.event.repository.name }}/${{ env.IG_VERSION }}
    keep_files: true               # viktigt – radera inte andra IG:ar

- name: Uppdatera IG-registret på fhir-portal
  env:
    GH_TOKEN: ${{ secrets.FHIR_PORTAL_TOKEN }}
  run: |
    gh api repos/inera-ab/fhir-portal/dispatches \
      --method POST \
      -f event_type=ig-deployed \
      -f "client_payload[ig_name]=${{ github.event.repository.name }}" \
      -f "client_payload[ig_title]=${{ env.IG_TITLE }}" \
      -f "client_payload[ig_description]=${{ env.IG_DESCRIPTION }}" \
      -f "client_payload[ig_version]=${{ env.IG_VERSION }}" \
      -f "client_payload[is_release]=${{ github.event_name == 'release' }}"
```

### Miljövariabler att sätta i IG-repots workflow

Läs dem från `ImplementationGuide-{package-id}.json` i output-katalogen:

```yaml
- name: Läs IG-metadata
  run: |
    MANIFEST=output/package.manifest.json
    PKG_ID=$(jq -r '.name' "$MANIFEST")
    echo "IG_VERSION=$(jq -r '.version' "$MANIFEST")" >> $GITHUB_ENV
    IG_FILE="output/ImplementationGuide-${PKG_ID}.json"
    echo "IG_TITLE=$(jq -r '.title' "$IG_FILE")"       >> $GITHUB_ENV
    echo "IG_DESCRIPTION=$(jq -r '.description' "$IG_FILE")" >> $GITHUB_ENV
```

### Parametrar för `repository_dispatch`

| Fält | Beskrivning |
|---|---|
| `ig_name` | Mappnamn under `/ig/` — ska matcha repo-namnet |
| `ig_title` | Visningsnamn i IG-katalogen |
| `ig_description` | Kort beskrivning |
| `ig_version` | Versionsnummer, t.ex. `1.0.0` |
| `is_release` | `true` för release-taggar, `false` för branch-byggen |

`is_release: true` gör att `latest_version` i `registry.json` uppdateras och att ett versionsnummer visas i IG-katalogen. Branch-byggen skriver inte över `latest_version`.

## GitHub Pages-inställningar

Settings → Pages → Source: **Branch `gh-pages` / `(root)`**

Custom domain: `fhir.inera.se`

> **DNS:** Lägg till en CNAME-post i Ineras DNS som pekar `fhir.inera.se` → `inera-ab.github.io`.
