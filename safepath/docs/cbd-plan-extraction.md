# CBD Evacuation Plan (2007) extraction summary

> Historical reference (2007), not a current evacuation order

Source: https://webapps1.chicago.gov/ChicagoAlertWeb/resources/pdf/CBD_Evac_Plan_final_30-5-07.pdf
Plan date: 2007-05-16 (Page headers read "05-16-07"; the cover reads "Additions: 29 May 07".). Pages: 18.
Acquisition: freshly downloaded. Classification: Historical. All records are `cbd_derived=true`, `active=false`, never an Official_Notice.

Local copy: No copyright, licence or reuse statement appears in the PDF text, and no explicit reuse licence was found for it on chicago.gov (checked via web search). Storage permission is therefore NOT confirmed: the PDF is kept only as a git-ignored local cache (data/raw/cbd_plan/) for extraction and is never committed or redistributed. Only short attributed excerpts with page references are stored under data/sources/.

Records: 87 short verbatim excerpts, each with PDF page and section heading, in `data/sources/cbd_plan_2007.json`.

## Topics

| Topic | Status | Records | Pages |
|---|---|---|---|
| Evacuation Level I and activation conditions | found | 8 | 4 |
| Evacuation Level II and activation conditions | found | 10 | 5 |
| Evacuation Level III and activation conditions | found | 17 | 6, 7 |
| Shelter-in-place versus evacuation criteria | not found | 0 | - |
| Activation and evacuation authority | found | 5 | 3, 8 |
| CBD boundaries | found | 1 | 2 |
| Hot and cold zone definitions | found | 3 | 5, 6, 9 |
| Transportation coordination | found | 3 | 4, 5, 11 |
| Road and expressway closure procedures | found | 6 | 6, 10, 12, 15 |
| CTA and Metra coordination | found | 6 | 14, 15 |
| Assembly and Transportation Centers (ATC) | found | 4 | 3, 4 |
| Specific ATC locations | not found | 0 | - |
| Temporary shelters | found | 3 | 4, 11 |
| Specific temporary shelter locations | not found | 0 | - |
| Communication procedures | found | 6 | 8, 9 |
| Agency role: OEMC | found | 5 | 2, 10, 11 |
| Agency role: Chicago Police Department | found | 4 | 2, 9, 10 |
| Agency role: Chicago Fire Department | found | 3 | 11 |
| Agency role: transportation agencies (CDOT, IDOT) | found | 3 | 13, 15 |

16 of 19 topics have extracted text.

## Not found / not extracted

- **Shelter-in-place versus evacuation criteria**: not found. The plan names shelter-in-place but states no criteria for choosing it over evacuation.
- **Specific ATC locations**: not found. No matching text located in the plan PDF.
- **Specific temporary shelter locations**: not found. No matching text located in the plan PDF.
- Per-agency participation matrices for Levels I-III (Immediate Response / Event +2 Hours / Event +8 Hours): the dot columns are graphics and do not extract as text, so which agency participates at which time is NOT recorded.
- Boundary geometry: the plan gives street names only; no coordinates are inferred.
- Contents of the four appendices (not in the PDF, not publicly located).

## CBD boundary (approximate)

Division Street (north), Roosevelt Road (south), Lake Michigan (east), Halsted Street (west), p. 2, section Purpose. Labeled approximate; no coordinates.

## Appendix and newer-version search records

- CPD Central Business District Evacuation Deployment Plan (Appendix): searched 2026-10-24, not located. Content left unpopulated.
- OEMC Central Business District Evacuation Deployment Plan (Appendix): searched 2026-10-24, not located. Content left unpopulated.
- CTA Central Business District Evacuation Plan (Appendix): searched 2026-10-24, not located. Content left unpopulated.
- State of Illinois Evacuation Plan for Chicago Central Business District & Chicago Expressway System (IDOT Appendix): searched 2026-10-24, not located. Content left unpopulated.
- Newer version of the Chicago CBD Evacuation Plan (after 2007): searched 2026-10-24, not located. Content left unpopulated.

Notes: text is quoted as extracted by pypdf (a few characters such as apostrophes extract as symbols). The plan misspells ATC as "ACTs" once (p. 4). Phone numbers and procedures are 2007 values and may be out of date.
