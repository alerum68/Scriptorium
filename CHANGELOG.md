# Changelog

This project is in active testing and has not reached a stable 1.0 release. Version numbers follow
`MAJOR.MM.PP` - minor and patch are always two digits (e.g. `0.07.00`) so tags keep sorting correctly
once either number passes 9. The `MM` number increases for new tools or features, `PP` for fixes and
cleanup with no new functionality.

## [Unreleased]

### Added
- **Dynamic zero-padding for Voyageur gather output**: `Commissioner/textutils.py` gained `dynamic_zero_pad_all_except`, which zero-pads numeric ID/reference fields (e.g. `family_number`, `line_number`, `claim_number`) to the widest value seen across a whole gather run, so lexicographic sort matches numeric order. An explicit exclusion list keeps archival citation fields (`folio`, `volume`, `reel_numbers`), routing/matching fields (`enumeration_district`, `rg_series_code`, `commission_reference`), file/record linkers (`page_id`, `image_id`, etc.), dates, and free text untouched. Wired into `Voyageur/A.py`, `FS.py`, `HBCA.py`, and `LAC.py` at their write points, scoped to the whole file/master DB rather than a single page.
- **Convention-based image directories**: Image source folders are now inferred automatically from the active prompt file name (`Media/<prompt_name>` — e.g. `Media/Parish`, `Media/Scrip`, `Media/Census`, `Media/HBCA`). No user configuration required.
- **Scrip tab gets its own GEDCOM setting**: `SCRIP_GEDCOM_NAME` added to the Scrip Information section in Paleographer settings, so Scrip and Parish each manage their own output files independently.
- **HBCA Keystone toggles**: `HBCA_RESOLVE_KEYSTONE` and `HBCA_DOWNLOAD_KEYSTONE_MEDIA` now render as on/off toggle switches in the Voyageur settings UI.

### Changed
- **License**: Switched from the PolyForm Noncommercial License 1.0.0 to the GNU General Public License v3.0.

### Removed
- **`CENSUS_IMAGE_DIR` and `IMAGE_EXTENSION` global settings**: Removed from Antiquarian's Global Settings UI — image directories are now resolved automatically.
- **`CHURCH_IMAGE_DIR` and `SCRIP_IMAGE_DIR` settings**: Removed from Paleographer settings schema — image paths are derived from the active `.pmt` prompt name at runtime.

### Fixed
- **Voyageur/A.py docstring**: Fixed escaped triple-quote syntax error introduced by a prior edit.


- **Archivist Structural Split**: `Archivist.py` (3,691 lines) split into a thin
  dispatcher plus `Utils.py`, `Census.py`, `General.py`, and `Scrip.py`, replacing
  the file's `is_scrip` boolean-flag branching with a `Profile` strategy-pattern
  polymorphism (`GeneralProfile`/`ScripProfile`), mirroring the dispatcher shape
  already established by `Voyageur.py`/`Paleographer.py`. No GEDCOM output change
  from the split itself, verified by golden-file regression fixtures.
- **Archivist (citation headers)**: the labels prefixed to translated/transcribed
  citation text (`CITATION_DETAIL`/`CITATION_TEXT`, formerly hardcoded
  `TRANSLATION_HEADER`/`TRANSCRIPTION_HEADER`) are now optional and blank by
  default — when unset, the text renders without a label line instead of always
  showing "Citation Details:"/"Citation Text:". An existing `.env` with the old
  `TRANSLATION_HEADER`/`TRANSCRIPTION_HEADER` vars still set keeps working as-is
  (read as a fallback when the new vars are blank/unset) — no action needed, but
  re-entering the same text under `CITATION_DETAIL`/`CITATION_TEXT` in
  Antiquarian's settings is recommended so the old var names can eventually be
  removed from your `.env`.
- **Unified Pipeline Architecture**: `Paleographer.py` and `Archivist.py`
  are unified as self-contained standalone execution entrypoints with folded sub-modules
  and embedded source templates, ensuring zero runtime sibling-import failure while keeping
  individual subfiles for modularity and test isolation.
- **Voyageur Dispatcher Consolidation**: `Voyageur.py` rewritten as a thin dispatcher to
  `A.py`, `FS.py`, and `LAC.py`'s real `main()` functions. `_retry_utils.py` renamed to
  `_gather_helpers.py` with shared gather-boilerplate now used by both `A.py` and `FS.py`.
  Fixed `Antiquarian.py`'s LAC dispatch to pass the `volume`/`reel` subcommand token
  `LAC.py`'s argparse requires.
- **Commissioner Integration**: folded `Commissioner` into `Voyageur` (for LAC gathering,
  volume harvesting, and search scraping) and `Paleographer` (for Scrip metadata enrichment,
  name resolution, citation cleaning, and archival collection partitioning), streamlining
  the pipeline into three core stages: Gathering (`Voyageur`) -> Analysis/Enrichment (`Paleographer`)
  -> GEDCOM Compilation (`Archivist`). Removed the standalone Commissioner tab from `Antiquarian.py`.
- **Cross-Source Merge Concept**: archived dual-source census merging prototype (`Merged.py`,
  `MergedCensus.py`, `test_merged_census.py`) into `DEV/merged_concept/` and catalogued on the
  roadmap for future development.
- **Voyageur (FamilySearch)**: full-resolution image download for census gathers, matching
  Ancestry's own per-page image capture. FamilySearch's deepzoomcloud storage endpoint
  blocks direct access from the record page's own origin across every mechanism tried
  (`GM_xmlhttpRequest` hangs to timeout; a plain cross-origin `fetch()` returns 429 even on
  a brand-new item never requested before; a plain cross-origin `<a download>` triggers
  nothing) - but a genuine top-level navigation to the same URL always works (confirmed:
  it's what the site's own "Print" button does). Voyageur.js now loads the image into a
  hidden iframe (a real navigation, not blocked by any of the above, and without the
  popup-blocker issue `window.open()` hits since it's not a trusted user gesture); a second
  `@match` rule runs a small helper inside that iframe, fetching same-origin and handing
  the raw bytes back to the parent page via `postMessage` - the actual download click
  happens in the parent's own context, since triggering it from inside the iframe silently
  produces nothing despite the fetch succeeding. `FS.py` collects and moves these images
  into the same nested `{year} US Federal Census / {location}` folder structure `A.py`
  already uses.
- **Antiquarian (settings shell)**: left sidebar navigation replacing the old top-tab
  strip (Voyageur/Paleographer/Archivist ungrouped, a labeled Utilities group for
  Registrar/Gazetteer/PDFix, Help and Global Settings pinned to the bottom), a custom
  dark theme with Georgia headings and role-tag pills per tool, and richer field widgets
  (toggles, sliders, segmented controls) in place of plain text entries for the settings
  that are really booleans or bounded numbers. The console is now a pop-up that raises
  over the active tab's body the moment a script starts (leaving that tab's own title
  visible above it) instead of a permanently-docked drawer, with a bigger progress bar
  and a live percentage readout. The status bar shows the outcome of the run that just
  finished (`FS.py: finished successfully` / `cancelled` / `failed (exit code ...)`)
  instead of reverting to a generic "System Ready" the instant it ends. Each tool's own
  script path is now hardcoded (`SCRIPT_PATHS`) instead of a Global Settings field - the
  codebase's own folder layout doesn't vary per user or need configuring. Global
  Settings' "Metadata & Organization" and "Standard Links" sections are merged into one
  "Researcher & Organization" card.

### Fixed
- **Voyageur (HBCA)**: fixed a crash during the HBCA gather run caused by `HBCA_IMAGE_DIR` missing from Antiquarian's internal `nested_dir_keys` resolution list. The setting was correctly resolving to `MEDIA_DIR/HBCA` inside `HBCA.py` standalone, but failing with a `KeyError` when launched via the Antiquarian GUI.
- **Archivist (census GEDCOM)**: a `NAME` line was being written as a direct child of a
  `SOUR` citation (both inside each person's own citation block and, duplicated one level
  shallower, inside `_TASK` records) - `NAME` isn't a legal `SOURCE_CITATION` child under
  GEDCOM 5.5.1, and confirmed live against a real RootsMagic-authored GEDCOM import into
  Family Tree Maker, this caused FTM to reject that line and cascade to rejecting the
  `NOTE` line right after it too (~85 "Unsupported or invalid tag" errors on one real
  test census page). A second, separate, TID-less `_TMPLT` block was also being duplicated
  at the per-citation level - the real, working Evidence-Explained-style template (using
  RootsMagic's own built-in era-matched TIDs) already lives once on the `SOURCE` record
  itself (`get_census_sources`); a citation only needs to reference it via `SOUR`/`PAGE`.
  Both removed. `_TASK`/`_FOLDER` records (RootsMagic's own Task List feature, with no FTM
  equivalent) are now only emitted for RM output - FTM rejected them outright ("Invalid
  record type"). The census flavor's `_FOLDER` value now maps through the same
  fixed-vocabulary keyword mapper (`evaluate_task_priority`) the church flavor already used
  for this, instead of writing a lightly-cleaned raw review-flag sentence RM's importer
  would reject. Fixed an `OBJE` media filename getting a doubled extension
  (`4211353_00003.jpg.jpg`) in the unified-schema census path, baked into the `@M...@`
  pointer ID itself - the extension is no longer re-appended when the source data already
  supplies one. Added a `FamilySearch Family Tree` `_WEBTAG`/`_LINK` profile link
  (`https://www.familysearch.org/tree/person/details/{fsftid}`) alongside the existing
  `_FSFTID` tag, for both the census and church flavors - previously only the
  record/citation-level FamilySearch ark URL was ever emitted, so there was no actual link
  to the person's own FS Tree profile to begin with.
- **Antiquarian (UI polish)**: primary action buttons (Generate GEDCOM, Run Script, Gather,
  Clear Cache, ...) passed a custom `fg_color` but no `text_color`, silently inheriting the
  theme's dark default text meant for its light default `fg_color` - near-black-on-green/
  red/blue read as "grayed out and disabled." All now set an explicit light `text_color`.
  A collapsible settings-section header (`▼`/`▶`) could flip open then immediately snap
  shut again on the very first click of a fresh session (every header worked normally after
  that, and only that first click was ever affected) - confirmed live as Windows
  double-dispatching that window-activating click to the header's bound handler; a short
  debounce on the toggle handler absorbs the duplicate without touching window-focus
  handling directly. The pop-up console overlay now also measures the active tab's own
  bottom-docked action-button row (not just its header) and stops short of it, so a script
  can be re-run without first closing the console.
- **Antiquarian (Global Settings)**: sections after the first (auto-expanded) one could
  render effectively dead - collapsed headers not responding to clicks, or responding only
  along a thin sliver near the arrow rather than across the whole row. Root-caused, not
  guessed at: wrapping each settings section in its own bordered `CTkFrame` ("card") left
  every card after the first genuinely unmapped by Tk (`winfo_ismapped() == False`, not
  just visually off) until some unrelated later event forced a full relayout - confirmed
  live via direct Tk introspection, and confirmed absent by rebuilding the same settings
  shell fresh from the pre-card version and testing after every phase. The bordered-card
  layout is reverted for now (back to one scrollable region holding collapsible sections,
  the same mechanism already used before) until a version of the "boxed" design that
  doesn't trigger this gets designed.
- **Voyageur -> Archivist handoff**: `A.py`/`FS.py` wrote the freshly-gathered `JSON_FILE`
  setting to their own subfolder's `.env` - a file Archivist never reads at all (`JSON_FILE`
  belongs to Archivist's own settings, per its `ARCHIVIST_VARS` entry). Both now write it to
  `Archivist/.env`. Also fixed the GUI's "Gather and Send to Archivist" chain: it never
  re-read this value from disk before its own `_save_env()` call overwrote it with whatever
  was already showing in that field before the gather started - `execute_script` now
  resyncs it first. Confirmed live: this was the exact cause of Archivist immediately
  failing with `FileNotFoundError` right after a real gather succeeded.
- **Voyageur (Ancestry/FamilySearch)**: `CENSUS_IMAGE_DIR` is a subfolder of the Base Media
  Directory (`MEDIA_DIR`), not of `PROGRAM_DIR` directly - both `A.py` and `FS.py` now
  resolve this themselves, matching how Antiquarian.py's GUI already resolves it before
  launching either script as a subprocess. Confirmed live: running either script standalone
  (bypassing the GUI, which normally injects the fully-resolved path) previously put images
  in a stray folder at the program root instead of nested under the user's actual
  RootsMagic media folder - a real gap, not just a testing artifact, since every other tool
  in this project is expected to produce correct output standalone.
- **Shared schema**: `Paleographer/schema.json` gains a constrained `sex` enum (`M`/`F`/`U`,
  now required - family-position resolution depends on it matching exactly across every
  source), `age_unit` (`years`/`months`/`days`, since infant baptism/burial ages are often
  given in months or days), a generic per-participant `facts[]` array (extra facts named
  from the shared fact/event vocabulary, replacing ad hoc DataFrame-column guessing), a
  nullable `role_name` (for sources with no relationship data at all, e.g. pre-1880 US
  census), and the `household_tally` convention for pre-1850 census records' unnamed
  age/sex/race tallies.
- **Voyageur**: census gathers (Ancestry and FamilySearch) are now normalized into the
  shared `sheets[].records[].participants[]` schema at gather time, via a declarative
  per-source field map (`Voyageur/field_maps/*.yaml`) - translating each source's own raw
  column header text into the schema's field names, instead of leaving Archivist to guess
  among several possible header spellings downstream. An unmapped header is flagged for
  review, never dropped or guessed at. Census households become records
  (`event_type: "Census (family)"`) grouped by family/dwelling number; a source with no
  such data (pre-1850) produces one participant per record with no fabricated grouping.
  `MergedCensus.py` is rewritten to merge on this normalized shape (matching sheets by
  enumeration district/page/roll, participants by Line Number) instead of raw per-source
  columns. Verified against real 1860 Ancestry census data (Pembina, Dakota Territory):
  156 people normalized with zero unmapped headers, correctly resolved into 45 family
  units through Archivist's existing (unchanged) household-parsing logic.
- **Archivist**: reads the shared schema directly for census now
  (`build_census_dataframe_from_unified`), adapting it back into the same column-named
  shape the existing, independently-verified household-parsing/citation logic has always
  operated on - that logic itself (era detection, `parse_household`,
  `parse_household_relational`, citation assembly) is unchanged. Dispatch between census
  and church documents now reads `record_type_name` directly off the loaded JSON, never a
  hardcoded family-name string or a shape guess. The legacy `{census_year, location,
  pages: [...]}` shape is still read unchanged for any already-gathered file that predates
  this normalization.
- **Paleographer + Archivist**: both now resolve their own settings (image directory,
  master DB filename, citation fields) from their own `.env` files via each record type's
  `field_remap` table (declared in `Parish.pmt`/`Scrip.pmt`'s front matter), with no
  dependency on Antiquarian.py's GUI layer to bridge prefixed settings-tab names to the
  generic names each script actually reads - fixes a real standalone-execution gap found
  during a function-by-function audit against the project's design specification.
- PyCharm Run/Debug configurations for Voyageur (per source), Paleographer, Archivist, and
  the Antiquarian GUI shell, so each can be launched standalone directly from the IDE.
- **Gazetteer**: Canadian place standardization, using the UNI-CEN Project's Census
  Division boundary shapefiles (`Gazetteer/CA_UNICEN_Counties/`, one file per census year
  1851-1921, gitignored like the existing US atlas - see that folder's own
  `LICENSE_AND_ATTRIBUTION.txt`). An event is matched against the US Newberry atlas first,
  exactly as before; if nothing matches there, it falls back to the Canadian boundaries for
  whichever census year is chronologically closest to the event's own date - the best
  available precision, since (confirmed via research) no Canadian dataset exists with a
  continuous start/end date range the way the Newberry atlas has for the US: municipal
  boundaries are provincial jurisdiction, not one uniform national system, and StatCan's own
  change registry only goes back to 1961. Historical province codes are used as recorded
  (e.g. 1861/1891 Alberta and Saskatchewan correctly resolve to "Northwest Territories",
  since neither became a province until 1905; Newfoundland resolves to its own country, not
  "Canada", since it didn't join Confederation until 1949). New optional setting
  `GAZETTEER_CA_SHAPEFILE_DIR`; Gazetteer runs US-only if it's not set/found, unchanged from
  before this existed.
- **Voyageur (Merged)**: which source is treated as primary on a field conflict is now
  configurable (`MERGE_PRIMARY_PROVIDER`, Ancestry/FamilySearch, default Ancestry -
  unchanged from before this setting existed). `MergedCensus.py`'s merge functions are
  genericized from hardcoded Ancestry/FamilySearch naming to primary/secondary throughout;
  both sources' own identifiers/links are still always carried through regardless of which
  is primary.
- **Archivist**: which GEDCOM flavor(s) to generate is now configurable
  (`GEDCOM_OUTPUT_MODE`: RM, FTM, or Both - default Both, unchanged from before). A
  single-flavor run drops the " - RM"/" - FTM" disambiguating filename suffix entirely,
  since there's nothing to disambiguate from with only one output.

### Fixed
- Repo-wide PEP-8 lint failures (`pycodestyle --max-line-length=120`), both newly
  introduced by this work and pre-existing debt never caught before CI actually ran on a
  pull request.
- **Voyageur (Ancestry/FamilySearch)**: periodic checkpoint downloads (crash-recovery
  saves taken every 20 pages/items) were never cleaned up from the Downloads folder - only
  the final combined JSON was ever moved out, so a long gather left several superseded,
  marker-prefixed checkpoint files behind permanently. `A.py`/`FS.py` now delete this run's
  own checkpoint files once the final JSON is safely written. (The marker prefix itself on
  the final JSON was already stripped before reaching the project folder - that part
  was never actually broken, despite being renamed several times looking for the bug.)

## [0.07.00] - 2026-07-29

### Added
- **PDFix**: new standalone tool, ported from the separate `PDFix` project, that
  losslessly shrinks PDF file size in bulk via PyMuPDF's garbage-collection + stream-
  deflate save flags (structural only - never rescales embedded image resolution/DPI).
  Recursively scans a target folder, with an optional per-file size threshold, automatic
  `.pdf.backup` creation before any in-place rewrite, and a repair-mode fallback (direct
  save -> incremental/conservative save -> full page-by-page reconstruction) for
  structurally damaged PDFs. Confirmed live: 14% size reduction on a real sample PDF,
  with the original preserved and a `.backup` copy created.
  - The ported repair-mode fallback had a latent bug, caught before it ever shipped:
    `optimize_pdf`'s page-by-page reconstruction path called
    `page_by_page_recovery(pdf_path, temp_optimized_pdf_path)` with 2 arguments against a
    signature that only accepted 1, so it raised `TypeError` instead of repairing
    anything - and even with the arg count fixed, the function always overwrote the
    original file in place rather than producing the temp file the surrounding code
    actually looks for. `page_by_page_recovery` now takes an optional `output_path`, used
    by this specific call site so the reconstructed PDF lands where `optimize_pdf`
    expects it, without ever touching `pdf_path` directly. Regression test added
    (`PDFix/tests/test_pdfix.py`) reproducing the exact original crash scenario.
- **Paleographer**: scanned/handwritten PDFs (the `pdf_native` path in
  `build_content_part_for_file` - no usable text layer, so the whole file uploads to
  AI Assistant as-is) now run through the same PDFix optimization against a throwaway temp
  copy before upload, cutting upload size/cost. Defaults to the most aggressive
  compression level (`PALEOGRAPHER_PDF_COMPRESSION_LEVEL`) since the optimization is
  structural/lossless and never touches embedded image pixel data, so transcription
  quality is unaffected by the level chosen. The researcher's original source PDF is
  never mutated - `optimize_pdf_for_upload()` always operates on a copy, deleted once the
  upload completes.
- **Paleographer**: records split across a page/image boundary no longer lose continuity.
  Each image was previously processed as its own fully independent API call with zero
  memory of any other image - a record starting near the bottom of one page and
  finishing at the top of the next had no shared context between the two calls at all.
  Confirmed real scope: 152 of 2,244 records (7.4%) in the actual Assumption register
  data were already self-flagged by the model with reasons like "cut off at the bottom
  of the page." Fixed with a stateful carryover, not content-matching (a continuation
  fragment doesn't reliably repeat its own margin number, so matching two independently-
  captured records after the fact isn't reliable): two new record fields,
  `continues_on_next_image` (set on a page's last record when it looks cut off) and
  `continues_from_previous_image` (set on the record that completes one), let
  `run_synchronous_batch` hold a cut-off record back instead of saving it, pass its data
  forward as extra prompt context for the next image (`engine.build_continuation_context`),
  and either accept the next image's merged replacement or fall back to saving the
  original fragment unchanged if nothing continues it - so a record is never duplicated
  and never silently dropped. New `UNIVERSAL_PROMPT_SUFFIX` rule (shared by every record
  type, not Parish-specific) documents the behavior for the model.
  - **Parish.pmt**: removed its old "10. RECORDS SPANNING PAGE BOUNDARIES" rule ("merge
    records logically... if cut off, use '[illegible]' and flag for review") - found while
    reading the fully-assembled prompt end to end after adding the above. This predated
    any cross-page context existing at all, never mentioned `continues_on_next_image`, and
    sat directly alongside the new precise rule giving the model conflicting guidance on
    the exact same situation.
- **Paleographer / Parish.pmt / Archivist**: a margin note offering a different name
  spelling is a later researcher's/transcriber's annotation, not the priest's own entry
  and not a disagreement to resolve (both readings are genuinely useful, kept side by
  side). Participants gained an `alternate_names` field; Archivist renders each as its
  own `proposed`-status `NAME` fact (reusing the same convention already built for
  census's crowdsourced alternate readings) plus a `NOTE` on the person's own event.
  `Parish.pmt` also gained explicit rules for Indigenous/non-European names (don't force
  a European given/surname split, never attempt to translate a name's meaning - just
  preserve it phonetically and flag genuine spelling uncertainty), struck-through/
  corrected text (use the correction, not the crossed-out original), and reinforced that
  an ambiguous digit (age/date/margin number) must never be silently resolved toward
  whichever reading "seems more plausible" - record what's legible and flag for a human,
  every time. Driven by auditing 1,368 real review flags across the actual 2,244-record
  Assumption register data.

### Fixed
- **Archivist (census)**: `parse_household`'s age/surname heuristic (used for census years
  with no Relationship-to-Head column, e.g. 1860) only ever evaluated a family/dwelling-
  number group's members against its own first-listed person as the presumed head. A
  household can legitimately contain two unrelated families under one shared number (a
  boarder living with an unrelated family) - confirmed against real data, where an entire
  second family (spouse pair plus children, matching surname to each other but not to the
  group's first person) was silently dropped: a valid spouse-pair match was found and then
  discarded because neither person also fit as a child of the first unit, so every member
  fell through to "unrelated" one at a time instead of forming their own family. Now forms
  a second unit when this happens, so their own children correctly attach to it on
  subsequent rows instead of also being lost. Found by comparing a real gather's JSON
  against its generated GEDCOM and the imported `.rmtree` - one file had 23 people
  incorrectly marked "Unrelated household member" across 4 households; 16 of those were
  real family members, now correctly linked.
- **Archivist (church)**: `get_volume_sources` collapsed two previously-distinct settings,
  `REGISTER_NAME` (what the register contains, e.g. "Baptisms, marriages and burials,
  1850-1900") and `VOLUME_TITLE` (this specific volume's own label, e.g. "Volume 1"), into
  just `volume_title` - silently dropping `REGISTER_NAME` from the citation's `TITL` line
  and RootsMagic's `RegisterName` template field. Restored as its own `CHURCH_CONFIG` key
  and Antiquarian settings field, matching the original GitHub-committed
  `ChurchCreateGedcom.py`.
- **Archivist (census)**: `build_census_citation`'s RM-flavor branch never emitted a
  `QUAY`/`_QUAL` citation-quality block at all (only the FTM branch had a bare `QUAY`); the
  church-flavor citation already had the full block. Added the same
  `QUAY`/`_QUAL`/`_SOUR`/`_INFO`/`_EVID` structure to the census RM citation.

### Changed
- **Voyageur (A)**: `triggerBlobDownload`'s image downloads were changed to fire-and-forget
  (not awaited) mid-batch, to keep a multi-second image transfer off the critical path
  between pages - but this let an image download overlap with the next page's own
  navigation/fetch, which the last reliably-working version of this gather never did (it
  always awaited each image fully before moving on). Reverted to sequential/awaited image
  downloads; the `pendingImageDownload` bookkeeping this required is removed as
  unnecessary once every download is already finished before the loop moves on.
- **Voyageur (A, FS)**: downloaded filenames no longer carry a `Antiquarian_` prefix (now
  `MGS_`) - purely cosmetic, `A.py`/`FS.py`'s Downloads-folder scan updated to match.
- **Archivist**: replaced the church-flavor GEDCOM builder's hardcoded `role_number` digit
  table (role "2" is always "Father", role "4" is always the marriage bride, a
  `record_number` starting with "C" means christening, ...) with a small fixed
  `role_semantic` vocabulary (`primary`/`spouse`/`child`/`father`/`mother`/
  `father_in_law`/`mother_in_law`) read directly off each participant, plus a
  `FactTypes.json`-driven lookup for a record's GEDCOM tag and whether it's a family-level
  fact (Marriage) vs a person-level one (Baptism, Burial, Scrip, ...). A new record type
  only needs to tag the right roles in its `.pmt`'s `roles:` table with a `semantic` -
  Archivist itself no longer hardcodes what any specific role means, which was the
  concrete reason it worked for the Assumption parish register but couldn't scale to a
  different one. `role_number`/`generate_uid`'s hashing are unchanged, so every
  already-generated individual's GEDCOM ID is unaffected by this change.
  - New capability, not previously possible: a burial's surviving-spouse mention (role "4"
    outside a marriage record had no handling at all before) now correctly forms a real
    `FAM`/`FAMS` link instead of being silently mislabeled "Witness."
  - `Paleographer/postprocess.py` gained `derive_role_semantics`; `Voyageur/FS.py` gained
    the equivalent `derive_role_semantic` for FamilySearch-indexed church records, so both
    gather paths feed Archivist the same field.
  - `Parish.pmt`'s "Father of Spouse"/"Mother of Spouse" roles and `Scrip.pmt`'s "Child"
    role gained the `semantic` tags this generalization reads.
  - Deferred, not part of this change: folding census's separate household-grouping path
    into the same ingestion function, and migrating `CHURCH_CONFIG`'s citation/parish-naming
    keys from `.env`-only to JSON-carried collection metadata.
- **Archivist**: `1 _RACE {value}` (a non-standard tag RootsMagic doesn't recognize as its
  real "Race" custom fact) replaced with a generic `build_custom_fact_lines()` helper that
  renders any `FactTypes.json` custom fact (`custom: true`) as a proper `EVEN`/`TYPE` block
  from that fact's own `use_value`/`use_date`/`use_place` flags - the same shape RootsMagic
  needs to match a custom fact back to its FactTypeTable entry by name. `dit Name` migrated
  to the same helper (output unchanged - it already used this shape by hand). A record
  whose own `event_type` is itself a custom fact (e.g. Scrip, which has no standard GEDCOM
  tag) now gets a `2 TYPE` line identifying which fact it is, plus its own
  `type_specific_fields` (scrip_number, scrip_amount, ...) rendered generically as that
  event's value text by field name - Archivist never hardcodes which fields a given record
  type carries. Standard record types (Baptism/Marriage/Burial) are unaffected: their
  events already resolve to a real GEDCOM tag, never the `EVEN` fallback this touches.

### Fixed
- **Archivist**: `build_individual`'s `1 FAMC` line for a primary who is purely a child in
  their record (no spouse/children of their own - the baptism/burial shape) was wrongly
  gated on parents actually being present, while `build_family` unconditionally creates
  that FAM regardless (even with neither parent recorded, just to hold the CHIL link,
  matching this record type's output from before the role_semantic generalization above).
  A primary with zero recorded parents ended up with an orphaned FAM record nothing
  pointed to. Caught by diffing the new semantic-driven output against the pre-refactor
  code on hand-built baptism/marriage/burial records (no surviving JSON for the actual
  Assumption register to diff against directly - only its already-generated `.ged`/
  `.rmtree` remain) - every other field, UID, and citation matched byte-for-byte.
- **Paleographer / Parish.pmt**: restored per-event-type role meaning that existed in the
  original embedded prompt but was dropped when roles moved into a `.pmt` front-matter
  table - `role_name` "Primary" means the child for a baptism but the deceased for a
  burial, and neither the model nor a human reader could tell that from the current bare
  role list. A role entry in the front matter can now carry an optional `context` string,
  surfaced by `engine.build_vocabulary_summary()` (generic, not Parish-specific) alongside
  the role name. Also restored the "Other" catch-all role for a named participant who
  doesn't fit any other slot - previously dropped, or left with `role_number: null`.

### Removed
- **Archivist**: `generate_uid`'s clergy-identity branch had a first check for a >10-digit
  `role_number` - a leftover from a prior prompt design that hardcoded a fixed roster of
  named priests (one specific parish's known clergy, each given a permanent ID) directly
  in the prompt. That mechanism was deliberately not carried into Parish.pmt's rewrite,
  since it requires knowing every priest in advance and doesn't scale to an arbitrary,
  unknown register - so the branch was reachable code with nothing left to feed it. Removed;
  clergy identity is now always the standardized-name hash, which is the actually-generic
  path. Registrar.py's fuzzy-duplicate matching (post-GEDCOM-import) remains the intended
  safety net for a spelling-variant of the same real priest hashing differently across
  records, not something this function tries to solve itself.

### Added
- **Paleographer**: new `Paleographer/tests/test_paleographer_pipeline.py` - a full
  end-to-end simulation of Paleographer.py itself (file discovery, prompt/schema assembly,
  post-processing, master DB write) with `google.genai.Client` replaced by a fake that
  returns a canned response instead of ever calling the real API. Runs both Parish and
  Scrip record types plus debug-file mode, verifying the real `Parish.pmt`/`Scrip.pmt`
  files and `FactTypes.json` vocabulary actually work together correctly - previously only
  engine.py/postprocess.py had unit tests for their own pieces in isolation; nothing
  exercised Paleographer.py's own orchestration or a real record type end-to-end.
- **Parish.pmt**: added an "Officiant" role and explicit instructions for capturing the
  officiating priest as their own participant with `is_priest: true`. Archivist.py already
  has a fully-built downstream mechanism for this (clergy name-prefix, occupation, a
  stable cross-record identity hash), but no role in Parish.pmt's vocabulary could ever
  produce it, so a register's officiant was silently never captured at all. Also instructs
  the model to always give the officiant's fullest legible name rather than a signature
  abbreviation, since Archivist's cross-record identity match for clergy is a plain
  standardized-name hash with no pre-known roster to fall back on (see the "Removed"
  entry above) - name-reading consistency across records is now the only thing keeping
  the same real priest from fragmenting into multiple GEDCOM individuals.
- **Parish.pmt**: added an explicit workflow step to identify *every* distinct sacramental
  entry on a sheet before transcribing - a single parish register page commonly lists
  several baptisms/marriages/burials in sequence, and nothing previously told the model
  not to stop after the first one.

### Changed
- **Scrip.pmt**: added an explicit rule that `is_priest` is always `false` (no clergy are
  involved in a scrip commission) - previously unaddressed, despite being a required schema
  field on every participant.
- **Voyageur (FS)**: migrated the entire FamilySearch gather flow off fixed-interval
  polling (`sleep(200)`/`sleep(100)` retry loops, up to 15s of blind ticking per wait) onto
  the same `MutationObserver`-based `waitForCondition()` the Ancestry side already used -
  now hoisted to shared top-level scope instead of being Ancestry-only. Covers tab-clicking,
  citation-text settling, index-table settling (including the early exit for a
  blank/title page's "No indexes are available" message), the Next-button wait, and page
  navigation. An already-ready page now resolves immediately instead of waiting out a fixed
  tick; a genuinely slow one still gets the same ceiling as before. No more `sleep()` calls
  remain anywhere in the script.

### Cleanup
Project-wide dead-code audit and pass across Voyageur.js, A.py, FS.py, MergedCensus.py,
Archivist.py, and Antiquarian.py - removing artifacts left behind by earlier fix attempts
that were superseded but never cleaned up, plus a few real bugs the audit surfaced along
the way:
- **Antiquarian**: `GEDCOM_OUTPUT_NAME` was only ever set from `CHURCH_GEDCOM_NAME` inside
  the `paleographer_api` mode (the AI transcription step, which never calls Archivist) and
  never in `gedcom_auto` (the only mode that does) - so clicking "Generate GEDCOM" on a
  church/scrip register always wrote to Archivist's module-level default filename instead
  of the configured one. Now also set in `gedcom_auto`'s church/scrip branch.
- **Archivist**: two `CHURCH_CONFIG.get(key, default)` calls checked for keys
  (`image_dir`, `review_color`) that don't exist in that dict, so both always silently fell
  through to their literal default - the `review_color` one meaning church-flavor review
  tasks always used color `'1'` regardless of the real, configurable `REVIEW_COLOR`
  setting. Both fixed (one collapsed to its always-taken branch, the other now uses
  `REVIEW_COLOR`).
- **Archivist**: `Extracted_URL` (the real citation link Voyageur.js scrapes directly off
  the page) was carried through the entire pipeline into the DataFrame but never actually
  read - citations always reconstructed the URL from `APID_DB`/`rec_id` instead, which is
  wrong for any row that fell back to a synthetic `rec_id` (no real pid). Citations now
  prefer the real scraped URL, falling back to the reconstructed one only when it's absent.
- **Archivist**: `_MergeReviewReason` (a merged person's conflict/no-match reason) wasn't in
  `CORE_COLUMNS`, so beyond surfacing correctly as a `_TASK`, it was *also* being swept into
  a plain `NOTE` on the census event by the generic dynamic-notes pass - every merged
  person's review reason was written into the GEDCOM twice.
- **Archivist**: removed dead `submitters`-attribution handling (four branches, three
  stale docstring claims) left over from the "Added by <user>" popup-capture feature that
  was removed earlier this session for freezing a real gather - `Voyageur.js` has never
  been able to produce a `submitters` key since.
- **Voyageur.js**: removed an unused `sleep()` helper and `blobToDataURL()` left over from
  abandoned wait/download approaches, a dead `&& toggleBtn` guard now unreachable-false
  after an earlier fix, consolidated a disabled-button check duplicated at the top and
  bottom of the per-page loop into one helper, and removed stale version-drift ("v1.0")
  text from the on-page UI.
- **Voyageur (A), FS.py**: removed an unused function parameter (`items_raw` on
  `detect_record_family_from_raw`), consolidated `items_raw`/`catalog_items` computation
  in `main()` so `build_universal_json`/`build_census_json` no longer each recompute it
  from scratch, and fixed a dead filter clause in A.py's download scan.
- **Voyageur (FS)**: an FS-only (non-merged) census run had no FamilySearch web link on its
  citation at all - `build_census_json` captured `person_ark` but never built
  `familysearch_url` from it outside of a merge (only `MergedCensus.py` did that
  conversion). Now set directly, same construction MergedCensus.py already used.
- **MergedCensus.py**: removed a film-number match tie-breaker that could never fire
  (FamilySearch's census citation never exposes `film_number`, only `roll_number`), a
  redundant `pid` reassignment that was a no-op, and consolidated two identical
  `normalize_key`/`normalize_locator` functions into one.
- **Antiquarian**: removed a dead `is_waiting_for_downloads` flag (assigned in three
  places, never read - the polling it once tracked moved into `A.py`/`FS.py` long ago), and
  renamed `PROGRAM_DIR_SENTINEL`'s value from `"PROGRAM_DIR"` (identical to the real
  settings key of the same name) to `"__PROGRAM_DIR__"` to remove the collision risk,
  matching `TOOLBOX_DIR_SENTINEL`'s own convention.
- Fixed several stale comments/docstrings referencing removed mechanisms or the tools'
  pre-rename names (`GedcomBuilder`, `Archivist.js`) across Voyageur.js, A.py, and
  Archivist.py.

### Fixed
- **Archivist (census)**: `get_row_val` treats a truthy `default` argument as an
  already-resolved override and returns it immediately without ever checking the row -
  correct for a global setting like `TOWNSHIP` that should win when explicitly configured,
  but two call sites passed a hardcoded last-resort filler literal instead: `line_num =
  get_row_val(row, ['Line Number', 'Line'], 'X')` and `row_country = get_row_val(row,
  ['Country'], 'USA')`. Both fillers are truthy, so the row's real Line Number/Country was
  *never* read - every citation printed a static "Line: X" (confirmed live: "Gabro,
  Jacques: Page: 3, Line: X...") and every Canadian gather was cited as "USA" regardless.
  Fixed by passing an empty default (so the row is actually checked) and applying the
  filler only if that comes back empty. Line Number now also has its own synthesis as a
  second-level fallback - sequential, 1-based, restarting at 1 on every new page (mirroring
  Voyageur.js's own synthesis) - for JSON that has no Line Number data at all. The same bug
  affected `row_state`/`row_county`/`row_town`/`row_roll`/`row_film`/`row_ed`, each passed
  its matching global setting (`STATE`/`COUNTY`/`TOWNSHIP`/`ROLL_NUMBER`/`FILM_NUMBER`/
  `ENUMERATION_DISTRICT`) as that same truthy `default` - since `run_census_flavor` already
  pre-fills those globals from the whole gather's modal JSON value, every person's citation
  silently used that one modal value instead of their own page's real one, collapsing a
  gather spanning multiple townships/counties/rolls (a real scenario - a NARA roll can
  change mid-run with no other signal) down to a single value. All six fixed the same way:
  row's own value first, global setting only as the fallback.

### Changed
- **Archivist (census)**: the Ancestry and FamilySearch `_WEBTAG` citation titles are now
  prefixed `Anc- `/`FS- ` respectively, so the two links are distinguishable at a glance in
  RootsMagic instead of showing the identical collection-name text for both.

### Removed
- **Voyageur (A)**: removed the `baseUrlPid + rowIndex` "math fallback" for a row's pid.
  Confirmed live it assumed the URL's own `pId` param was this page's own starting pid, but
  that param actually carries the *previous* page's last-clicked record forward instead of
  resetting - so the guess was frequently wrong and could collide with an already-seen real
  pid, cascading into an entire page being wiped out (see the `rowIndex` fix below). Anchor
  href / React fiber / network-cache lookups are reliable enough on their own now, and
  Archivist's own `rec_id` fallback (`ANCESTRY_START_RECORD_ID` + row position) already
  covers a person left with no pid at all.

### Fixed
- **Voyageur (A)**: `rowIndex` only incremented after a row was successfully pushed, so the
  duplicate-pid `continue` skipped the increment entirely - freezing `rowIndex` on the
  first duplicate hit. Since the math-fallback pid is `baseUrlPid + rowIndex` (needed
  because the URL's own `pId` param carries the *previous* page's last-clicked record
  forward rather than resetting, and the network-pid cache can be empty for a page too), a
  frozen `rowIndex` made every remaining row on that page recompute the same already-seen
  pid, cascading into "every row for the rest of the page marked duplicate" - confirmed
  live, this silently skipped an entire page's worth of real people. `rowIndex` now
  increments for every real data row immediately after its own pid lookup, whether that
  row turns out to be a duplicate or not.
- **Voyageur (A)**: live testing showed alternate-name settle-waits suddenly start timing
  out partway down a page (rows 1-25 fine, 26-30 all timed out on their full 5s ceiling)
  and never recover for the rest of that page - consistent with a virtualized/windowed
  index table not having fully rendered a row further down yet, even though it's already
  in the collected row list, so clicking it didn't reliably register. `row.click()` is now
  preceded by `row.scrollIntoView()` (a no-op for an already-visible row). As a backstop
  regardless of cause, once 2 consecutive rows on a page time out, the rest of that page
  skips attempting alternate-reading entirely instead of paying the full ceiling per row
  for a result already known to come back empty - eliminated ~15-25s of dead time observed
  on a real gather's last page.
- **Voyageur (A)**: `readPersonAlternates`'s settle-wait fell back to reading whatever was
  currently in the Detail panel even when it had timed out without ever confirming the
  panel actually showed the current person - so a slow panel swap meant the *previous*
  row's alternate name/place got silently attributed to the next person too (confirmed
  live: "Gabe", a real alternate for one specific person, showed up on others as well).
  A timeout now returns no alternates for that person instead of guessing from stale DOM
  state - missing one person's alternates on a slow render is a far smaller cost than
  misattributing someone else's.
- **Voyageur (A)**: the blank/unindexed-page detection (`toggleBtnWait`, up to 15s) ran
  unconditionally on every page, including the true last frame of the roll - live-confirmed
  the Next button is only ever missing/disabled on that true last frame (a blank page
  earlier in the roll still shows it), so that's now checked first and used to shorten the
  ceiling to 3s specifically on a confirmed-last page, where a wrong "blank" guess can't
  cost a skipped page in the middle of the roll the way the original regression did. The
  authoritative stop-vs-continue decision still re-checks at the same later point in the
  page lifecycle as before, preserving its original timing/safety margin.
- **Voyageur (A)**: the final master JSON download (`downloadFinalJson`, triggered from
  `stopBatch`) fired immediately when the batch loop ended, with no wait for the very last
  page's image - `downloadCurrentImage()` is deliberately fire-and-forget mid-batch (so a
  multi-second image transfer doesn't block navigation to the next page), so the last
  image was still mid-download when the JSON (`A.py`'s signal to move everything) landed,
  and A.py's one-shot scan missed it. `stopBatch` now awaits that last pending download
  before triggering the final JSON.
- **Archivist (census)**: no setting fed `GEDCOM_OUTPUT_NAME` for census runs (only the
  church flavor's `CHURCH_GEDCOM_NAME` does), so it always fell back to the module-level
  default `Family_Register.ged` regardless of what was actually gathered. Now derives the
  name from the gathered JSON's own filename (the original `CensusConverter.py`'s
  convention) unless explicitly overridden.
- **Archivist (merged census)**: `MergedCensus.py`'s page matching compared Ancestry's
  `film_number` against FamilySearch's `roll_number` as a single "whichever's present"
  locator - two different identifiers - so a page with both a film and roll number on the
  Ancestry side (common) produced a false mismatch and split what should have been one
  merged page into two, one of them missing all of Ancestry's data. Now compares
  roll-to-roll and film-to-film separately, each only as a tie-breaker when both sides
  actually have that specific field.
- **Voyageur (FS) / Archivist**: FamilySearch-sourced people had no name at all after a
  merge - `FS.py`'s `build_census_json` only ever exposed a single `Name` field, never
  split into `Given Name`/`Surname` like Ancestry's schema, so those columns came back
  missing (pandas `NaN`) once merged into one DataFrame alongside Ancestry rows that do
  have the split - and `clean_val()` had no NaN handling, rendering the literal string
  `"nan"`. `FS.py` now splits `Name` at the source; `clean_val()` now treats NaN as blank.
- **Voyageur (FS)**: each FamilySearch person's `pid` (used directly as the GEDCOM's
  REFN/`@I@` id and folded into the `_APID` citation tag) was built as
  `{page-level ARK}-{row number}` (e.g. `3:1:33S7-9YBJ-9PD7-1`) - two identifiers glued
  together. Now uses that person's own real, already-distinct FamilySearch ark instead.
- **Archivist (merged census)**: FamilySearch's `_WEBTAG` title read "FamilySearch Record"
  instead of matching Ancestry's own collection-name title.
- **Voyageur (A, FS)**: dropped `GM_download` entirely after two rounds of live testing
  confirmed it fundamentally unreliable for this - its "downloads" permission grant resets
  every time this script's content changes in Tampermonkey, and even when granted it would
  still sometimes ignore the requested filename/folder outright and save a hash-named file
  in the Downloads root instead, for both the master JSON and every page image (tried both
  a `blob:` and a `data:` URL as the input - same failure either way). Since `A.py`/`FS.py`
  only ever looked in the intended subfolder, this made every gather hang forever waiting
  for a file that had actually already "finished" downloading elsewhere under the wrong
  name. Reverted to the plain `<a download>` method the original `CensusExtractor.js` used
  in production (see git history) - no special permission grant to lose, and it always
  honors the exact name given. Chrome replaces `/` in a `download` attribute with `_`
  rather than creating a subfolder, so the intended subfolder is now baked into the
  filename as a `Antiquarian_A_`/`Antiquarian_A_Images_`/`Antiquarian_FS_` prefix instead
  of a real path - `A.py`/`FS.py` now scan the Downloads *root* for that prefix (stripped
  back off once found) rather than a dedicated subfolder.
- **Antiquarian**: `_run_subprocess` treated any non-zero, non-cancel-looking exit code as
  either "an error" or "cancelled", but on Windows `Popen.terminate()` and an unhandled
  Python exception both exit with code 1 - so a genuine crash (e.g. `A.py` failing partway
  through moving a file) was silently mislabeled "Task was cancelled by you" instead of
  surfacing as an error, hiding the real cause. Cancellation is now tracked with its own
  flag (`_cancel_requested`) instead of being inferred from the exit code.
- **Voyageur (A)**: `shutil.move()` for the final JSON and each image had no retry, so a
  transient Windows file lock (Chrome or antivirus still holding the freshly-downloaded
  file open for a moment) could crash the whole gather with the file left stranded in the
  Downloads staging folder. Both moves now retry up to 5 times with a short backoff.

### Added
- **Antiquarian**: the Voyageur tab has a new "Gather and Send to Archivist" button - runs the
  selected gather and, only if it finishes cleanly (not on error or cancellation),
  automatically runs Archivist's "Generate GEDCOM" right after, in one click.
  `execute_script` gained an optional `on_success` callback for this, invoked once a
  subprocess exits with code 0, so other one-click chains can reuse the same mechanism
  later.
- **Voyageur (A) / Archivist**: captures Ancestry's own crowdsourced "alternate
  reading" submissions for Name and Birth Place (the bracketed values shown alongside
  Ancestry's own primary indexed reading in each person's Detail panel) and merges them
  into the GEDCOM as additional facts - a plain `NAME` line (no `/TYPE` switch, not
  marked aka) positioned right after the primary name for alternate names, and a full
  extra `BIRT` event (same date, alternate place substituted in) for alternate
  birthplaces. Both carry a `proposed` (not `proven`) proof status and the same
  per-person citation as every other tagged fact. Read directly off each alternate's
  own button text - not by clicking through to its "Added by <user>" popup, which
  turned out to have two entirely different rendering variants depending on viewport,
  sometimes took several seconds to render, and had no reliable close control in
  either variant - confirmed live that this combination caused a real gather to freeze
  on an orphaned, unclosable popup. Confirmed live that selecting a different person's
  row to read their Detail panel costs zero extra network requests - all people's data
  for a page is already bulk-loaded - so this only adds UI interaction time, not
  fetches.

### Fixed
- **Voyageur (A)**: `ensureInfoPanelOpen()` assumed the info panel always starts closed
  and used the Detail tab button's mere DOM presence as an "already open" check - but
  confirmed live, the panel's open/closed state is a persisted user preference (a fresh
  page load can start already open) and the tab bar exists in the DOM either way, so
  the old check would sometimes toggle an already-open panel *closed* instead of
  opening a closed one. Now checks `.infopanel`'s own "opened" class directly.

### Changed
- **Voyageur (A)**: `runAncestryGather()`'s per-page waits (index panel ready, table content
  updated, citation scraped, next-image button, page navigation) were all fixed-interval
  polling loops (100-200ms ticks). Since each one's first check happens before the awaited
  change has actually landed (e.g. right after clicking "Next Image" the old page's DOM is
  still there), every page paid for at least one full tick per wait, stacking up to
  roughly a second of pure polling overhead per page regardless of how fast Ancestry
  actually responded. Replaced with a shared `waitForCondition()` helper that uses a
  `MutationObserver` to re-check the instant the DOM actually changes, so an
  already-ready page resolves immediately; every existing give-up ceiling (5-30s) is
  preserved as a fallback timeout.

### Added
- **Merged census (Ancestry + FamilySearch)**: a new `Voyageur/MergedCensus.py` merges two
  already-gathered census JSON files (one per source, covering the same physical page) into
  one, per-person - preferring Ancestry's fuller field set on conflicts (flagged for
  review, not silently dropped), while keeping FamilySearch's own ark/citation links. Each
  merged person carries both `_APID` and `_FSFTID` and both web links, but only **one**
  citation, citing NARA (the actual microfilm holder) rather than Ancestry.com or
  FamilySearch.org as the repository. `FS.py` gained a census-shaped output path
  (`build_census_json`, auto-selected alongside the existing church-flavor path) and now
  extracts the NARA microfilm series/roll from FamilySearch's citation and Catalog Record
  table, previously left blank. The Voyageur tab gained a "Merged (Ancestry +
  FamilySearch)" source: paste both URLs and one click runs both gathers back-to-back
  (`Voyageur/Merged.py`) and merges the results automatically.

### Fixed
- **Voyageur (A)**: `main()` still scanned the Downloads *root* for the final JSON and
  images, left over from before Voyageur.js's `GM_download` reorganization moved these into
  a fixed `Downloads/Voyageur/A/` (`Voyageur/A/Images/` for images) subfolder - `FS.py` was
  updated for this at the time, `A.py` was not. The real files, correctly named, sat
  untouched in that subfolder while the root-folder scan silently grabbed whatever
  unrelated `.jpg` happened to exist in Downloads within the same time window and moved
  *that* under its own (often hash-like) name instead - confirmed live: the user's actual
  gathered images kept their real filenames in Chrome's own download history the whole
  time. Also added the `'[checkpoint' not in name` filter `FS.py` already had, since a
  naive "first .json found" could otherwise grab a mid-run checkpoint file instead of the
  real final JSON - confirmed live, both existed in the same folder simultaneously.
- **Voyageur (FS)**: `parse_citation`'s regex is tuned for church-register citations'
  simple "...; Publisher, Loc." ending; a US census citation instead ends in a longer
  "citing NARA microfilm publication `<ID>` (`<location>`: `<name>`, n.d.)" clause. The
  regex still technically matched (didn't error) but silently captured garbage into
  `publisher`/`pub_loc` instead of the real NARA holder - confirmed against a real "United
  States, Census, 1860" citation. `build_census_json` now parses that clause separately
  rather than patching the shared regex, to avoid risking the working church-flavor path.
- **Voyageur (FS)**: `runFamilySearchGather()` only waited for the Image Index table /
  citation heading to exist after paging to a new image, not for their real content to
  finish loading, so every image after the first captured an empty stub row and a literal
  "No citation is available." placeholder instead of real data. Now waits for actual
  content; confirmed clean on a full 65-image live run.
- **Voyageur (FS)**: the Catalog Record table (Item 1/2/3 Film/Digital Notes) was scraped
  but discarded after a one-time use detecting record_family; now deduped and attached to
  the shared `citation` object in the output JSON.
- **Voyageur (FS)**: `parse_citation`'s URL regex stopped at the first colon it found,
  truncating FamilySearch ark URLs (which have colons inside the path itself) well before
  their actual end.
- **Voyageur (A and FS)**: a mid-batch crash (confirmed live: Ancestry's own viewer threw
  an internal error and wiped the page ~55 images into a real run) lost every accumulated
  page/item, since nothing was saved until the final "Stop & Download." Both gathers now
  write a periodic checkpoint JSON (every 20 pages/items) so a crash only costs the pages
  since the last one.
- **Voyageur (A and FS)**: JSON and image downloads used a page-triggered `<a>` click,
  which Chrome silently drops once several automatic downloads fire in a row without a
  fresh user gesture - no error surfaced, the file just never lands. Switched to
  Tampermonkey's `GM_download`, which isn't subject to that throttling and saves into an
  organized `Downloads/Voyageur/<A|FS>/` subfolder instead of the Downloads root.
  `FS.py` now watches that fixed subfolder instead of scanning all of Downloads by
  filename/mtime guessing.
- **Voyageur (FS)**: `FS.py`'s output was a bespoke flat structure Archivist.py cannot
  actually read (it only recognizes `"sheets" in loaded_data`) - rewritten to conform to
  `Paleographer/schema.json` exactly, with event/fact vocabulary sourced from a new shared
  `FactTypes.json` (RootsMagic's own FactTypeTable, built from two real `.rmtree`
  databases) and role vocabulary from `Parish.pmt`, both read as data rather than
  imported as code. Also now extracts every column FamilySearch's Image Index actually
  provides (Spouse's Father/Mother, Age, Birth/Death Date, Legitimacy, Entry/Page Number)
  instead of only Name/Father/Mother/Spouse, and splits "dit" names into their own field.
- **Archivist**: `record_type_code` comparisons were hardcoded to Parish.pmt's old ad hoc
  numbering ("1"=baptism, "2"=marriage) instead of RootsMagic's real FactTypeIDs now used
  by the shared FactTypes.json (Baptism=7, Marriage=300) - would have silently
  misclassified every baptism/marriage as a burial. Now uses named constants
  (`RM_BAPTISM_CODE`/`RM_MARRIAGE_CODE`) matching the shared table.
- **Archivist**: `generate_uid`/`generate_fam_uid` hash individuals/families purely from
  `(vol, page, record_id, role)` - no name is in the hash - which assumes a page/record
  locator is always present (true for Paleographer's AI-read pages). FamilySearch's Image
  Index frequently lacks both "Page Number" and "Entry Number" (confirmed on real data:
  ~85% of rows in one real register), so leaving both blank collapsed hundreds of
  genuinely different people onto identical UIDs - caught by generating a sample GEDCOM
  from a live FS gather and diffing its FAM/INDI ID uniqueness against the existing,
  working Assumption Parish pipeline. Fixed in `FS.py`: falls back to the row's own
  item_id/position to guarantee uniqueness whenever the real locators are absent, without
  touching Archivist's proven hashing scheme.
- **Voyageur (FS)**: the "Attach to Tree" table cell actually holds two separate links -
  a "View record for `<name>`" link (always present, that row's own per-record ark) and an
  "Attach to Family Tree" action - but `querySelector('a[href]')` grabbed whichever came
  first (always the view-record link), so the scraped "fsftid" was actually just that ark
  mislabeled as a Family Tree person ID, regardless of real attachment status. Confirmed
  live (attached vs. unattached rows on a real 1880 census collection) that a genuine
  attached Family Tree person only ever appears via a distinct
  `/tree/person/details/XXXX-XXXX` link with its own different ID. Now scrapes both
  correctly: `person_ark` (always present) and `fsftid` (only when genuinely attached).
  `person_ark` is wired into the citation as a `_WEBTAG`/`_LINK` pointing directly at that
  FamilySearch record.

### Changed
- **CensusConverter/** is renamed **Archivist/**; `CensusExtractor.py`/`.js` become
  `Archivist.py`/`.js`. The rename makes explicit what the tool actually does: it scrapes
  an already-indexed site (Ancestry.com) and pulls the index data that site has already
  produced, rather than transcribing anything itself, unlike Paleographer, which reads an
  unindexed image and has an AI transcribe it from scratch.
- **CountyFix/**, **LACDownloader/**, and **Dupes/** are renamed **Gazetteer/**,
  **Voyageur/**, and **Registrar/**, matching Archivist and Paleographer with names drawn
  from historical/archival roles instead of generic function names.

## [0.06.00] - 2026-07-25

### Added
- **Paleographer**: replaces the Register Transcriber. Transcribes any historical document
  type, not just Catholic parish registers, driven entirely by a single prompt file per
  type (`Paleographer/prompts/*.pmt`). Adding a new record type never requires writing any
  code: create one `.pmt` file (a small structured header declaring that type's event
  types, roles, default values, and any extra fields, plus a plain-language prompt body)
  and it appears in the Record Type dropdown automatically.
- **Scrip Records**: a first-draft record type for Metis and Half-breed scrip commission
  applications, proving the design generalizes beyond sacramental church records.
- **AI Assistant Batch API support**: large multi-page documents (like a full scrip case file)
  are submitted as a background batch job instead of a single real-time call, since a
  21-to-45-page bundle is too much for the synchronous API to handle reliably. Click
  "Step 1: Gather Data (API)" again later to retrieve results once AI Assistant finishes.
- Much of what used to be asked of the AI (constructing IDs, mapping event types and
  roles to codes, formatting dates, stripping diacritics, filling in default values) is
  now done deterministically in Python after the AI supplies the raw facts, cutting
  output tokens and removing a class of formatting errors.

### Changed
- The "Register Transcriber" tab is renamed "Paleographer" and gains a Record Type
  dropdown in place of the old Prompt Template dropdown.
- `ChurchRegisters/` is renamed `Paleographer/`; `ChurchGatherData.py` becomes
  `Paleographer.py`; `register_schema.json` becomes the generalized `schema.json`.
- "Step 2: Generate GEDCOM" is disabled unless Parish is the selected record type, since
  GEDCOM generation doesn't understand other record types yet.

## [0.05.00] - 2026-07-25

### Added
- **GEDCOM Builder**: A single, unified tool that replaces the separate Census Converter and
  Register to GEDCOM converter. Reads a JSON file (census-flavored or church-register-flavored,
  auto-detected) and generates both RootsMagic and Family Tree Maker GEDCOM files. Family Tree
  Maker output is new for church registers, which previously supported RootsMagic only.
- **Family Tree Maker support for church registers**: Citations, review-flag tasks, and witness
  or godparent links now all export correctly for FTM, matching a real native FTM export's
  conventions.

### Changed
- **Census Extractor now downloads JSON instead of CSV**, taking advantage of JSON's nesting to
  avoid repeating page and image metadata on every row. Update `CensusExtractor.js` in your
  TamperMonkey dashboard to the new version.
- Both the Census and Register tabs' GEDCOM-generation buttons now call the shared GEDCOM
  Builder instead of their own separate scripts.

### Removed
- **CensusConverter.py** and **ChurchCreateGedcom.py**, superseded by the unified GEDCOM Builder.

## [0.04.00] - 2026-07-25

### Added
- **LAC Downloader**: New tool that takes a pasted Library and Archives Canada / Heritage Canadiana URL and
  batch-downloads all high-resolution page images for that microfilm roll, organized into their own folder.
- **License**: The project is now licensed under the PolyForm Noncommercial License 1.0.0, free to use and
  modify for any noncommercial purpose, not for building or selling a commercial product or service.
- **Prompt Template selection**: The Register Transcriber tab now has a dropdown to pick which transcription
  prompt to use for a given register, and supports adding your own custom prompt templates.

### Changed
- **Settings now save per tool**: Global settings (API key, base folders, etc.) still save to the main
  settings file, but each tool's own settings now save inside that tool's own folder, so every tool stays
  fully self-contained.

## [0.03.00] - 2026-07-21

### Added
- **Census Extractor**: Paste an Ancestry.com census URL and it automatically downloads the record images
  and data, then generates the GEDCOM, no manual CSV handling required.
- **Tooltips** for every setting in the Toolbox.

### Changed
- Improved UI responsiveness and general speed optimizations.
- Census Converter's household-grouping logic made more accurate, with column-header fixes for the 1880
  census.

## [0.02.01] - 2026-07-18

### Changed
- Project-wide code cleanup: consistent PEP-8 formatting, removed unused imports and variables, and fixed
  several IDE-flagged warnings across all tools.

## [0.02.00] - 2026-07-17

### Added
- **Master GUI**: A unified, tabbed application to configure and run every tool from one window, with live
  console output, progress bars, and automatic `.env` management.
- **AI Assistant Cache Cleanup** utility.
- **Historical County Fixer**, correcting county/territory names in a RootsMagic tree to match historical
  boundaries for each event's date.
- **RootsMagic Duplicate Finder**, using fuzzy name/age matching and family cross-referencing to flag likely
  duplicate people for review.
- **Register Transcriber** (historical church register OCR/translation) and **Register to GEDCOM** converter.
- **Census to GEDCOM Converter**.

## [0.01.00] - 2026-07-12

### Added
- Initial upload of the project.
