// ==UserScript==
// @name         Voyageur
// @namespace    https://github.com/alerum68/Antiquarian
// @version      0.3.46
// @description  Gathers pages from supported Repositories. Detects which repository you're on from the URL and runs that repository's own gather logic.
// @author       alerum68
// @match        *://*.ancestry.com/imageviewer*
// @match        *://*.familysearch.org/ark:/*
// @match        *://sg30p0.familysearch.org/*
// @connect      *
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @run-at       document-start
// ==/UserScript==
// noinspection JSUnresolvedReference

(function () {
    'use strict';

    // Shared location utility: determines Country from state/province text.
    // Matches Gazetteer.CA_PROVINCE_NAMES (Gazetteer/Gazetteer.py) plus the historical
    // fur-trade-era names. Defaults to "USA" when state is empty or unrecognized.
    const CANADIAN_PROVINCES_AND_TERRITORIES = new Set([
        'alberta', 'british columbia', 'manitoba', 'new brunswick', 'newfoundland',
        'nova scotia', 'northwest territories', 'north-west territories', 'ontario',
        'prince edward island', 'quebec', 'saskatchewan', 'yukon', 'nunavut',
    ]);

    function getCountryFromState(state) {
        const normalized = (state || '').trim().toLowerCase();
        return CANADIAN_PROVINCES_AND_TERRITORIES.has(normalized) ? 'Canada' : 'USA';
    }
    // Shared by both runXGather() functions below. Resolves as soon as checkFn() returns a
    // truthy value, re-testing it on every DOM mutation (React re-rendering a panel, a
    // table's rows updating, a button's disabled attribute flipping) instead of on a fixed
    // timer - so a page that's already ready resolves immediately, and a slow one still
    // only waits as long as it actually takes, up to timeoutMs. Falls back to a plain
    // timeout if nothing mutates before then (e.g. waiting on something that will never
    // appear).
    function waitForCondition(checkFn, {timeoutMs = 15000, target = document.body} = {}) {
        return new Promise((resolve) => {
            const startedAt = performance.now();
            const immediate = checkFn();
            if (immediate) {
                resolve({result: immediate, elapsedMs: 0, timedOut: false});
                return;
            }

            let settled = false;
            const finish = (result, timedOut) => {
                if (settled) return;
                settled = true;
                observer.disconnect();
                clearTimeout(timer);
                resolve({result, elapsedMs: Math.round(performance.now() - startedAt), timedOut});
            };

            const observer = new MutationObserver(() => {
                const result = checkFn();
                if (result) finish(result, false);
            });
            observer.observe(target, {childList: true, subtree: true, attributes: true, characterData: true});

            const timer = setTimeout(() => finish(null, true), timeoutMs);
        });
    }

    // Shared by any gather's town/place-boundary stop condition (see runExtractionLoop
    // below): true only when every one of these fields matches exactly. enumeration_district
    // is included alongside city because some collections leave city blank/"Not Stated" for
    // an entire roll - ED is the finer-grained boundary those records actually expose.
    function placesMatch(a, b) {
        return a.state === b.state && a.county === b.county && a.city === b.city
            && a.enumeration_district === b.enumeration_district;
    }

    // Persists in-flight batch progress across a `location.reload()` triggered by the
    // index-not-loaded retry (see runExtractionLoop). Tampermonkey re-runs this entire
    // script from scratch on reload - without this, startBatch()'s reset of
    // accumulatedPages/batchPageCounter/seenPids would silently discard every page
    // gathered so far. sessionStorage (not GM_setValue) is deliberate: this only needs to
    // survive a same-tab reload, not a browser restart, and needs no new script permissions.
    const RELOAD_STATE_KEY = 'voyageur_a_reload_state';

    function saveReloadState(state) {
        sessionStorage.setItem(RELOAD_STATE_KEY, JSON.stringify({
            pageUrl: window.location.href,
            accumulatedPages: state.accumulatedPages,
            batchPageCounter: state.batchPageCounter,
            seenPids: Array.from(state.seenPids),
            firstPagePlace: state.firstPagePlace,
            pagesNeedingRetry: state.pagesNeedingRetry,
            retryPhase: state.retryPhase,
            currentRetryTarget: state.currentRetryTarget,
        }));
    }

    function loadReloadState() {
        const raw = sessionStorage.getItem(RELOAD_STATE_KEY);
        if (!raw) return null;
        let parsed;
        try {
            parsed = JSON.parse(raw);
        } catch (e) {
            return null;
        }
        if (parsed.pageUrl !== window.location.href) return null;
        return {
            accumulatedPages: parsed.accumulatedPages || [],
            batchPageCounter: parsed.batchPageCounter || 1,
            seenPids: new Set(parsed.seenPids || []),
            firstPagePlace: parsed.firstPagePlace || null,
            pagesNeedingRetry: parsed.pagesNeedingRetry || [],
            retryPhase: parsed.retryPhase || false,
            currentRetryTarget: parsed.currentRetryTarget || null,
        };
    }

    function clearReloadState() {
        sessionStorage.removeItem(RELOAD_STATE_KEY);
    }

    // Same sessionStorage-survives-a-reload convention as RELOAD_STATE_KEY above, but keyed
    // by runId (the gather's own mgs_run URL param, stable across every image in one run)
    // rather than the exact page URL. Confirmed live, and unexpected at first: clicking
    // FamilySearch's own "Next Image" button is a REAL page navigation, not client-side
    // routing, so Tampermonkey re-injects this entire script from scratch on every single
    // image. Keying by exact pageUrl (as originally written) never matched after such a
    // navigation, since the URL legitimately changes to the next image's own ark. Without
    // this fix, accumulatedItems silently reset to empty on every image after the first, and
    // only the LAST scraped item ever survived to the final JSON - confirmed live: a real
    // 3-image gather run's downloaded JSON contained exactly 1 item, not 3.
    const FS_RELOAD_STATE_KEY = 'voyageur_fs_reload_state';

    // Defense-in-depth alongside stopBatch()'s URL-stripping fix: a goToNextImage()
    // navigation already committed (browser mid-navigating to a URL FamilySearch's own
    // router built while mgs_auto=1 was still present) can land AFTER stopBatch() already
    // ran on the old page - too late for that page's history.replaceState() to change a
    // navigation already in flight to a different document. This sessionStorage flag
    // (keyed by run_id, so a genuinely new run is never blocked) survives that landing and
    // is checked by the auto-start trigger at the bottom of this function, alongside
    // shouldAutoStart itself.
    const FS_STOPPED_RUNS_KEY = 'voyageur_fs_stopped_runs';

    function markFsRunStopped(runId) {
        let stopped = [];
        try {
            stopped = JSON.parse(sessionStorage.getItem(FS_STOPPED_RUNS_KEY) || '[]');
        } catch (e) {
            stopped = [];
        }
        if (!stopped.includes(runId)) stopped.push(runId);
        sessionStorage.setItem(FS_STOPPED_RUNS_KEY, JSON.stringify(stopped));
    }

    function isFsRunStopped(runId) {
        try {
            return JSON.parse(sessionStorage.getItem(FS_STOPPED_RUNS_KEY) || '[]').includes(runId);
        } catch (e) {
            return false;
        }
    }

    // Called by startBatch() on every start (manual click or auto-triggered) - a deliberate
    // restart must not stay blocked by an earlier stop on the same run_id, since shouldAutoStart
    // (computed once per script injection) would otherwise keep coming back false on every
    // subsequent "Next Image" reload for the rest of this run.
    function clearFsRunStopped(runId) {
        let stopped = [];
        try {
            stopped = JSON.parse(sessionStorage.getItem(FS_STOPPED_RUNS_KEY) || '[]');
        } catch (e) {
            stopped = [];
        }
        sessionStorage.setItem(FS_STOPPED_RUNS_KEY, JSON.stringify(stopped.filter((id) => id !== runId)));
    }

    function saveFsReloadState(runId, state) {
        sessionStorage.setItem(FS_RELOAD_STATE_KEY, JSON.stringify({
            runId,
            accumulatedItems: state.accumulatedItems,
            seenItemIds: Array.from(state.seenItemIds),
            itemsAtLastCheckpoint: state.itemsAtLastCheckpoint,
            pagesNeedingRetry: state.pagesNeedingRetry,
            retryPhase: state.retryPhase,
            currentRetryTarget: state.currentRetryTarget,
            collectionId: state.collectionId,
            collectionName: state.collectionName,
        }));
    }

    function loadFsReloadState(runId) {
        const raw = sessionStorage.getItem(FS_RELOAD_STATE_KEY);
        if (!raw) return null;
        let parsed;
        try {
            parsed = JSON.parse(raw);
        } catch (e) {
            return null;
        }
        if (parsed.runId !== runId) return null;
        return {
            accumulatedItems: parsed.accumulatedItems || [],
            seenItemIds: new Set(parsed.seenItemIds || []),
            itemsAtLastCheckpoint: parsed.itemsAtLastCheckpoint || 0,
            pagesNeedingRetry: parsed.pagesNeedingRetry || [],
            retryPhase: parsed.retryPhase || false,
            currentRetryTarget: parsed.currentRetryTarget || null,
            collectionId: parsed.collectionId || '',
            collectionName: parsed.collectionName || '',
        };
    }

    function clearFsReloadState() {
        sessionStorage.removeItem(FS_RELOAD_STATE_KEY);
    }

    // DOM HELPERS - module-level (not nested in runFamilySearchGather) so they're reachable
    // from module.exports for testing, same as the pure API-parsing helpers below.
    function findByExactText(selector, text) {
        // FamilySearch's own CSS class names aren't a stable target (same reasoning as
        // Ancestry's findLeafByExactText) - match by visible text instead.
        const candidates = document.querySelectorAll(selector);
        for (const el of candidates) {
            if (el.textContent.trim() === text) return el;
        }
        return null;
    }

    // FamilySearch's "explore" viewer (confirmed live 2026-08-20) renders control labels
    // ("Names", "Save Record", "Information", "Go to image N") via aria-label on
    // otherwise-textless icon buttons - exhaustive textContent search (exact,
    // case-insensitive, substring, whole-DOM) found zero matches for any of them, while
    // [aria-label] search found them immediately. findByExactText can never find these; use
    // this instead for any control whose visible label isn't real DOM text.
    function findByAriaLabel(selector, label) {
        const candidates = document.querySelectorAll(selector);
        for (const el of candidates) {
            if ((el.getAttribute('aria-label') || '').trim() === label) return el;
        }
        return null;
    }

    // FamilySearch orchestration-API graph traversal - confirmed live (see
    // docs/superpowers/specs/2026-08-14-fs-orchestration-api-extraction-design.md) that
    // FamilySearch's own client fires GET .../orchestration/sls/image/{ark} automatically on
    // page load, returning a flat `elements` array cross-referenced by UUID/ark via
    // subElements/superElements - not a nested tree. These are pure, DOM-free functions:
    // build the {id: element} index once per response, then walk it.
    function buildFsElementIndex(apiResponse) {
        const byId = {};
        (apiResponse.elements || []).forEach((e) => { byId[e.id] = e; });
        return byId;
    }

    function fsFieldText(fieldElement) {
        const fv = fieldElement && fieldElement.fieldValues && fieldElement.fieldValues[0];
        if (!fv) return '';
        if (fv.normalizedValues && fv.normalizedValues[0]) return fv.normalizedValues[0].text || '';
        if (fv.origValue) return fv.origValue.text || '';
        return '';
    }

    function fsFindChild(byId, subElements, elementType) {
        if (!subElements) return null;
        for (const ref of subElements) {
            const el = byId[ref.id];
            if (el && el.elementType === elementType) return el;
        }
        return null;
    }

    // Direct indirection: PERSON -> FIELD (matching fieldType) -> text. Confirmed live for
    // RELATIONSHIP_TO_HEAD, MARITAL_STATUS, OCCUPATION, RACE_OR_COLOR, FTHR_BIR_PLACE,
    // MTHR_BIR_PLACE, SEX_CODE, SOURCE_HOUSEHOLD_ID, FS_HOUSEHOLD_ID, SOURCE_HOUSE_NBR - these
    // FIELD elements are direct children of PERSON, no wrapper element in between. Returns ''
    // when the field is genuinely absent (the 1850-1870 era case - not an error, just no such
    // question on that year's questionnaire).
    function fsPersonFieldText(byId, person, fieldType) {
        if (!person || !person.subElements) return '';
        for (const ref of person.subElements) {
            const el = byId[ref.id];
            if (el && el.elementType === 'FIELD' && el.fieldType === fieldType) return fsFieldText(el);
        }
        return '';
    }

    // Two-level indirection: PERSON -> {wrapperElementType, e.g. AGE} -> FIELD -> text.
    // Confirmed live for AGE - unlike the direct fields above, AGE is its own wrapping
    // element type, not a bare FIELD child of PERSON.
    function fsWrappedFieldText(byId, person, wrapperElementType) {
        const wrapper = fsFindChild(byId, person && person.subElements, wrapperElementType);
        if (!wrapper) return '';
        const field = fsFindChild(byId, wrapper.subElements, 'FIELD');
        return field ? fsFieldText(field) : '';
    }

    // Three-level indirection: PERSON -> NAME -> NAME_GIVEN/NAME_SURNAME -> FIELD -> text.
    // A person can have more than one NAME (multiple indexed variants, confirmed live: 101
    // NAME elements for 42 PERSON elements on one 1850 image) - prefers the one marked
    // primary, same convention already used for PERSON itself in this file, falling back to
    // the first NAME found when none is marked primary.
    function fsPersonName(byId, person) {
        const names = (person && person.subElements || [])
            .map((ref) => byId[ref.id])
            .filter((el) => el && el.elementType === 'NAME');
        const nameEl = names.find((n) => n.primary) || names[0];
        if (!nameEl) return {given: '', surname: ''};
        const givenEl = fsFindChild(byId, nameEl.subElements, 'NAME_GIVEN');
        const surnameEl = fsFindChild(byId, nameEl.subElements, 'NAME_SURNAME');
        const givenField = givenEl ? fsFindChild(byId, givenEl.subElements, 'FIELD') : null;
        const surnameField = surnameEl ? fsFindChild(byId, surnameEl.subElements, 'FIELD') : null;
        return {
            given: givenField ? fsFieldText(givenField) : '',
            surname: surnameField ? fsFieldText(surnameField) : '',
        };
    }

    // PERSON -> EVENT(eventType) -> PLACE -> FIELD -> text. Confirmed live both BIRTH and
    // CENSUS event types exist with this shape (design spec's element-type table: "Residence
    // (CENSUS) / birthplace (BIRTH)") - shared by fsPersonBirthPlace and
    // fsPersonResidencePlace below. Returns '' when the event is absent entirely (FamilySearch's
    // indexing didn't derive one for this person).
    function fsPersonEventPlace(byId, person, eventType) {
        const events = (person && person.subElements || [])
            .map((ref) => byId[ref.id])
            .filter((el) => el && el.elementType === 'EVENT');
        const event = events.find((e) => e.eventType === eventType);
        if (!event) return '';
        const placeEl = fsFindChild(byId, event.subElements, 'PLACE');
        if (!placeEl) return '';
        const field = fsFindChild(byId, placeEl.subElements, 'FIELD');
        return field ? fsFieldText(field) : '';
    }

    function fsPersonBirthPlace(byId, person) {
        return fsPersonEventPlace(byId, person, 'BIRTH');
    }

    // Confirmed live only via the design spec's single BIRTH-event example ("Maine, United
    // States") - a multi-segment CENSUS-event residence text's own internal comma order
    // (specific-to-general vs general-to-specific) has NOT been confirmed against a real
    // captured PLACE text (the design spec's own "STATE/COUNTY/TOWN proper scoping" item is
    // explicitly flagged unresolved). fsResidencePlaceToBrowsePath below assumes
    // specific-to-general, matching the one confirmed example - needs live verification
    // against a real CENSUS-event capture before being fully trusted.
    function fsPersonResidencePlace(byId, person) {
        return fsPersonEventPlace(byId, person, 'CENSUS');
    }

    // Converts a raw residence PLACE text into browsePath segments in the general-to-specific
    // order parseFsLocationFromBrowsePath()/FS.py's parse_census_browse_path() both expect
    // (matching fsImageIndexBrowsePathSegments' own segment order convention) - see the
    // live-verification caveat on fsPersonResidencePlace above.
    function fsResidencePlaceToBrowsePath(residenceText) {
        if (!residenceText) return [];
        return residenceText.split(',').map((s) => s.trim()).filter(Boolean).reverse();
    }

    // Image-level singleton FIELD lookup - unlike fsPersonFieldText (scoped to one PERSON's
    // direct children), EXT_FILM_NBR/EXT_PUB_NBR/EXT_REPOSITORY_NAME are confirmed live to
    // appear once per image, not per person (design spec's "Citation data" section) - safe to
    // find anywhere in the flat elements array.
    function fsImageLevelFieldText(apiResponse, fieldType) {
        const field = (apiResponse.elements || [])
            .find((e) => e.elementType === 'FIELD' && e.fieldType === fieldType);
        return field ? fsFieldText(field) : '';
    }

    // Confirmed live via a full raw orchestration-API
    // capture: STATE/COUNTY/CITY/DISTRICT_ENUMERATION are directly available as image-level
    // FIELD elements (attached to a shared PLACE element covering every person on the
    // page), far more reliable than parsing them back out of the citation prose text -
    // that depends on the exact phrasing fsBuildCitationTextFromApiResponse happens to
    // produce, and confirmed live to break down entirely once the trailing NARA clause is
    // absent. fsImageLevelFieldText's own .find() picks up this canonical entry correctly
    // even though several other (per-person residence, blank on this page) STATE/COUNTY/
    // CITY fields also exist elsewhere in the same elements array - confirmed live it's
    // always first in array order.
    function fsImageLevelLocation(apiResponse) {
        return {
            state: fsImageLevelFieldText(apiResponse, 'STATE'),
            county: fsImageLevelFieldText(apiResponse, 'COUNTY'),
            city: fsImageLevelFieldText(apiResponse, 'CITY'),
            enumeration_district: fsImageLevelFieldText(apiResponse, 'DISTRICT_ENUMERATION'),
        };
    }

    // RECORD.subElements directly lists the household's PERSON arks - confirmed live, no
    // separate id-matching needed the way the old UI-scraper had to reconstruct household
    // membership from DOM position.
    function fsHouseholds(apiResponse, byId) {
        return (apiResponse.elements || [])
            .filter((e) => e.elementType === 'RECORD')
            .map((record) => ({
                recordId: record.id,
                personIds: (record.subElements || [])
                    .map((ref) => ref.id)
                    .filter((id) => byId[id] && byId[id].elementType === 'PERSON'),
            }));
    }

    // Generic, non-curating field collector - walks every subElement reachable from PERSON
    // (2026-08-20, explicit user direction: capture the raw JSON in full, do NOT hand-pick
    // which fields matter or rename them at extraction time - renaming/mapping to GEDCOM
    // fields happens only downstream, in Archivist, via a declarative field map). Keys are
    // FamilySearch's own vocabulary verbatim (fieldType for direct FIELD children;
    // NAME_GIVEN/NAME_SURNAME for the name-wrapper shape; EVENT_<eventType>_PLACE for
    // event/place wrappers; the wrapper's own elementType, e.g. "AGE", for any other single
    // FIELD-holding wrapper) - nothing is dropped because we don't yet have a mapping for
    // it, and nothing is translated to a human-friendly label here. Grounded in the graph
    // shape confirmed live across two real captures (see fsPersonFieldText/fsWrappedFieldText/
    // fsPersonName/fsPersonEventPlace above and the 2026-08-14 design spec) - not yet
    // verified this covers every element type FamilySearch's API can return; extend the
    // three `if` branches below if a real capture surfaces a differently-shaped wrapper.
    function fsRawFieldsFromApiPerson(byId, person) {
        const raw = {};
        if (!person || !person.subElements) return raw;

        for (const ref of person.subElements) {
            const el = byId[ref.id];
            if (!el) continue;

            if (el.elementType === 'FIELD') {
                raw[el.fieldType || el.id] = fsFieldText(el);
                continue;
            }

            if (el.elementType === 'NAME') {
                const givenEl = fsFindChild(byId, el.subElements, 'NAME_GIVEN');
                const surnameEl = fsFindChild(byId, el.subElements, 'NAME_SURNAME');
                const givenField = givenEl ? fsFindChild(byId, givenEl.subElements, 'FIELD') : null;
                const surnameField = surnameEl ? fsFindChild(byId, surnameEl.subElements, 'FIELD') : null;
                if (givenField) raw.NAME_GIVEN = fsFieldText(givenField);
                if (surnameField) raw.NAME_SURNAME = fsFieldText(surnameField);
                continue;
            }

            if (el.elementType === 'EVENT') {
                const placeEl = fsFindChild(byId, el.subElements, 'PLACE');
                const placeField = placeEl ? fsFindChild(byId, placeEl.subElements, 'FIELD') : null;
                if (placeField) raw[`EVENT_${el.eventType || 'UNKNOWN'}_PLACE`] = fsFieldText(placeField);
                continue;
            }

            // Any other single-FIELD wrapper (e.g. AGE) - keyed by the wrapper's own
            // elementType, since the inner FIELD itself carries no distinguishing fieldType.
            const field = fsFindChild(byId, el.subElements, 'FIELD');
            if (field) raw[el.elementType] = fsFieldText(field);
        }

        return raw;
    }

    // True Family Tree person id (entityId) for a given record_ark, if
    // /service/tree/links/sources/attachments (intercepted in runFamilySearchGather, see
    // its own comment) has one on file - most personas were never attached by anyone, so
    // '' (never fabricated) is the common, expected result, not an error.
    // Matches by checking whether record_ark appears
    // WITHIN each stored source URI, not by exact dictionary-key equality - an exact-match
    // lookup kept failing live even when the same person was genuinely referenced, because
    // the stored URI can carry more around the persona ark than a clean extraction assumes
    // (trailing path segments, encoding). A substring match is strictly more permissive, so
    // it can only find a real match an exact lookup missed, never a false one (ark ids are
    // long, specific alphanumeric strings - two different real arks colliding as substrings
    // of each other is not a realistic risk).
    function fsPersonArkFromAttachments(recordArk) {
        if (typeof unsafeWindow === 'undefined' || !recordArk) return '';
        const map = unsafeWindow.__voyageurFsAttachments || {};
        for (const uri of Object.keys(map)) {
            if (uri.includes(recordArk)) {
                const found = map[uri];
                console.log(`[Voyageur FS ARK] lookup ${recordArk} -> ${found} (matched via ${uri})`);
                return found;
            }
        }
        console.log(`[Voyageur FS ARK] lookup ${recordArk} -> (none) | known uris: ${JSON.stringify(Object.keys(map))}`);
        return '';
    }

    function fsBuildRowsFromApiResponse(apiResponse) {
        const byId = buildFsElementIndex(apiResponse);
        const rows = [];
        for (const household of fsHouseholds(apiResponse, byId)) {
            for (const personId of household.personIds) {
                const person = byId[personId];
                if (!person) continue;

                const columns = fsRawFieldsFromApiPerson(byId, person);
                // record_ark: this persona's own record-scoped identifier (person.id) -
                // always present. person_ark: a true, enduring Family Tree identifier, only
                // when one is actually found (never fabricated) - see
                // docs/plans/2026-08-20-familysearch-viewer-rebuild.md Task 4.
                rows.push({columns, record_ark: person.id, person_ark: fsPersonArkFromAttachments(person.id)});
            }
        }
        return rows;
    }

    // Confirmed live 2026-08-21: /service/tree/links/sources/attachments fires MULTIPLE
    // separate times for one page's Names panel (one real capture showed 3 sources, then a
    // later, separate response with 12 more - not one single batch covering everyone at
    // once). waitForFsAttachments() only waits for the FIRST response to arrive, so
    // fsBuildRowsFromApiResponse() can still miss a person whose real entityId only shows up
    // in a LATER batch - confirmed live: Jess G Crowston's real KLBM-H9P mapping was stored
    // correctly, but only after his row had already been built with an empty person_ark, and
    // nothing ever went back to fill it in. This re-checks every row still missing a
    // person_ark across a few more short rounds, so a later batch still gets picked up
    // instead of being silently missed.
    async function backfillFsPersonArks(rows, {maxRounds = 6, roundDelayMs = 500} = {}) {
        for (let round = 0; round < maxRounds; round++) {
            const stillMissing = rows.filter((r) => !r.person_ark && r.record_ark);
            if (stillMissing.length === 0) return;
            await new Promise((resolve) => setTimeout(resolve, roundDelayMs));
            for (const row of stillMissing) {
                const resolved = fsPersonArkFromAttachments(row.record_ark);
                if (resolved) row.person_ark = resolved;
            }
        }
    }

    // Builds the same prose citation_text string FS.py's parse_citation()/
    // parse_nara_citing_clause() already regex-parse, entirely from JSON fields, so the
    // Names-panel extraction path no longer depends on scrapeCitationAndCatalog()'s UI read
    // for citation/location data (rows already came from the JSON path - see
    // fsBuildRowsFromApiResponse). collectionName/url/imageNumber/imageTotal come from the
    // caller - DOM/window reads with no JSON source in this endpoint's response, same caveat
    // fsBuildCitationTextFromImageIndexResponse already carries for imageNumber/imageTotal.
    function fsBuildCitationTextFromApiResponse(apiResponse, byId, primaryPerson, {collectionName = '', url = '', imageNumber, imageTotal} = {}) {
        const date = new Date().toLocaleDateString('en-US', {day: 'numeric', month: 'long', year: 'numeric'});

        const browsePath = fsResidencePlaceToBrowsePath(fsPersonResidencePlace(byId, primaryPerson));
        if (imageNumber && imageTotal) browsePath.push(`image ${imageNumber} of ${imageTotal}`);

        const publication = fsImageLevelFieldText(apiResponse, 'EXT_FILM_NBR');
        const rawRepoName = fsImageLevelFieldText(apiResponse, 'EXT_REPOSITORY_NAME');
        // Same trailing "(NARA)" stripping as fsBuildCitationTextFromImageIndexResponse -
        // confirmed live EXT_REPOSITORY_NAME carries the identical suffix ("The U.S. National
        // Archives and Records Administration (NARA)").
        const repoName = rawRepoName.replace(/\s*\([^)]*\)\s*$/, '').trim();
        const repoLoc = 'Washington D.C.';

        let text = `"${collectionName}," database with images, FamilySearch (${url} : ${date}), ${browsePath.join(' > ')}`;
        if (publication && repoName) {
            text += `; citing NARA microfilm publication ${publication} (${repoLoc}: ${repoName}, n.d.).`;
        } else {
            text += '.';
        }
        return text;
    }

    // filmdatainfo/image-data parsing (the "Image Index" page's own data source, reached via
    // Image Browser/township navigation - confirmed live to be a completely different
    // endpoint from the orchestration API, never firing on the same page as it). Confirmed
    // live against two real captures: records[] is one entry PER HOUSEHOLD, each holding
    // {fields[] (record/citation-level admin data), persons[]}. Each person mixes two shapes:
    // facts[] entries carry a convenience .value alongside their fuller .fields[].values[]
    // backing data; fields[] entries (relationship, household id, parents' birthplace) have
    // no .value shortcut and must be read via .values[], preferring Interpreted over Original.

    // Prefers the FIRST Interpreted value when multiple exist - confirmed live this is
    // correct, not an arbitrary choice: the head-of-household's own RelationshipToHead field
    // carries a correction trail (Original "Self", then two Interpreted entries "Head" then
    // "Self") where the first Interpreted entry is the right one.
    function fsImageIndexFieldText(fieldOrFact) {
        if (!fieldOrFact) return '';
        if (typeof fieldOrFact.value === 'string') return fieldOrFact.value;
        const values = fieldOrFact.values || [];
        const interpreted = values.find((v) => v.type === 'http://gedcomx.org/Interpreted');
        if (interpreted) return interpreted.text || '';
        const original = values.find((v) => v.type === 'http://gedcomx.org/Original');
        return original ? (original.text || '') : '';
    }

    function fsImageIndexFindByType(list, type) {
        return (list || []).find((item) => item.type === type) || null;
    }

    // GedcomX type URIs are the closest thing this API has to a "raw field name" - keyed by
    // their trailing path segment (e.g. "http://gedcomx.org/MaritalStatus" -> "MaritalStatus")
    // rather than translated to a human-friendly label, same "capture everything, rename
    // nothing at extraction time" direction as fsRawFieldsFromApiPerson above.
    function fsLastUriSegment(uri) {
        if (!uri) return '';
        const parts = String(uri).split('/');
        return parts[parts.length - 1] || String(uri);
    }

    function fsRawFieldsFromImageIndexPerson(person) {
        const raw = {};
        const nameForm = person && person.names && person.names[0]
            && person.names[0].nameForms && person.names[0].nameForms[0];
        for (const part of (nameForm && nameForm.parts) || []) {
            if (part.type) raw[fsLastUriSegment(part.type)] = part.value || '';
        }
        if (person && person.gender && person.gender.type) {
            raw.Gender = fsLastUriSegment(person.gender.type);
        }
        for (const field of (person && person.fields) || []) {
            if (field.type) raw[fsLastUriSegment(field.type)] = fsImageIndexFieldText(field);
        }
        for (const fact of (person && person.facts) || []) {
            if (!fact.type) continue;
            const key = fsLastUriSegment(fact.type);
            raw[key] = fsImageIndexFieldText(fact);
            // A fact's place can be a single "Place" field (Birth) or several sub-fields
            // (Census: State/County/Township/District) - walk all of them rather than
            // looking for one specific type, so nothing under place.fields is missed.
            for (const placeField of (fact.place && fact.place.fields) || []) {
                if (placeField.type) raw[`${key}_${fsLastUriSegment(placeField.type)}`] = fsImageIndexFieldText(placeField);
            }
        }
        return raw;
    }

    function fsBuildRowsFromImageIndexResponse(apiResponse) {
        const rows = [];
        const records = (apiResponse && apiResponse.records) || [];
        records.forEach((record) => {
            (record.persons || []).forEach((person) => {
                const columns = fsRawFieldsFromImageIndexPerson(person);

                // identifiers[...][0] is a full URL (.../ark:/61903/1:1:XXXX-XXX) - extract
                // just the "1:1:XXXX-XXX" segment to match the orchestration-API path's own
                // record_ark convention (a bare ark, not a full URL).
                const identifierUrl = (person.identifiers
                    && person.identifiers['http://gedcomx.org/Persistent']
                    && person.identifiers['http://gedcomx.org/Persistent'][0]) || '';
                const arkMatch = identifierUrl.match(/(1:1:[A-Z0-9-]+)/);
                const recordArk = arkMatch ? arkMatch[1] : '';

                rows.push({columns, record_ark: recordArk, person_ark: fsPersonArkFromAttachments(recordArk)});
            });
        });
        return rows;
    }

    // Prefers Township (the more common modern label) but falls back to MinorCivilDivision -
    // confirmed live these are the same third-level-locality concept under two different type
    // URIs depending on era/collection (1880 sample used Township, 1860 sample used
    // MinorCivilDivision for an identically-shaped place). Matches FS.py's own
    // parse_census_browse_path(), which reads browse-path segments positionally, not by an
    // explicit state/county/township prefix - so segment ORDER here (state, county, township,
    // then "ED n" appended last) must stay exactly this order.
    function fsImageIndexBrowsePathSegments(censusFact) {
        const placeFields = (censusFact && censusFact.place && censusFact.place.fields) || [];
        const state = fsImageIndexFieldText(fsImageIndexFindByType(placeFields, 'http://gedcomx.org/State'));
        const county = fsImageIndexFieldText(fsImageIndexFindByType(placeFields, 'http://gedcomx.org/County'));
        const township = fsImageIndexFieldText(fsImageIndexFindByType(placeFields, 'http://gedcomx.org/Township'))
            || fsImageIndexFieldText(fsImageIndexFindByType(placeFields, 'http://gedcomx.org/MinorCivilDivision'));
        const district = fsImageIndexFieldText(fsImageIndexFindByType(placeFields, 'http://gedcomx.org/District'));
        const segments = [state, county, township].filter(Boolean);
        if (district) segments.push(district);
        return segments;
    }

    // Builds the same prose citation_text string FS.py's parse_citation()/
    // parse_nara_citing_clause() already regex-parse (Voyageur/FS.py CITATION_RE/
    // NARA_CITING_RE) - entirely from JSON fields, so the Image-Index extraction path never
    // depends on scrapeCitationAndCatalog()'s UI read. imageNumber/imageTotal come from the
    // caller (the one remaining UI fallback in this whole plan - no JSON source for the total
    // image count was found in this endpoint's response).
    function fsBuildCitationTextFromImageIndexResponse(apiResponse, {imageNumber, imageTotal} = {}) {
        const record = ((apiResponse && apiResponse.records) || [])[0];
        if (!record) return '';
        const headPerson = (record.persons || [])[0];
        const recordFields = record.fields || [];
        const personFields = headPerson ? (headPerson.fields || []) : [];

        const collectionName = ((apiResponse.collections || [])[0]
            && apiResponse.collections[0].collections
            && apiResponse.collections[0].collections[0]
            && apiResponse.collections[0].collections[0].title) || '';
        const url = apiResponse.imageURL || '';
        const date = new Date().toLocaleDateString('en-US', {day: 'numeric', month: 'long', year: 'numeric'});

        const censusFact = headPerson ? fsImageIndexFindByType(headPerson.facts || [], 'http://gedcomx.org/Census') : null;
        const browsePath = fsImageIndexBrowsePathSegments(censusFact);
        if (imageNumber && imageTotal) browsePath.push(`image ${imageNumber} of ${imageTotal}`);

        const publication = fsImageIndexFieldText(fsImageIndexFindByType(recordFields, 'http://familysearch.org/types/fields/FilmNbr'))
            || fsImageIndexFieldText(fsImageIndexFindByType(recordFields, 'http://familysearch.org/types/fields/DigitalFilmNbr'));
        const rawRepoName = fsImageIndexFieldText(fsImageIndexFindByType(personFields, 'http://familysearch.org/types/fields/ExtRepositoryName'));
        // Confirmed live: the real value carries a trailing "(NARA)" that would otherwise
        // break NARA_CITING_RE's repo_name group ([^,()]+ - excludes parentheses).
        const repoName = rawRepoName.replace(/\s*\([^)]*\)\s*$/, '').trim();
        // NARA repository location - no matching JSON field found in either captured sample
        // (see design spec's "Not yet verified" list). Hardcoded: every US census NARA
        // microfilm citation this project has observed cites this same physical archive
        // location regardless of the specific record.
        const repoLoc = 'Washington D.C.';

        let text = `"${collectionName}," database with images, FamilySearch (${url} : ${date}), ${browsePath.join(' > ')}`;
        if (publication && repoName) {
            text += `; citing NARA microfilm publication ${publication} (${repoLoc}: ${repoName}, n.d.).`;
        } else {
            text += '.';
        }
        return text;
    }


    // Dispatch by hostname, the same way Voyageur.py's Python side dispatches by an explicit
    // source-code argument - adding a new Major Repository here is a new @match line above
    // plus a new runXGather() function below, nothing else touched.
    const host = window.location.hostname;

    if (host.includes('ancestry.com')) {
        runAncestryGather();
    } else if (host === 'sg30p0.familysearch.org') {
        runFsImageIframeHelper();
    } else if (host.includes('familysearch.org')) {
        runFamilySearchGather();
    }

    // ==========================================
    // FAMILYSEARCH IMAGE HELPER - runs inside the hidden iframe downloadFsImage (below)
    // creates, same-origin relative to the deepzoomcloud image itself. Direct access to
    // this endpoint from the FamilySearch record page's own origin is blocked - confirmed
    // live across three separate mechanisms: GM_xmlhttpRequest hangs to timeout, a plain
    // cross-origin fetch() gets 429 even on a brand-new item_id never requested before,
    // and a plain cross-origin <a download> triggers nothing at all - while a genuine
    // top-level navigation to this exact URL (what the site's own "Print" button does)
    // always works cleanly. Loading it in a hidden iframe gets a real navigation (no
    // popup-blocker issue, unlike window.open(), and the parent page's own state/loop
    // isn't disturbed the way navigating the parent tab itself would be), and this
    // function then runs same-origin inside that iframe, where fetch() is no longer
    // cross-origin and none of the above restrictions apply.
    // ==========================================
    function runFsImageIframeHelper() {
        // Only fetches here and hands the raw bytes back to the parent via postMessage -
        // confirmed live that triggering the actual <a download> click from *inside* this
        // iframe runs the whole fetch+blob chain successfully (postMessage 'done' arrives,
        // the image visibly renders) but never actually saves a file, while the identical
        // click mechanism triggered from the top-level record page (the JSON downloads)
        // always works - browsers commonly restrict download-triggering from a nested
        // iframe even when same-origin. The parent does the actual click instead, in the
        // same top-level context that already reliably downloads the JSON.
        const match = window.location.pathname.match(/\/dz\/v1\/([^/]+)\/\$dist/);
        let itemId = match ? decodeURIComponent(match[1]) : "unknown_item";
        itemId = itemId.replace(/^(3:1:3|1:1:)/, '');
        const fileName = `${itemId.replace(/[^a-zA-Z0-9_-]/g, '_')}.jpg`;

        fetch(window.location.href)
            .then(response => {
                if (!response.ok) {
                    window.parent.postMessage({voyageurFsImage: 'error', status: response.status}, '*');
                    return;
                }
                return response.arrayBuffer().then(buffer => {
                    window.parent.postMessage({voyageurFsImage: 'data', buffer, fileName}, '*', [buffer]);
                });
            })
            .catch(e => {
                window.parent.postMessage({voyageurFsImage: 'error', message: String(e)}, '*');
            });
    }

    // ==========================================
    // ANCESTRY (A) - census index + image gather
    // ==========================================
    function runAncestryGather() {
        // GUARD: Check if the extractor has been explicitly triggered for this session or via URL
        if (sessionStorage.getItem('run_census_extractor') !== 'true' && !window.location.href.includes('mgs_auto=1')) {
            return; // Exit immediately, doing nothing
        }

        // Clear the flag so it only runs once per trigger (optional)
        sessionStorage.removeItem('run_census_extractor');

        const DEBUG_MODE = true;
        let isAutoExtracting = false;
        let accumulatedPages = [];
        let batchPageCounter = 1;
        let seenPids = new Set();
        let firstPagePlace = null;
        let pagesNeedingRetry = [];
        let inRetryPhase = false;
        let currentRetryTarget = null;

        // A location.reload() triggered by the index-not-loaded retry (see
        // runExtractionLoop) re-runs this whole script from scratch. Restore whatever was
        // saved right before that reload so the batch resumes instead of startBatch()
        // silently wiping every page gathered so far.
        let resumingFromReload = false;
        const resumedState = loadReloadState();
        if (resumedState) {
            accumulatedPages = resumedState.accumulatedPages;
            batchPageCounter = resumedState.batchPageCounter;
            seenPids = resumedState.seenPids;
            firstPagePlace = resumedState.firstPagePlace;
            pagesNeedingRetry = resumedState.pagesNeedingRetry;
            inRetryPhase = resumedState.retryPhase;
            currentRetryTarget = resumedState.currentRetryTarget;
            resumingFromReload = true;
            clearReloadState();
        }

        const shouldAutoStart = window.location.href.includes('mgs_auto=1');
        const runId = new URLSearchParams(window.location.search).get('mgs_run') || 'norun';

        function debugLog(msg) {
            if (DEBUG_MODE) {
                console.log(`[MGS DEBUG] ${msg}`);
            }
        }

        if (typeof unsafeWindow !== 'undefined' && !unsafeWindow.__mgs_intercepted) {
            unsafeWindow.__mgs_intercepted = true;
            unsafeWindow.__mgs_pids = [];
            // State for the index-panel-data interceptor below - separate from __mgs_pids
            // since this captures the FULL per-person field response, not just a PID list.
            unsafeWindow.__voyageurAncestryIndexPanelResponses = {};
            // Per-"dbId:imageId" resolver callbacks for waitForAncestryIndexPanelResponse()
            // below - same waiter-map pattern as FS's __voyageurFsApiWaiters elsewhere in
            // this file. Storing a response sets a plain object property, not a DOM node -
            // waitForCondition()'s MutationObserver would never see it, so waiters are
            // notified directly here instead.
            unsafeWindow.__voyageurAncestryIndexPanelWaiters = {};

            const ANCESTRY_INDEX_PANEL_TARGET = '/imageviewer/api/record/index-panel-data';

            function ancestryIndexPanelKeyFromUrl(url) {
                const dbMatch = url.match(/[?&]dbId=([^&]+)/);
                const imgMatch = url.match(/[?&]imageId=([^&]+)/);
                if (!dbMatch || !imgMatch) return null;
                return `${decodeURIComponent(dbMatch[1])}:${decodeURIComponent(imgMatch[1])}`;
            }

            function storeAncestryIndexPanelResponse(url, bodyText) {
                const key = ancestryIndexPanelKeyFromUrl(url);
                if (!key) return;
                try {
                    const parsed = JSON.parse(bodyText);
                    unsafeWindow.__voyageurAncestryIndexPanelResponses[key] = parsed;
                    const waiter = unsafeWindow.__voyageurAncestryIndexPanelWaiters[key];
                    if (waiter) waiter(parsed);
                } catch (e) {
                    // Leave unset - waitForAncestryIndexPanelResponse() below times out the
                    // same as "never arrived", the correct behavior for an unparseable body.
                }
            }

            function extractPidsFromText(text) {
                try {
                    let parsed;
                    try {
                        parsed = JSON.parse(text);
                    } catch (e) {
                        parsed = null;
                    }

                    if (parsed && Array.isArray(parsed.RecordRectangles) && parsed.RecordRectangles.length > 0) {
                        const ids = parsed.RecordRectangles
                            .map(r => (r && r.RecordId != null) ? String(r.RecordId) : null)
                            .filter(id => id !== null);
                        if (ids.length > 0) {
                            unsafeWindow.__mgs_pids = ids;
                            return;
                        }
                    }

                    const matches = [...text.matchAll(/"(?:recordId|pId|clientRecordId)"\s*:\s*"?(\d{5,15})"?/gi)];
                    if (matches.length > 2) {
                        unsafeWindow.__mgs_pids = matches.map(m => m[1]);
                    }
                } catch (e) {
                }
            }

            const origFetch = unsafeWindow.fetch;
            unsafeWindow.fetch = async function (...args) {
                const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
                const response = await origFetch.apply(this, args);
                try {
                    const clone = response.clone();
                    clone.text().then(text => {
                        extractPidsFromText(text);
                        if (url.includes(ANCESTRY_INDEX_PANEL_TARGET)) {
                            storeAncestryIndexPanelResponse(url, text);
                        }
                    }).catch(() => {
                    });
                } catch (e) {
                }
                return response;
            };

            const origOpen = unsafeWindow.XMLHttpRequest.prototype.open;
            unsafeWindow.XMLHttpRequest.prototype.open = function (method, url) {
                this.addEventListener('load', function () {
                    extractPidsFromText(this.responseText);
                    if (url && url.includes(ANCESTRY_INDEX_PANEL_TARGET)) {
                        storeAncestryIndexPanelResponse(url, this.responseText);
                    }
                });
                origOpen.apply(this, arguments);
            };
        }

        // Instant resolution if the response already arrived before this was called (the
        // API call fires on page load, same as the DOM table's own data source - by the
        // time extractCurrentPageData's caller has already confirmed the DOM table
        // populated, this response has almost always already arrived too). Falls back to a
        // bounded timer only when the API genuinely never fires for this collection/page.
        async function waitForAncestryIndexPanelResponse(dbId, imageId, {timeoutMs = 8000} = {}) {
            const key = `${dbId}:${imageId}`;
            const startedAt = performance.now();
            const existing = (unsafeWindow.__voyageurAncestryIndexPanelResponses || {})[key];
            if (existing) {
                return {result: existing, elapsedMs: 0, timedOut: false};
            }
            return new Promise((resolve) => {
                let settled = false;
                const timer = setTimeout(() => {
                    if (settled) return;
                    settled = true;
                    delete unsafeWindow.__voyageurAncestryIndexPanelWaiters[key];
                    resolve({result: null, elapsedMs: Math.round(performance.now() - startedAt), timedOut: true});
                }, timeoutMs);
                unsafeWindow.__voyageurAncestryIndexPanelWaiters[key] = (result) => {
                    if (settled) return;
                    settled = true;
                    clearTimeout(timer);
                    delete unsafeWindow.__voyageurAncestryIndexPanelWaiters[key];
                    resolve({result, elapsedMs: Math.round(performance.now() - startedAt), timedOut: false});
                };
            });
        }

        function initUI() {
            if (document.getElementById('extractor-ui-container')) return;

            const style = document.createElement('style');
            style.innerHTML = `
                #extractor-ui-container { position: fixed; bottom: 20px; right: 20px; background-color: #1a1a1a; color: white; border-radius: 8px; padding: 12px 16px; font-family: sans-serif; z-index: 999999; box-shadow: 0 4px 12px rgba(0,0,0,0.5); display: flex; flex-direction: column; gap: 10px; align-items: center; border: 1px solid #333; min-width: 180px; }
                #extractor-header { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; padding-bottom: 4px; border-bottom: 1px solid #333; }
                #extractor-title { font-weight: bold; font-size: 15px; }
                #extractor-status-light { width: 12px; height: 12px; border-radius: 50%; background-color: #22c55e; box-shadow: 0 0 8px #22c55e; transition: all 0.3s ease; }
                #extractor-status-light.running { background-color: #3b82f6; box-shadow: 0 0 10px #3b82f6; animation: pulse-light 1.5s infinite; }
                @keyframes pulse-light { 0% { transform: scale(0.95); opacity: 0.8; } 50% { transform: scale(1.1); opacity: 1; } 100% { transform: scale(0.95); opacity: 0.8; } }
                .extractor-btn { color: white; border: none; border-radius: 6px; padding: 10px 14px; font-size: 14px; font-weight: bold; cursor: pointer; width: 100%; transition: background-color 0.2s; }
                #ext-start-btn { background-color: #2b7a4b; } #ext-start-btn:hover { background-color: #1e5935; }
                #ext-stop-btn { background-color: #991b1b; display: none; }
                #extractor-toast-container { position: fixed; bottom: 140px; right: 20px; z-index: 999999; display: flex; flex-direction: column; gap: 10px; pointer-events: none; }
                .extractor-toast { background-color: #333; color: #fff; padding: 12px 20px; border-radius: 6px; font-size: 14px; opacity: 0; transform: translateY(10px); transition: opacity 0.3s, transform 0.3s; }
                .extractor-toast.show { opacity: 1; transform: translateY(0); }
                .extractor-toast.error { background-color: #b91c1c; } .extractor-toast.success { background-color: #15803d; }
            `;
            document.head.appendChild(style);

            const toastContainer = document.createElement('div');
            toastContainer.id = 'extractor-toast-container';
            document.body.appendChild(toastContainer);

            window.showToast = function (message, type = 'success', duration = 2500) {
                const toast = document.createElement('div');
                toast.className = `extractor-toast ${type}`;
                toast.innerText = message;
                toastContainer.appendChild(toast);
                void toast.offsetWidth;
                toast.classList.add('show');
                setTimeout(() => {
                    toast.classList.remove('show');
                    setTimeout(() => toast.remove(), 300);
                }, duration);
            }

            const controlPanel = document.createElement('div');
            controlPanel.id = 'extractor-ui-container';

            const header = document.createElement('div');
            header.id = 'extractor-header';
            const statusLight = document.createElement('div');
            statusLight.id = 'extractor-status-light';
            const title = document.createElement('span');
            title.id = 'extractor-title';
            title.innerText = 'Voyageur (A)';

            header.appendChild(statusLight);
            header.appendChild(title);
            controlPanel.appendChild(header);

            const startBtn = document.createElement('button');
            startBtn.id = 'ext-start-btn';
            startBtn.className = 'extractor-btn';
            startBtn.innerText = 'Start Auto-Batch';

            const stopBtn = document.createElement('button');
            stopBtn.id = 'ext-stop-btn';
            stopBtn.className = 'extractor-btn';
            stopBtn.innerText = 'Stop & Download JSON';

            controlPanel.appendChild(startBtn);
            controlPanel.appendChild(stopBtn);
            document.body.appendChild(controlPanel);

            startBtn.addEventListener('click', startBatch);
            stopBtn.addEventListener('click', stopBatch);

            window._startBtn = startBtn;
            window._stopBtn = stopBtn;
            window._statusLight = statusLight;
        }

        function getYearAndLocation() {
            let year = "UnknownYear";
            let locationStr = "Unknown_Location";

            if (typeof unsafeWindow !== 'undefined' && unsafeWindow.__PRELOADED_STATE__) {
                try {
                    const state = unsafeWindow.__PRELOADED_STATE__;
                    if (state.viewer) {
                        // collectionInfo lives under viewer.imageInfo, not directly under viewer -
                        // reading the wrong path here silently failed and always fell through to
                        // the document.title regex below.
                        year = state.viewer.imageInfo?.collectionInfo?.publicationYear || year;
                        const path = state.viewer.imageInfo?.browsePath;
                        if (path && path.length > 0) locationStr = path.join(' - ');
                    }
                } catch (err) {
                }
            }
            if (year === "UnknownYear") {
                const yearMatch = document.title.match(/(1[7-9]\d\d|19[0-6]\d)/);
                if (yearMatch) year = yearMatch[0];
            }
            locationStr = locationStr.replace(/[/\\?%*:|"<>]/g, '-').replace(/\s+/g, ' ').trim();
            return {year, locationStr};
        }

        function getBaseImageId() {
            const urlMatch = window.location.href.match(/images\/([^?&/]+)/);
            if (urlMatch) return urlMatch[1].replace(/[^a-zA-Z0-9_-]/g, '');
            return "Unknown_Image";
        }

        function getEnumerationDistrict(pathArr, structureType) {
            // Different collections/years can have different browsePath depths and orders (e.g.
            // some don't have an Enumeration District level at all). Use the collection's own
            // structureType labels from __PRELOADED_STATE__ to find which position actually holds
            // the ED instead of assuming a fixed index. Returns the index too (not just the
            // value) so callers building place_details from the remaining path segments can
            // exclude it, rather than duplicating the ED into both fields (confirmed happening
            // across every 4-segment browsePath year from 1880 on: static-checked all 17 US
            // census years' real browsePath/structureType shapes against this function).
            const keys = Object.keys(structureType || {});
            const namedIndex = keys.findIndex(k => /district/i.test(k));
            if (namedIndex !== -1 && pathArr[namedIndex]) return {value: pathArr[namedIndex], index: namedIndex};
            if (pathArr.length > 3) return {value: pathArr[pathArr.length - 1], index: pathArr.length - 1};
            return {value: "", index: -1};
        }

        function parseFilmRollFromImageId(imageId) {
            // Fallback only, used when the real Source Citation can't be scraped (see
            // scrapeSourceCitation below). Ancestry image IDs for NARA-microfilmed collections
            // sometimes encode the microfilm publication and roll, e.g. "M-T0627-03009-00399"
            // -> NARA publication T627, roll 3009. Not all collections use this scheme (in
            // fact most pre-1940 ones don't), so a non-match just leaves both blank rather
            // than guessing wrong.
            const match = imageId.match(/^[A-Z]-([A-Z])0*(\d+)-0*(\d+)-\d+$/i);
            if (!match) return {film: "", roll: ""};
            const [, letter, filmNum, rollNum] = match;
            return {film: `${letter.toUpperCase()}${filmNum}`, roll: rollNum};
        }

        function findLeafByExactText(text) {
            // Ancestry's CSS class names for the info-panel tab bar aren't stable across
            // deploys, so match by the tab's own visible label instead of a selector.
            const candidates = document.querySelectorAll('button, a, [role="tab"], li, div, span');
            for (const el of candidates) {
                if (el.children.length === 0 && el.textContent.trim() === text) return el;
            }
            return null;
        }

        function parseCitationFields(citationText) {
            const fields = {};
            citationText.split(';').forEach(part => {
                const idx = part.indexOf(':');
                if (idx === -1) return;
                const label = part.slice(0, idx).trim();
                const value = part.slice(idx + 1).trim();
                if (label) fields[label] = value;
            });
            return fields;
        }

        function parseSourceInformation(text) {
            // Ancestry's (and, going by the same convention, FamilySearch's) "Source
            // Information" prose follows a consistent two-sentence template:
            // "<Repository>. <Collection> [database on-line]. <City, ST, USA>: <Repository
            // Entity>, <year>.\n\nOriginal data: <description>. <City, ST/Country>: <Publisher>,
            // <year>. <extra>." Pull every "<Location>: <Entity>, <Year>." pair out of it; the
            // first is always the online-database repository, the last is always the original
            // data's publisher. ("Original data:" itself sometimes false-matches as a pair in
            // between the two real ones, since it's also followed eventually by a ", <year>."
            // - harmless, since only the first and last matches are actually used.)
            const result = {repository: "", repositoryLoc: "", publisher: "", pubLoc: ""};
            if (!text) return result;

            // The repository name is the leading "<Name>. " clause, e.g. "Ancestry.com. " or
            // "FamilySearch.org. " - matched as a dotted domain-like token so it isn't cut
            // short at its own internal period.
            const repoMatch = text.match(/^([A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)\.\s/);
            if (repoMatch) result.repository = repoMatch[1].trim();

            // The trailing year is sometimes literally "n.d." (no date) instead of a real
            // year - confirmed live on a real citation ("...National Archives and Records
            // Administration, n.d.") - which silently broke this match entirely, since only
            // the first (Ancestry.com) pair would then be found and get reused for both
            // repository and publisher, defeating the whole point of citing NARA instead.
            // Note: "n.d" (not "n.d.") - the outer \. right after the group supplies the
            // final period, so "n.d." + outer \. would wrongly require two trailing dots.
            const pairs = [...text.matchAll(
                /([A-Z][A-Za-z., ]{2,60}?):\s*([A-Z][A-Za-z., &]{2,80}?),\s*(?:1[6-9]\d{2}|20\d{2}|n\.d)\./g)];
            if (pairs.length > 0) {
                result.repositoryLoc = pairs[0][1].trim().replace(/,\s*USA$/i, '');
            }
            if (pairs.length > 1) {
                const last = pairs[pairs.length - 1];
                result.pubLoc = last[1].trim();
                result.publisher = last[2].trim();
            }
            return result;
        }

        async function scrapeSourceCitation() {
            // The real, authoritative Roll/Film/ED/Year/Repository/Publisher live in the info
            // panel's "Source" tab as rendered text (e.g. "Year: 1940; Census Place: St Thomas,
            // Pembina, North Dakota; Roll: m-t0627-03009; Page: 6A; Enumeration District:
            // 34-33" plus a separate "Source Information" paragraph), not in
            // __PRELOADED_STATE__ or any network response we could otherwise intercept. Must
            // be re-scraped on every page: NARA microfilm rolls don't align with county/ED
            // boundaries, so a roll can change mid-run without any change in
            // County/Township/ED to signal it.
            const sourceTab = findLeafByExactText('Source');

            // aria-controls names the DOM element that actually holds this tab's content.
            // Live testing confirmed this container is mounted in the DOM from the very
            // start (before any clicking) - yet its text never appeared in
            // document.body.innerText no matter how long we waited. innerText respects CSS
            // visibility/layout; textContent doesn't. That means the pane is present but
            // not "visible" by that reckoning (off-screen or zero-height until selected,
            // rather than removed from the DOM) - so read the container's own textContent
            // directly instead of waiting on it to become visible. Since the data is
            // already mounted, this needs no wait at all.
            const sourceContentId = sourceTab ? sourceTab.getAttribute('aria-controls') : null;
            const sourceContentEl = sourceContentId ? document.getElementById(sourceContentId) : null;

            if (sourceTab) sourceTab.click();

            let citationBlock = "", infoBlock = "";

            if (sourceContentEl) {
                const rawText = (sourceContentEl.textContent || "").replace(/\s+/g, ' ').trim();

                if (DEBUG_MODE) {
                    console.log(`[MGS DEBUG] citation: container raw text (first 1000 chars): ${rawText.slice(0, 1000)}`);
                }

                // Loosened from the old \n+-based boundaries (textContent has no layout
                // whitespace to rely on) - anchored on the literal heading words instead,
                // which should still appear verbatim regardless of surrounding markup. No
                // trailing \b: confirmed live that Ancestry concatenates directly with no
                // separator at all ("...803094DescriptionCity: Pembina..."), so the
                // transition from the heading's last letter into the next section's first
                // letter is word-to-word, never a \b boundary - a \b here made the whole
                // match silently fail every time.
                const citeMatch = rawText.match(
                    /Source Citation\s*(.*?)\s*(?:Source Information|Description|Source Description|Person)/);
                if (citeMatch && citeMatch[1].trim()) citationBlock = citeMatch[1].trim();

                const infoMatch = rawText.match(/Source Information\s*(.*?)\s*(?:Source Description|Person)/);
                if (infoMatch && infoMatch[1].trim()) infoBlock = infoMatch[1].trim();

                if (DEBUG_MODE) {
                    console.log(`[MGS DEBUG] citation: parsed citationBlock: "${citationBlock.slice(0, 300)}" | infoBlock: "${infoBlock.slice(0, 300)}"`);
                }
            } else if (DEBUG_MODE) {
                console.log(`[MGS DEBUG] citation: "Source" tab found: ${!!sourceTab} | content container found: false`);
                if (!sourceTab) {
                    // The tab's exact label may have changed - list every short leaf
                    // element's text so the real label (if any) is visible in the console
                    // instead of guessing blind.
                    const candidateLabels = [...new Set(
                        [...document.querySelectorAll('button, a, [role="tab"], li, span')]
                            .filter(el => el.children.length === 0)
                            .map(el => el.textContent.trim())
                            .filter(t => t && t.length <= 30)
                    )].slice(0, 40);
                    console.log(`[MGS DEBUG] citation: no exact "Source" leaf found - candidate short labels on page: ${JSON.stringify(candidateLabels)}`);
                }
            }

            if (!citationBlock) {
                return {
                    roll: "", film: "", ed: "", year: "",
                    repository: "", repositoryLoc: "", publisher: "", pubLoc: ""
                };
            }

            const fields = parseCitationFields(citationBlock);
            const info = parseSourceInformation(infoBlock);

            // Ancestry's own Roll value is formatted "<roll number>_<film number>"
            // (confirmed live, e.g. "M653_94" - "M653" is the roll number, "94" is the film
            // number). Split them into Archivist's separate Roll/Film fields instead of
            // carrying the combined string as "roll" alone - distinct from the separate
            // "Family History Library Film" catalog number (a different, unrelated
            // numbering system - confirmed live: "803094" alongside "M653_94" on the same
            // record), which is only used as a fallback when Roll itself isn't parseable.
            const rollValue = fields['Roll'] || "";
            const rollMatch = rollValue.match(/^(.+?)_(\d+)$/);
            const rollNumber = rollMatch ? rollMatch[1] : rollValue;
            const rollFilmNumber = rollMatch ? rollMatch[2] : "";

            return {
                roll: rollNumber,
                film: rollFilmNumber || fields['Family History Library Film'] || "",
                ed: fields['Enumeration District'] || "",
                year: fields['Year'] || "",
                repository: info.repository,
                repositoryLoc: info.repositoryLoc,
                publisher: info.publisher,
                pubLoc: info.pubLoc
            };
        }



        async function extractCurrentPageData() {
            let imageId = getBaseImageId();
            let country = "USA", state = "", county = "", city = "", placeDetails = "", enumerationDistrict = "";

            let dbid = "0";
            const dbMatch = window.location.href.match(/collections\/(\d+)/i) || window.location.href.match(/dbid=(\d+)/i) || window.location.href.match(/view\/\d+:(\d+)/i);
            if (dbMatch) dbid = dbMatch[1];

            if (typeof unsafeWindow !== 'undefined' && unsafeWindow.__PRELOADED_STATE__) {
                const info = unsafeWindow.__PRELOADED_STATE__.viewer?.imageInfo;
                const pathArr = info?.browsePath || [];
                state = pathArr[0] || "";
                county = pathArr[1] || "";
                city = pathArr[2] || "";
                country = getCountryFromState(state);
                const ed = getEnumerationDistrict(pathArr, info?.structureType);
                enumerationDistrict = ed.value;
                placeDetails = pathArr.slice(3).filter((_, i) => (i + 3) !== ed.index).join(" - ") || "";
            }

            const citation = await scrapeSourceCitation();
            const imageIdGuess = parseFilmRollFromImageId(imageId);
            const filmNumber = citation.film || imageIdGuess.film;
            const rollNumber = citation.roll || imageIdGuess.roll;
            if (citation.ed) enumerationDistrict = citation.ed;
            const repository = citation.repository || "";
            const repositoryLoc = citation.repositoryLoc || "";
            const publisher = citation.publisher || "";
            const pubLoc = citation.pubLoc || "";

            if (DEBUG_MODE) {
                console.log(`[MGS DEBUG] page ${batchPageCounter} | url: ${window.location.href} | cached pids: ${typeof unsafeWindow !== 'undefined' ? unsafeWindow.__mgs_pids.length : 'n/a'}`);
            }

            const pageEntry = {
                page_number: batchPageCounter,
                image_id: imageId,
                country: country, state: state, county: county, city: city, place_details: placeDetails,
                enumeration_district: enumerationDistrict, film_number: filmNumber, roll_number: rollNumber,
                apid_db: dbid !== "0" ? dbid : "",
                repository: repository, repository_loc: repositoryLoc, publisher: publisher, pub_loc: pubLoc,
                people: [],
                incomplete: false,
            };

            // API is the only extraction path - DOM-table scraping proved unnecessary in the
            // common case and unreliable as a fallback (see design spec).
            const dbIdForApi = (dbid && dbid !== "0") ? dbid : null;
            let apiSourcedPeople = null;
            if (dbIdForApi && imageId) {
                const apiWait = await waitForAncestryIndexPanelResponse(dbIdForApi, imageId, {timeoutMs: 8000});
                if (!apiWait.timedOut && apiWait.result && Array.isArray(apiWait.result.records) && apiWait.result.records.length > 0) {
                    apiSourcedPeople = ancestryRowsFromIndexPanelResponse(apiWait.result).filter((p) => {
                        if (p.pid && seenPids.has(p.pid)) return false;
                        if (p.pid) seenPids.add(p.pid);
                        return true;
                    });
                    debugLog(`Ancestry index-panel-data API: ${apiSourcedPeople.length} people (${apiWait.elapsedMs}ms).`);
                } else {
                    debugLog(`Ancestry index-panel-data API ${apiWait.timedOut ? 'timed out' : 'returned no records'} after ${apiWait.elapsedMs}ms.`);
                }
            }

            if (apiSourcedPeople) {
                pageEntry.people.push(...apiSourcedPeople);
            } else {
                pageEntry.incomplete = true;
            }

            const thisPlace = {
                state: pageEntry.state, county: pageEntry.county,
                city: pageEntry.city, enumeration_district: pageEntry.enumeration_district,
            };
            if (firstPagePlace === null) {
                firstPagePlace = thisPlace;
            } else if (!placesMatch(thisPlace, firstPagePlace)) {
                return {placeBoundaryCrossed: true, pageEntry: null};
            }

            return {placeBoundaryCrossed: false, pageEntry};
        }

        async function downloadCurrentImage() {
            const startedAt = performance.now();
            return new Promise((resolve) => {
                let highResUrl = "";
                let imgFileName = getBaseImageId() + ".jpg";

                if (typeof unsafeWindow !== 'undefined' && unsafeWindow.__PRELOADED_STATE__) {
                    try {
                        let path = unsafeWindow.__PRELOADED_STATE__.viewer?.imageInfo?.imageDownloadUrl;
                        if (path) {
                            if (path.startsWith('http')) {
                                highResUrl = path;
                            } else {
                                highResUrl = (unsafeWindow.__PRELOADED_STATE__.mainOrigin || "https://www.ancestry.com") + path;
                            }
                        }
                    } catch (err) {
                    }
                }

                if (!highResUrl) {
                    if (window.showToast) window.showToast("Error: Could not find image URL.", "error");
                    resolve();
                    return;
                }

                const finish = () => {
                    if (DEBUG_MODE) {
                        console.log(`[MGS DEBUG] image download for ${imgFileName} finished after ${Math.round(performance.now() - startedAt)}ms`);
                    }
                    resolve();
                };

                GM_xmlhttpRequest({
                    method: "GET",
                    url: highResUrl,
                    responseType: "blob",
                    timeout: 20000,
                    ontimeout: function () {
                        if (window.showToast) window.showToast("Image request timed out, skipping.", 'error');
                        finish();
                    },
                    onload: function (response) {
                        if (response.status === 200) {
                            triggerBlobDownload(response.response, imgFileName, `A_${runId}/Images`);
                            if (window.showToast) window.showToast(`Image captured: ${imgFileName}`, 'success', 1000);
                        } else {
                            if (window.showToast) window.showToast(`Failed to fetch image. Status: ${response.status}`, 'error');
                        }
                        finish();
                    },
                    onerror: function () {
                        if (window.showToast) window.showToast("Network error fetching image.", 'error');
                        finish();
                    }
                });
            });
        }

        function triggerBlobDownload(blob, jsonFileName, subfolder) {
            // Plain <a download> - GM_download was tried and reverted (see CHANGELOG):
            // its permission grant proved unreliable and reset on every script edit.
            // Chrome replaces "/" in a download attribute with "_" instead of creating
            // subfolders, so the subfolder is baked into the filename as a prefix
            // instead (matching what A.py/FS.py scan the Downloads root for).
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.setAttribute("href", url);
            link.setAttribute("download", `TMP_${subfolder.replace(/\//g, '_')}_${jsonFileName}`);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
        }

        function triggerJsonDownload(jsonString, jsonFileName) {
            triggerBlobDownload(new Blob([jsonString], {type: 'application/json;charset=utf-8;'}), jsonFileName, `A_${runId}`);
        }

        function downloadFinalJson() {
            if (accumulatedPages.length === 0) {
                if (window.showToast) window.showToast("No data gathered to download.", "error");
                return;
            }
            const {year, locationStr} = getYearAndLocation();
            const payload = {
                census_year: year, location: locationStr, pages: accumulatedPages,
                incomplete_pages: incompleteItemsSummary(accumulatedPages),
            };
            triggerJsonDownload(JSON.stringify(payload, null, 2), `${year} - ${locationStr} - ANC.json`);
            if (window.showToast) window.showToast("Success! Master JSON Downloaded.", "success", 5000);
        }

        // A page that dies mid-batch (an Ancestry viewer crash, a network drop, the tab being
        // closed) takes every accumulated page with it, since nothing was persisted until the
        // final Stop & Download - discovered the hard way when Ancestry's own viewer crashed
        // ~55 images into a real run and silently lost all of them. Periodically writing a
        // checkpoint means a crash only costs the pages since the last one.
        const CHECKPOINT_INTERVAL_PAGES = 20;

        function downloadCheckpointJson() {
            if (accumulatedPages.length === 0) return;
            const {year, locationStr} = getYearAndLocation();
            const payload = {census_year: year, location: locationStr, pages: accumulatedPages};
            triggerJsonDownload(
                JSON.stringify(payload, null, 2),
                `${year} - ${locationStr} [checkpoint through page ${batchPageCounter}].json`
            );
            if (window.showToast) window.showToast(`Checkpoint saved (page ${batchPageCounter}).`, "success", 1500);
        }

        function isNextBtnDisabled(btn) {
            return !!btn && (btn.disabled || btn.classList.contains('disabled') || btn.getAttribute('aria-disabled') === 'true' || btn.hasAttribute('disabled'));
        }

        async function runExtractionLoop() {
            while (isAutoExtracting) {
                const nextBtnSelector = 'button[aria-label="Next image"], .pagination.right button.page, .nextButton, button[title="Next image"]';
                const nextBtnWait = await waitForCondition(() => document.querySelector(nextBtnSelector), {timeoutMs: 5000});
                const nextBtn = nextBtnWait.result;
                const isNextDisabled = isNextBtnDisabled(nextBtn);

                if (window.showToast) window.showToast(`Transcribing page ${batchPageCounter}...`, "success", 1500);
                const extractResult = await extractCurrentPageData();
                if (extractResult.placeBoundaryCrossed) {
                    debugLog(`Place boundary crossed at page ${batchPageCounter}. Stopping and discarding this page.`);
                    if (window.showToast) window.showToast("New town detected - stopping batch.", "error", 3000);
                    stopBatch();
                    break;
                }
                if (extractResult.pageEntry) {
                    accumulatedPages.push(extractResult.pageEntry);
                    if (extractResult.pageEntry.incomplete) {
                        pagesNeedingRetry.push({
                            page_number: extractResult.pageEntry.page_number,
                            image_id: extractResult.pageEntry.image_id,
                            url: window.location.href,
                        });
                    }
                }

                await downloadCurrentImage();

                if (batchPageCounter % CHECKPOINT_INTERVAL_PAGES === 0) {
                    downloadCheckpointJson();
                }

                let finalNextBtn = nextBtn;
                let finalIsNextDisabled = isNextDisabled;
                if (!finalNextBtn || !document.body.contains(finalNextBtn)) {
                    const recheck = await waitForCondition(() => document.querySelector(nextBtnSelector), {timeoutMs: 5000});
                    finalNextBtn = recheck.result;
                    finalIsNextDisabled = isNextBtnDisabled(finalNextBtn);
                }

                if (finalNextBtn && !finalIsNextDisabled) {
                    const prevUrl = window.location.href;
                    if (window.showToast) window.showToast("Advancing to next page...", "success", 1000);
                    if (typeof unsafeWindow !== 'undefined') {
                        unsafeWindow.__mgs_pids = [];
                    }
                    finalNextBtn.click();
                    const navWait = await waitForCondition(() => window.location.href !== prevUrl, {timeoutMs: 15000});
                    if (navWait.timedOut) {
                        if (window.showToast) window.showToast("Navigation timed out. Stopping.", "error");
                        stopBatch();
                        break;
                    }
                    batchPageCounter++;
                } else {
                    stopBatch();
                    break;
                }
            }
        }
        async function finishBatch() {
            if (pagesNeedingRetry.length > 0) {
                await runAncestryRetryPass();
                return;
            }
            const incomplete = incompleteItemsSummary(accumulatedPages);
            if (incomplete.length > 0) {
                const pageList = incomplete.map((p) => p.page_number).join(', ');
                if (window.showToast) {
                    window.showToast(`Incomplete pages (no index data received): ${pageList}`, 'error', 3600000);
                }
            }
            clearReloadState();
            downloadFinalJson();
        }

        async function runAncestryRetryPass() {
            const target = pagesNeedingRetry.shift();
            saveReloadState({
                accumulatedPages, batchPageCounter, seenPids, firstPagePlace,
                pagesNeedingRetry, retryPhase: true, currentRetryTarget: target,
            });
            window.location.href = buildRetryNavigationUrl(target.url, runId);
        }

        async function retryCurrentPageThenContinue() {
            batchPageCounter = currentRetryTarget.page_number;
            const extractResult = await extractCurrentPageData();
            if (!extractResult.placeBoundaryCrossed && extractResult.pageEntry && extractResult.pageEntry.people.length > 0) {
                const idx = accumulatedPages.findIndex((p) => p.page_number === currentRetryTarget.page_number);
                if (idx !== -1) {
                    accumulatedPages[idx] = extractResult.pageEntry;
                    accumulatedPages[idx].incomplete = false;
                }
            }
            await downloadCurrentImage();
            currentRetryTarget = null;
            await finishBatch();
        }


        function startBatch() {
            isAutoExtracting = true;
            if (!resumingFromReload) {
                accumulatedPages = [];
                seenPids.clear();
                batchPageCounter = 1;
                firstPagePlace = null;
                pagesNeedingRetry = [];
            }
            resumingFromReload = false;

            if (window._startBtn) window._startBtn.style.display = 'none';
            if (window._stopBtn) window._stopBtn.style.display = 'block';
            if (window._statusLight) window._statusLight.classList.add('running');
            if (window.showToast) window.showToast("Starting Batch Extraction...", "success");

            if (inRetryPhase) {
                inRetryPhase = false;
                retryCurrentPageThenContinue().catch((err) => {
                    console.error('[MGS] Retry pass crashed:', err);
                    downloadFinalJson();
                });
                return;
            }

            runExtractionLoop().catch((err) => {
                console.error('[MGS] Extraction loop crashed:', err);
                if (window.showToast) window.showToast(`Extraction stopped: ${err.message || err}`, 'error', 4000);
                stopBatch();
            });
        }

        function stopBatch() {
            if (!isAutoExtracting) return;
            isAutoExtracting = false;
            if (window._startBtn) window._startBtn.style.display = 'block';
            if (window._stopBtn) window._stopBtn.style.display = 'none';
            if (window._statusLight) window._statusLight.classList.remove('running');
            debugLog("Batch stopped.");
            finishBatch().catch((err) => console.error('[MGS] finishBatch crashed:', err));
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                initUI();
                if (shouldAutoStart && !isAutoExtracting) startBatch();
            });
        } else {
            initUI();
            if (shouldAutoStart && !isAutoExtracting) startBatch();
        }
    }

    // ==========================================
    // FAMILYSEARCH (FS) - church-register index + citation gather
    // ==========================================
    function runFamilySearchGather() {
        // GUARD: only run when explicitly triggered, same convention as Ancestry above.
        if (sessionStorage.getItem('run_fs_gather') !== 'true' && !window.location.href.includes('mgs_auto=1')) {
            return;
        }
        sessionStorage.removeItem('run_fs_gather');

        const DEBUG_MODE = true;
        let isRunning = false;
        let accumulatedItems = [];
        let seenItemIds = new Set();
        let itemsAtLastCheckpoint = 0;
        // Collection-level (not per-image) - FamilySearch's own "Information" tab is the
        // only place that exposes the real numeric collection id (see
        // scrapeFsCollectionInfo() below); scraped once per run and persisted through every
        // "Next Image" reload the same way accumulatedItems/seenItemIds already are, since
        // every image in one gather belongs to the same one collection.
        let fsCollectionId = '';
        let fsCollectionName = '';

        const runId = new URLSearchParams(window.location.search).get('mgs_run') || 'norun';
        // isFsRunStopped() check: see FS_STOPPED_RUNS_KEY's own note - covers the narrower
        // race stopBatch()'s URL-strip alone can't (a navigation already in flight when Stop
        // was clicked, landing on a page whose URL was already committed with mgs_auto=1
        // still baked in).
        const shouldAutoStart = window.location.href.includes('mgs_auto=1') && !isFsRunStopped(runId);

        // A genuine FamilySearch page navigation from clicking "Next Image" (see
        // goToNextImage/FS_RELOAD_STATE_KEY's own note) re-runs this whole script from
        // scratch - restore whatever was saved right before so the batch resumes instead of
        // silently discarding every item gathered so far. Same convention as
        // runAncestryGather's own resumedState handling. isResumingFsState is read by
        // startBatch() below to skip its own unconditional reset, the same way
        // runAncestryGather's resumingFromReload guards startBatch() there.
        let pagesNeedingRetry = [];
        let inRetryPhase = false;
        let currentRetryTarget = null;

        let isResumingFsState = false;
        const resumedFsState = loadFsReloadState(runId);
        if (resumedFsState) {
            accumulatedItems = resumedFsState.accumulatedItems;
            seenItemIds = resumedFsState.seenItemIds;
            itemsAtLastCheckpoint = resumedFsState.itemsAtLastCheckpoint;
            pagesNeedingRetry = resumedFsState.pagesNeedingRetry;
            inRetryPhase = resumedFsState.retryPhase;
            currentRetryTarget = resumedFsState.currentRetryTarget;
            fsCollectionId = resumedFsState.collectionId;
            fsCollectionName = resumedFsState.collectionName;
            isResumingFsState = true;
            clearFsReloadState();
        }

        function debugLog(msg) {
            if (DEBUG_MODE) console.log(`[Voyageur FS] ${msg}`);
        }

        // Confirmed live: FamilySearch's own client requests this endpoint automatically on
        // every image's page load, no UI interaction required - installed here, as early as
        // possible inside this function (before any async work), mirroring
        // runAncestryGather's own __mgs_intercepted pattern (same fetch/XHR patch technique,
        // different target URL/response shape). __voyageurFsApiResponses is keyed by ark so
        // multiple in-flight requests (if FamilySearch ever prefetches adjacent images) don't
        // collide with each other.
        if (typeof unsafeWindow !== 'undefined' && !unsafeWindow.__voyageurFsApiIntercepted) {
            unsafeWindow.__voyageurFsApiIntercepted = true;
            unsafeWindow.__voyageurFsApiResponses = {};
            // Per-ark resolver callbacks for waitForFsApiResponse() below. Storing a response
            // sets a plain object property, not a DOM node - waitForCondition()'s
            // MutationObserver would never see it, so waiters are notified directly here
            // instead of relying on that helper's DOM-mutation-driven re-checks.
            unsafeWindow.__voyageurFsApiWaiters = {};
            // True Family Tree person id (entityId) per record-scoped ark, keyed exactly
            // like record_ark ("1:1:XXXX-XXX") - populated from
            // /service/tree/links/sources/attachments below. Confirmed live 2026-08-21: this
            // endpoint fires automatically once the Names panel loads (a batch covering
            // every person on the page/household, not scoped to one persona and not
            // requiring any click) with response shape
            // {attachedSourcesMap: {"<full ark URL>": [{persons: [{entityId: "<tree id, e.g.
            // KLBM-H9P>", ...}], sourceId: "..."}]}} - entityId is the id shown in the UI's
            // "Attached in Tree to ... <id>" text, absent from the orchestration API
            // response itself (see docs/plans/2026-08-20-familysearch-viewer-rebuild.md
            // Task 4). Not every persona has an entry here - most historical personas were
            // never attached to a Tree profile by anyone; absence is the common case, not
            // an error.
            unsafeWindow.__voyageurFsAttachments = {};
            // Confirmed live 2026-08-21 (console capture across a real multi-image gather):
            // this endpoint's response routinely arrives AFTER the orchestration API's own
            // response for the same image - fsBuildRowsFromApiResponse() runs as soon as the
            // (usually faster) orchestration response lands and calls
            // fsPersonArkFromAttachments() synchronously right then, so it was reading this
            // map before the correct entityId had even been stored - a real, confirmed
            // person_ark (e.g. Jess G Crowston's KLBM-H9P) was found empty at row-build time,
            // then successfully stored a moment later, too late to help that row. These two
            // fields let buildFsItemData() await this response (see waitForFsAttachments
            // below) before building rows, not just fire in whatever order the two network
            // calls happen to land in. __voyageurFsAttachmentsRequested is set the moment the
            // outgoing request is observed (not when it resolves) - condition-based, not a
            // fixed delay: a page where this endpoint is never called at all (most historical
            // personas were never attached) skips waiting entirely instead of sitting through
            // an arbitrary timeout for a response that was never coming.
            unsafeWindow.__voyageurFsAttachmentsRequested = false;
            unsafeWindow.__voyageurFsAttachmentsArrived = false;
            unsafeWindow.__voyageurFsAttachmentsWaiters = [];

            const FS_API_TARGET = '/service/records/volunteer/orchestration/sls/image/';
            const FS_ATTACHMENTS_TARGET = '/service/tree/links/sources/attachments';

            function fsApiArkFromUrl(url) {
                const match = url.match(/\/image\/([^/?#]+)/);
                return match ? decodeURIComponent(match[1]) : null;
            }

            function storeFsApiResponse(url, bodyText) {
                const ark = fsApiArkFromUrl(url);
                if (!ark) return;
                try {
                    const parsed = JSON.parse(bodyText);
                    unsafeWindow.__voyageurFsApiResponses[ark] = parsed;
                    const waiter = unsafeWindow.__voyageurFsApiWaiters[ark];
                    if (waiter) waiter(parsed);
                } catch (e) {
                    // Leave unset - waitForFsApiResponse() below times out the same as "never
                    // arrived", which is the correct behavior for an unparseable response.
                }
            }

            function storeFsAttachmentsResponse(bodyText) {
                try {
                    const parsed = JSON.parse(bodyText);
                    const map = parsed.attachedSourcesMap || {};
                    const rawKeys = Object.keys(map);
                    // TEMPORARY diagnostic (2026-08-21): person_ark keeps coming back empty/
                    // wrong for a person confirmed live to have a real Tree attachment
                    // (Jess G Crowston / KLBM-H9P) - logging the raw response shape here.
                    console.log(`[Voyageur FS ARK] attachments response: ${rawKeys.length} source(s)`, rawKeys);
                    for (const arkUri of rawKeys) {
                        const entry = map[arkUri][0];
                        const person = entry && entry.persons && entry.persons[0];
                        if (person && person.entityId) {
                            // Store the full DECODED uri as
                            // the key, not a regex-extracted ark - fsPersonArkFromAttachments()
                            // now matches by substring against this, so it no longer matters
                            // whether the uri carries anything beyond the bare persona ark
                            // (trailing path segments, etc.) that an exact-key lookup would
                            // have missed.
                            const decodedUri = decodeURIComponent(arkUri);
                            unsafeWindow.__voyageurFsAttachments[decodedUri] = person.entityId;
                            console.log(`[Voyageur FS ARK] stored ${decodedUri} -> ${person.entityId}`);
                        } else {
                            console.log(`[Voyageur FS ARK] no entityId for uri ${arkUri}`, entry);
                        }
                    }
                } catch (e) {
                    // Leave unset - a true person_ark simply won't be found for any ark in
                    // this batch, matching the "don't fabricate" convention everywhere else.
                    console.log('[Voyageur FS ARK] failed to parse attachments response:', e);
                } finally {
                    // Resolve even on a parse failure - "a response arrived (however
                    // unusable)" is still the correct signal to stop waitForFsAttachments()
                    // from blocking on a reply that already came and went.
                    unsafeWindow.__voyageurFsAttachmentsArrived = true;
                    const waiters = unsafeWindow.__voyageurFsAttachmentsWaiters.splice(0);
                    waiters.forEach((resolve) => resolve());
                }
            }

            const origFsXhrOpen = unsafeWindow.XMLHttpRequest.prototype.open;
            unsafeWindow.XMLHttpRequest.prototype.open = function (method, url) {
                this.__voyageurFsApiUrl = url;
                if (url && url.includes(FS_ATTACHMENTS_TARGET)) {
                    unsafeWindow.__voyageurFsAttachmentsRequested = true;
                }
                this.addEventListener('load', function () {
                    if (this.__voyageurFsApiUrl && this.__voyageurFsApiUrl.includes(FS_API_TARGET)) {
                        storeFsApiResponse(this.__voyageurFsApiUrl, this.responseText);
                    } else if (this.__voyageurFsApiUrl && this.__voyageurFsApiUrl.includes(FS_ATTACHMENTS_TARGET)) {
                        storeFsAttachmentsResponse(this.responseText);
                    }
                });
                return origFsXhrOpen.apply(this, arguments);
            };

            const origFsFetch = unsafeWindow.fetch;
            unsafeWindow.fetch = async function (...args) {
                const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
                if (url.includes(FS_ATTACHMENTS_TARGET)) {
                    unsafeWindow.__voyageurFsAttachmentsRequested = true;
                }
                const resp = await origFsFetch.apply(this, args);
                if (url.includes(FS_API_TARGET)) {
                    resp.clone().text().then((t) => storeFsApiResponse(url, t));
                } else if (url.includes(FS_ATTACHMENTS_TARGET)) {
                    resp.clone().text().then((t) => storeFsAttachmentsResponse(t));
                }
                return resp;
            };
        }

        // Lets buildFsItemData() await this endpoint's response before building rows - see
        // the note on __voyageurFsAttachmentsArrived above for why this matters. Condition-
        // based, not a fixed delay: gated on whether the request was actually observed at
        // all (most historical personas were never attached, so most pages never call this
        // endpoint), not on a blind timeout. requestGraceMs only covers the narrow window
        // where the orchestration API response (which this function is awaited right after)
        // and the attachments request can be dispatched within the same tick - real
        // uncertainty about dispatch ORDER, not about whether the response has arrived yet
        // (that part is resolved eagerly the instant it lands, via the waiters array, not
        // polled). timeoutMs is a last-resort ceiling for a request that was genuinely
        // observed but never got a response (network failure, backgrounded tab) - the same
        // accepted pattern waitForFsApiResponse already uses for its own network wait.
        async function waitForFsAttachments({timeoutMs = 3000, requestGraceMs = 300} = {}) {
            if (unsafeWindow.__voyageurFsAttachmentsArrived) {
                console.log('[Voyageur FS ARK] waitForFsAttachments: already arrived, not waiting.');
                return;
            }

            if (!unsafeWindow.__voyageurFsAttachmentsRequested) {
                await new Promise((resolve) => setTimeout(resolve, requestGraceMs));
                if (!unsafeWindow.__voyageurFsAttachmentsRequested) {
                    console.log(`[Voyageur FS ARK] waitForFsAttachments: no request observed after `
                        + `${requestGraceMs}ms grace - not waiting further.`);
                    return;
                }
            }

            if (unsafeWindow.__voyageurFsAttachmentsArrived) {
                console.log('[Voyageur FS ARK] waitForFsAttachments: response arrived during grace window.');
                return;
            }

            console.log('[Voyageur FS ARK] waitForFsAttachments: request observed, waiting for response...');
            const startedAt = performance.now();
            let timedOut = true;
            await new Promise((resolve) => {
                const timer = setTimeout(resolve, timeoutMs);
                unsafeWindow.__voyageurFsAttachmentsWaiters.push(() => {
                    clearTimeout(timer);
                    timedOut = false;
                    resolve();
                });
            });
            const elapsedMs = Math.round(performance.now() - startedAt);
            if (timedOut) {
                console.log(`[Voyageur FS ARK] waitForFsAttachments: TIMED OUT after ${elapsedMs}ms - `
                    + `proceeding without it.`);
            } else {
                console.log(`[Voyageur FS ARK] waitForFsAttachments: response arrived after ${elapsedMs}ms.`);
            }
        }

        // Instant resolution if the response already arrived before this was called (the API
        // call fires on page load, which can beat the gather loop reaching this image).
        // Deliberately does NOT go through waitForCondition() - that helper only re-checks its
        // condition on MutationObserver-observed DOM mutations, and storeFsApiResponse() above
        // sets a plain object property that never touches the DOM, so it would never reliably
        // trigger a re-check. Instead, storeFsApiResponse() resolves the matching waiter
        // directly by ark; this only falls back to a timer if the response never arrives.
        async function waitForFsApiResponse(ark, {timeoutMs = 15000} = {}) {
            const startedAt = performance.now();
            const existing = (unsafeWindow.__voyageurFsApiResponses || {})[ark];
            if (existing) {
                return {result: existing, elapsedMs: 0, timedOut: false};
            }
            return new Promise((resolve) => {
                let settled = false;
                const timer = setTimeout(() => {
                    if (settled) return;
                    settled = true;
                    delete unsafeWindow.__voyageurFsApiWaiters[ark];
                    resolve({result: null, elapsedMs: Math.round(performance.now() - startedAt), timedOut: true});
                }, timeoutMs);
                unsafeWindow.__voyageurFsApiWaiters[ark] = (result) => {
                    if (settled) return;
                    settled = true;
                    clearTimeout(timer);
                    delete unsafeWindow.__voyageurFsApiWaiters[ark];
                    resolve({result, elapsedMs: Math.round(performance.now() - startedAt), timedOut: false});
                };
            });
        }

        // Same technique as the orchestration-API interceptor just above, targeting
        // filmdatainfo/image-data instead - confirmed live this endpoint's request URL is
        // identical for every image ("/search/filmdatainfo/image-data", no per-image query
        // param), so responses can't be keyed by URL the way the orchestration API's can.
        // Keyed by the response BODY's own arkId field instead.
        if (typeof unsafeWindow !== 'undefined' && !unsafeWindow.__voyageurFsImageIndexIntercepted) {
            unsafeWindow.__voyageurFsImageIndexIntercepted = true;
            unsafeWindow.__voyageurFsImageIndexResponses = {};
            unsafeWindow.__voyageurFsImageIndexWaiters = {};

            const FS_IMAGE_INDEX_TARGET = '/search/filmdatainfo/image-data';

            function storeFsImageIndexResponse(bodyText) {
                let parsed;
                try {
                    parsed = JSON.parse(bodyText);
                } catch (e) {
                    // Leave unset - waitForFsImageIndexResponse() below times out the same as
                    // "never arrived", the correct behavior for an unparseable response.
                    return;
                }
                const ark = parsed && parsed.arkId;
                if (!ark) return;
                unsafeWindow.__voyageurFsImageIndexResponses[ark] = parsed;
                const waiter = unsafeWindow.__voyageurFsImageIndexWaiters[ark];
                if (waiter) waiter(parsed);
            }

            const origImageIndexXhrOpen = unsafeWindow.XMLHttpRequest.prototype.open;
            unsafeWindow.XMLHttpRequest.prototype.open = function (method, url) {
                this.__voyageurFsImageIndexUrl = url;
                this.addEventListener('load', function () {
                    if (this.__voyageurFsImageIndexUrl && this.__voyageurFsImageIndexUrl.includes(FS_IMAGE_INDEX_TARGET)) {
                        storeFsImageIndexResponse(this.responseText);
                    }
                });
                return origImageIndexXhrOpen.apply(this, arguments);
            };

            const origImageIndexFetch = unsafeWindow.fetch;
            unsafeWindow.fetch = async function (...args) {
                const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
                const resp = await origImageIndexFetch.apply(this, args);
                if (url.includes(FS_IMAGE_INDEX_TARGET)) {
                    resp.clone().text().then((t) => storeFsImageIndexResponse(t));
                }
                return resp;
            };
        }

        // Same event-driven-with-timeout-fallback shape as waitForFsApiResponse above.
        async function waitForFsImageIndexResponse(ark, {timeoutMs = 15000} = {}) {
            const startedAt = performance.now();
            const existing = (unsafeWindow.__voyageurFsImageIndexResponses || {})[ark];
            if (existing) {
                return {result: existing, elapsedMs: 0, timedOut: false};
            }
            return new Promise((resolve) => {
                let settled = false;
                const timer = setTimeout(() => {
                    if (settled) return;
                    settled = true;
                    delete unsafeWindow.__voyageurFsImageIndexWaiters[ark];
                    resolve({result: null, elapsedMs: Math.round(performance.now() - startedAt), timedOut: true});
                }, timeoutMs);
                unsafeWindow.__voyageurFsImageIndexWaiters[ark] = (result) => {
                    if (settled) return;
                    settled = true;
                    clearTimeout(timer);
                    delete unsafeWindow.__voyageurFsImageIndexWaiters[ark];
                    resolve({result, elapsedMs: Math.round(performance.now() - startedAt), timedOut: false});
                };
            });
        }

        // ==========================================
        // UI (same visual conventions as Ancestry's, separate element ids so both can
        // theoretically be open in different tabs without colliding)
        // ==========================================
        function initUI() {
            if (document.getElementById('fs-ui-container')) return;

            const style = document.createElement('style');
            style.innerHTML = `
                #fs-ui-container { position: fixed; bottom: 20px; right: 20px; background-color: #1a1a1a; color: white; border-radius: 8px; padding: 12px 16px; font-family: sans-serif; z-index: 999999; box-shadow: 0 4px 12px rgba(0,0,0,0.5); display: flex; flex-direction: column; gap: 10px; align-items: center; border: 1px solid #333; min-width: 180px; }
                #fs-header { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; padding-bottom: 4px; border-bottom: 1px solid #333; }
                #fs-title { font-weight: bold; font-size: 15px; }
                #fs-status-light { width: 12px; height: 12px; border-radius: 50%; background-color: #22c55e; box-shadow: 0 0 8px #22c55e; transition: all 0.3s ease; }
                #fs-status-light.running { background-color: #3b82f6; box-shadow: 0 0 10px #3b82f6; animation: fs-pulse 1.5s infinite; }
                @keyframes fs-pulse { 0% { transform: scale(0.95); opacity: 0.8; } 50% { transform: scale(1.1); opacity: 1; } 100% { transform: scale(0.95); opacity: 0.8; } }
                .fs-btn { color: white; border: none; border-radius: 6px; padding: 10px 14px; font-size: 14px; font-weight: bold; cursor: pointer; width: 100%; transition: background-color 0.2s; }
                #fs-start-btn { background-color: #2b7a4b; } #fs-start-btn:hover { background-color: #1e5935; }
                #fs-stop-btn { background-color: #991b1b; display: none; }
                #fs-toast-container { position: fixed; bottom: 140px; right: 20px; z-index: 999999; display: flex; flex-direction: column; gap: 10px; pointer-events: none; }
                .fs-toast { background-color: #333; color: #fff; padding: 12px 20px; border-radius: 6px; font-size: 14px; opacity: 0; transform: translateY(10px); transition: opacity 0.3s, transform 0.3s; }
                .fs-toast.show { opacity: 1; transform: translateY(0); }
                .fs-toast.error { background-color: #b91c1c; } .fs-toast.success { background-color: #15803d; }
            `;
            document.head.appendChild(style);

            const toastContainer = document.createElement('div');
            toastContainer.id = 'fs-toast-container';
            document.body.appendChild(toastContainer);

            window.fsShowToast = function (message, type = 'success', duration = 2500) {
                const toast = document.createElement('div');
                toast.className = `fs-toast ${type}`;
                toast.innerText = message;
                toastContainer.appendChild(toast);
                void toast.offsetWidth;
                toast.classList.add('show');
                setTimeout(() => {
                    toast.classList.remove('show');
                    setTimeout(() => toast.remove(), 300);
                }, duration);
            };

            const panel = document.createElement('div');
            panel.id = 'fs-ui-container';

            const header = document.createElement('div');
            header.id = 'fs-header';
            const statusLight = document.createElement('div');
            statusLight.id = 'fs-status-light';
            const title = document.createElement('span');
            title.id = 'fs-title';
            title.innerText = 'Voyageur (FS)';
            header.appendChild(statusLight);
            header.appendChild(title);
            panel.appendChild(header);

            const startBtn = document.createElement('button');
            startBtn.id = 'fs-start-btn';
            startBtn.className = 'fs-btn';
            startBtn.innerText = 'Start Auto-Batch';

            const stopBtn = document.createElement('button');
            stopBtn.id = 'fs-stop-btn';
            stopBtn.className = 'fs-btn';
            stopBtn.innerText = 'Stop & Download JSON';

            panel.appendChild(startBtn);
            panel.appendChild(stopBtn);
            document.body.appendChild(panel);

            startBtn.addEventListener('click', startBatch);
            stopBtn.addEventListener('click', stopBatch);

            window._fsStartBtn = startBtn;
            window._fsStopBtn = stopBtn;
            window._fsStatusLight = statusLight;
        }

        // ==========================================
        // DOM HELPERS
        // ==========================================
        function getItemId() {
            const match = window.location.pathname.match(/ark:\/61903\/([^/?#]+)/);
            return match ? decodeURIComponent(match[1]) : "";
        }

        // ==========================================
        // SCRAPING
        // ==========================================
        // Runs once per image, before either data source is awaited. Names/Image Index tab
        // presence is the ground truth of which page actually rendered - confirmed live this
        // is a navigation-method split (Search -> Names panel, Image Browser -> Image Index),
        // not something inferable from the URL. Event-driven via the existing
        // waitForCondition convention.
        async function detectFsPageType({timeoutMs = 15000} = {}) {
            const wait = await waitForCondition(() => {
                if (findByAriaLabel('[role="tab"], button, a', 'Names')
                    || findByExactText('[role="tab"], button, a', 'Names')) return 'names';
                if (findByAriaLabel('[role="tab"], button, a', 'Image Index')
                    || findByExactText('[role="tab"], button, a', 'Image Index')) return 'image-index';
                return null;
            }, {timeoutMs});
            return wait.result;
        }

        // FamilySearch's own "Information" tab (confirmed live 2026-08-21) is the only place
        // that exposes the real numeric FamilySearch collection id - citation_text never
        // carries a `cc=`-style query param under this architecture (Voyageur.js's own
        // navigate URL, not FamilySearch's catalog browse URL, is what ends up in the
        // citation), and the records/images/orchestration GraphQL API's `artifact`/
        // `getArtifactParent`/`group` queries all require an internal id format that
        // couldn't be reverse-engineered from the image ark. The Information panel's
        // "Historical Record Collection" link (as opposed to the "Catalog Collection" link
        // right above it) reliably points to /en/search/collection/<id> - unambiguous to
        // select via the href pattern alone, no text-matching needed. Called once per run
        // (gated by fsCollectionId being falsy) since every image in one gather belongs to
        // the same collection - switches to Information, scrapes, then switches back to
        // whichever tab (Names/Image Index) this page actually started on so the rest of
        // buildFsItemData's existing per-image flow is unaffected.
        async function scrapeFsCollectionInfo(pageType) {
            const infoTab = findByAriaLabel('[role="tab"], button, a', 'Information')
                || findByExactText('[role="tab"], button, a', 'Information');
            if (!infoTab) {
                debugLog('Information tab not found - collection_id will stay blank.');
                return {collectionId: '', collectionName: ''};
            }
            infoTab.click();

            const linkWait = await waitForCondition(
                () => document.querySelector('a[href*="/search/collection/"]'),
                {timeoutMs: 10000}
            );
            let collectionId = '', collectionName = '';
            if (linkWait.result) {
                const href = linkWait.result.getAttribute('href') || '';
                const idMatch = href.match(/\/search\/collection\/(\d+)/);
                if (idMatch) collectionId = idMatch[1];
                collectionName = linkWait.result.textContent.trim();
            } else {
                debugLog(`Information panel collection link never appeared (${linkWait.elapsedMs}ms) - collection_id will stay blank.`);
            }

            const backLabel = pageType === 'image-index' ? 'Image Index' : 'Names';
            const backTab = findByAriaLabel('[role="tab"], button, a', backLabel)
                || findByExactText('[role="tab"], button, a', backLabel);
            if (backTab) {
                backTab.click();
                await waitForCondition(() => !document.querySelector('a[href*="/search/collection/"]'), {timeoutMs: 5000});
            }

            return {collectionId, collectionName};
        }

        function parseFsLocationFromBrowsePath(browsePathArray) {
            let state = "", county = "", city = "", ed = "";
            let segments = browsePathArray.filter(s => !/^image\s+\d+\s+of\s+\d+$/i.test(s));
            let edIndex = segments.findIndex(s => /\bED\b|enumeration district/i.test(s));
            if (edIndex !== -1) {
                ed = segments[edIndex];
                segments.splice(edIndex, 1);
            }
            if (segments.length > 0) state = segments[0];
            if (segments.length > 1) county = segments[1];
            if (segments.length > 2) city = segments[2];
            return { state, county, city, enumeration_district: ed };
        }

        async function buildFsItemData(itemId) {
            const pageType = await detectFsPageType();
            let rows = [];
            let citationText = '';
            let catalogItems = [];
            let incomplete = false;
            let locationInfo = { state: "", county: "", city: "", enumeration_district: "" };

            if (pageType === 'image-index') {
                const apiWait = await waitForFsImageIndexResponse(itemId);
                if (apiWait.result) {
                    rows = fsBuildRowsFromImageIndexResponse(apiWait.result);
                    const imageMatch = document.body.innerText.match(/Image\s+(\d+)\s+of\s+(\d+)/i);
                    citationText = fsBuildCitationTextFromImageIndexResponse(apiWait.result, {
                        imageNumber: imageMatch ? imageMatch[1] : undefined,
                        imageTotal: imageMatch ? imageMatch[2] : undefined,
                    });
                    
                    const record = ((apiWait.result && apiWait.result.records) || [])[0];
                    const headPerson = record ? (record.persons || [])[0] : null;
                    const censusFact = headPerson ? fsImageIndexFindByType(headPerson.facts || [], 'http://gedcomx.org/Census') : null;
                    const browsePath = fsImageIndexBrowsePathSegments(censusFact);
                    locationInfo = parseFsLocationFromBrowsePath(browsePath);
                } else {
                    incomplete = true;
                    debugLog(`No Image-Index response arrived for item ${itemId} after ${apiWait.elapsedMs}ms.`);
                    if (window.fsShowToast) window.fsShowToast('No index data received for this image.', 'error', 4000);
                }
                // No DOM scraping needed for image-index pages (fixes missing-panel errors).
            } else if (pageType === 'names') {
                const apiWait = await waitForFsApiResponse(itemId);
                if (apiWait.result) {
                    // Confirmed live 2026-08-21: "Next Image" turned out to be a client-side
                    // route change, NOT a full page reload as this file's own comments assumed
                    // elsewhere (confirmed by injecting a probe script that survived the
                    // navigation) - unsafeWindow, and everything on it, persists across every
                    // image in one gather, not just within one. Without this reset,
                    // __voyageurFsAttachmentsArrived - and __voyageurFsAttachmentsRequested -
                    // go true on the FIRST image's attachments response and stay true forever,
                    // so waitForFsAttachments() on every later image short-circuits on a STALE
                    // flag instead of waiting for THIS image's own batch - confirmed live as
                    // the real cause of Jess G Crowston's real KLBM-H9P mapping (present and
                    // correctly keyed in the raw response, per a direct capture) never reaching
                    // his row. __voyageurFsAttachments itself (the map of already-seen
                    // mappings) is deliberately NOT cleared here - those entries stay correct
                    // and harmless to keep across images, only the per-image "have we heard
                    // back yet" flags need to start fresh.
                    if (typeof unsafeWindow !== 'undefined') {
                        unsafeWindow.__voyageurFsAttachmentsArrived = false;
                        unsafeWindow.__voyageurFsAttachmentsRequested = false;
                    }
                    await waitForFsAttachments();
                    rows = fsBuildRowsFromApiResponse(apiWait.result);
                    // The attachments endpoint can fire again after this point with a LATER
                    // batch covering people the first response didn't - see
                    // backfillFsPersonArks's own note.
                    await backfillFsPersonArks(rows);
                    const byId = buildFsElementIndex(apiWait.result);
                    const primaryPerson = (apiWait.result.elements || []).find((e) => e.elementType === 'PERSON' && e.primary);
                    const imageMatch = document.body.innerText.match(/Image\s+(\d+)\s+of\s+(\d+)/i);
                    citationText = fsBuildCitationTextFromApiResponse(apiWait.result, byId, primaryPerson, {
                        collectionName: document.title,
                        url: window.location.href,
                        imageNumber: imageMatch ? imageMatch[1] : undefined,
                        imageTotal: imageMatch ? imageMatch[2] : undefined,
                    });
                    locationInfo = fsImageLevelLocation(apiWait.result);
                } else {
                    incomplete = true;
                    debugLog(`No orchestration-API response arrived for item ${itemId} after ${apiWait.elapsedMs}ms.`);
                    if (window.fsShowToast) window.fsShowToast('No index data received for this image.', 'error', 4000);
                }
                // No DOM scraping needed for the Names panel either now, matching the
                // Image-Index path above. catalogItems stays [] - roll_number still resolves
                // downstream via nara_info['publication'] parsed from citationText's own NARA
                // clause (FS.py's build_census_json), same fallback the Image-Index path
                // already relies on.
            } else {
                incomplete = true;
                debugLog(`Neither Names nor Image Index tab found for item ${itemId} - unrecognized page shape.`);
                if (window.fsShowToast) window.fsShowToast('Unrecognized page.', 'error', 4000);
            }

            return {
                item_id: itemId,
                citation_text: citationText,
                catalog_items: catalogItems,
                rows,
                incomplete,
                country: getCountryFromState(locationInfo.state),
                state: locationInfo.state,
                county: locationInfo.county,
                city: locationInfo.city,
                enumeration_district: locationInfo.enumeration_district,
                collection_id: fsCollectionId,
                collection_name: fsCollectionName
            };
        }

        // Returns {progressed} rather than silently returning on an already-seen/missing
        // itemId - a bounded browse set (e.g. one enumeration district's own image count)
        // doesn't always give goToNextImage() a genuine dead end (a disabled/absent "Next"
        // button): FamilySearch's own navigation can instead wrap back around to an image
        // already gathered, or land somewhere with no parseable ark at all. Silently
        // returning here (the prior behavior) gave runLoop() no way to distinguish that from
        // real forward progress, so it kept calling goToNextImage() and cycling through the
        // same already-seen images forever instead of ever reaching stopBatch() - confirmed
        // live: a completed 14-image gather kept "flashing" between images indefinitely
        // instead of downloading its final JSON.
        async function scrapeCurrentImage() {
            const itemId = getItemId();
            if (!itemId) return {progressed: false, reason: 'no-item-id'};
            if (seenItemIds.has(itemId)) return {progressed: false, reason: 'already-seen'};

            const itemData = await buildFsItemData(itemId);
            await downloadFsImage(itemId);

            seenItemIds.add(itemId);
            accumulatedItems.push(itemData);
            if (itemData.incomplete) {
                pagesNeedingRetry.push({item_id: itemId, url: window.location.href});
            }

            debugLog(`Scraped item ${itemId}: ${itemData.rows.length} index rows.`);
            return {progressed: true};
        }

        // FamilySearch's account-level "explore" record view (reached, confirmed live, via
        // some FamilySearch navigation paths) drops the dedicated Next Image button entirely
        // - without this fallback, the batch silently stopped after exactly one image
        // whenever that view was reached.
        //
        // The "Enter Image number" input looked like an equivalent second control but is
        // NOT real navigation - confirmed live, extensively: typing a new value and
        // submitting it (even via a genuine trusted OS-level keypress, not just a synthetic
        // dispatch()) updates the displayed number cosmetically while
        // window.location.pathname never changes. scrapeCurrentImage() would then silently
        // skip every subsequent image forever, since getItemId() parses the ark from that
        // same unchanged pathname and seenItemIds already has it.
        //
        // The filmstrip's own "Go to image N" thumbnail buttons are genuine navigation -
        // confirmed live that clicking one changes the ark (e.g. "...9YBZ-XVG" ->
        // "...9YBZ-F86" on the same record). In the classic "index" view, the
        // currently-viewed image's own neighbor is reliably already rendered in the
        // (virtualized) filmstrip. In "explore" mode specifically, the filmstrip is often
        // empty outright and this fallback can't reach it - see the KNOWN LIMITATION note
        // inside advanceViaFilmstripThumbnail() below.
        async function advanceViaFilmstripThumbnail() {
            // KNOWN LIMITATION (not fully solved): in the "explore" view specifically, this
            // filmstrip is a virtualized list that's frequently empty and never renders any
            // "Go to image N" buttons at all - confirmed live across repeated attempts. A
            // window resize event is a plausible nudge for this general class of
            // virtualized-list bug (many such lists use a ResizeObserver), but it did NOT
            // reliably fix this specific case in live testing - neither did toggling
            // FamilySearch's own "View Grid" control. Left in as a cheap, harmless attempt
            // rather than removed, since it's free and may help on some page states even
            // though it isn't a confirmed fix. When this path exhausts its wait, the batch
            // stops after the current image exactly like it did before this whole fallback
            // existed - no worse than the prior behavior, just not the full fix. See
            // GitHub issue tracking FamilySearch "explore" view multi-page continuation for
            // follow-up.
            let currentWait = await waitForCondition(() => [...document.querySelectorAll('button')]
                .find(b => /^Go to image \d+, viewed$/.test(b.getAttribute('aria-label') || '')) || null,
                {timeoutMs: 3000});
            if (!currentWait.result) {
                window.dispatchEvent(new Event('resize'));
                currentWait = await waitForCondition(() => [...document.querySelectorAll('button')]
                    .find(b => /^Go to image \d+, viewed$/.test(b.getAttribute('aria-label') || '')) || null,
                    {timeoutMs: 10000});
            }
            const currentBtn = currentWait.result;
            if (!currentBtn) {
                debugLog(`advanceViaFilmstripThumbnail: no currently-viewed thumbnail found (${currentWait.elapsedMs}ms)`);
                return {advanced: false, timedOut: false};
            }
            const current = parseInt(currentBtn.getAttribute('aria-label').match(/^Go to image (\d+)/)[1], 10);

            const nextLabel = `Go to image ${current + 1}`;
            const nextWait = await waitForCondition(() => [...document.querySelectorAll('button')]
                .find(b => b.getAttribute('aria-label') === nextLabel) || null, {timeoutMs: 10000});
            const nextThumb = nextWait.result;
            if (!nextThumb) {
                debugLog(`advanceViaFilmstripThumbnail: "${nextLabel}" thumbnail never found (${nextWait.elapsedMs}ms) `
                    + '- likely the last image');
                return {advanced: false, timedOut: false};
            }

            const prevUrl = window.location.href;
            nextThumb.click();
            const navWait = await waitForCondition(() => window.location.href !== prevUrl, {timeoutMs: 10000});
            return {advanced: !navWait.timedOut, timedOut: navWait.timedOut};
        }

        // FamilySearch's own "Next Image" button (aria-label exactly "Next Image", matching
        // nextBtnSelector below all along) genuinely exists in BOTH the classic "index" view
        // and the "explore" view - it was never missing. It simply never mounts into the DOM
        // until the mouse hovers over the image viewer, confirmed live via screen-share: a
        // real hover made it visible where every automated DOM query had found nothing.
        // Dispatching synthetic mouse hover events on the viewer's own wrapper element
        // reliably mounts it without an actual OS-level mouse move - confirmed live
        // (before: false, after: true on the same page, same session).
        function triggerNextButtonHoverMount() {
            const wrapper = document.querySelector('[class*="wrapperCss"]') || document.body;
            ['mouseover', 'mouseenter', 'mousemove'].forEach(type => {
                wrapper.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, clientX: 900, clientY: 300}));
            });
        }

        async function goToNextImage() {
            triggerNextButtonHoverMount();

            // Event-driven (see waitForCondition) rather than polling every 200ms for up
            // to 5s.
            const nextBtnSelector = 'button[aria-label="Next Image"], button[aria-label="Next image"], '
                + '.pagination.right button.page, button[title="Next Image"]';
            const nextBtnWait = await waitForCondition(() => document.querySelector(nextBtnSelector),
                {timeoutMs: 5000});
            const nextBtn = nextBtnWait.result;
            const isDisabled = nextBtn && (nextBtn.disabled || nextBtn.getAttribute('aria-disabled') === 'true');

            if (nextBtn && !isDisabled) {
                const prevUrl = window.location.href;
                nextBtn.click();
                // No post-nav settle delay needed - scrapeCurrentImage's own downstream
                // waits (waitForFsApiResponse/waitForFsImageIndexResponse) already handle "has
                // the new page's content rendered yet" on their own terms.
                const navWait = await waitForCondition(() => window.location.href !== prevUrl,
                    {timeoutMs: 10000});
                return {advanced: !navWait.timedOut, timedOut: navWait.timedOut};
            }
            return advanceViaFilmstripThumbnail();
        }

        // ==========================================
        // BATCH LOOP
        // ==========================================
        async function runLoop() {
            if (!fsCollectionId) {
                const pageType = await detectFsPageType();
                const info = await scrapeFsCollectionInfo(pageType);
                fsCollectionId = info.collectionId;
                fsCollectionName = info.collectionName;
                saveFsReloadState(runId, {
                    accumulatedItems, seenItemIds, itemsAtLastCheckpoint,
                    pagesNeedingRetry, retryPhase: inRetryPhase, currentRetryTarget,
                    collectionId: fsCollectionId, collectionName: fsCollectionName,
                });
            }
            while (isRunning) {
                if (window.fsShowToast) window.fsShowToast(`Gathering ${getItemId()}...`, 'success', 1200);
                const {progressed, reason} = await scrapeCurrentImage();
                if (!progressed) {
                    debugLog(`scrapeCurrentImage made no progress (${reason}) - treating as end of batch.`);
                    if (window.fsShowToast) {
                        window.fsShowToast(
                            reason === 'already-seen'
                                ? 'Reached an already-gathered image - stopping.'
                                : 'Could not identify this page - stopping.',
                            'success', 3000);
                    }
                    stopBatch();
                    break;
                }

                if (accumulatedItems.length - itemsAtLastCheckpoint >= FS_CHECKPOINT_INTERVAL_ITEMS) {
                    downloadCheckpointJson();
                    itemsAtLastCheckpoint = accumulatedItems.length;
                }

                // Saved before every advance attempt - see FS_RELOAD_STATE_KEY's own note:
                // clicking "Next Image" is a real page navigation on FamilySearch, so this
                // script re-runs from scratch on every single image regardless of whether
                // goToNextImage() itself ever called location.reload(). Without this,
                // accumulatedItems reset to empty on the next injection and only the LAST
                // scraped item ever reached the final JSON - confirmed live.
                saveFsReloadState(runId, {
                    accumulatedItems, seenItemIds, itemsAtLastCheckpoint,
                    collectionId: fsCollectionId, collectionName: fsCollectionName,
                });

                const {advanced, timedOut} = await goToNextImage();
                if (!advanced) {
                    if (timedOut && window.fsShowToast) window.fsShowToast('Navigation timed out. Stopping.', 'error');
                    stopBatch();
                    break;
                }
            }
        }

        async function finishBatch() {
            if (pagesNeedingRetry.length > 0) {
                await runFsRetryPass();
                return;
            }
            const incomplete = incompleteItemsSummary(accumulatedItems);
            if (incomplete.length > 0) {
                const idList = incomplete.map((i) => i.item_id).join(', ');
                if (window.fsShowToast) {
                    window.fsShowToast(`Incomplete images (no index data received): ${idList}`, 'error', 3600000);
                }
            }
            clearFsReloadState();
            downloadFinalJson();
        }

        async function runFsRetryPass() {
            const target = pagesNeedingRetry.shift();
            saveFsReloadState(runId, {
                accumulatedItems, seenItemIds, itemsAtLastCheckpoint,
                pagesNeedingRetry, retryPhase: true, currentRetryTarget: target,
                collectionId: fsCollectionId, collectionName: fsCollectionName,
            });
            window.location.href = buildRetryNavigationUrl(target.url, runId);
        }

        async function retryCurrentItemThenContinue() {
            const itemData = await buildFsItemData(currentRetryTarget.item_id);
            if (!itemData.incomplete) {
                const idx = accumulatedItems.findIndex((i) => i.item_id === currentRetryTarget.item_id);
                if (idx !== -1) accumulatedItems[idx] = itemData;
            }
            await downloadFsImage(currentRetryTarget.item_id);
            currentRetryTarget = null;
            await finishBatch();
        }

        function triggerFsJsonDownload(jsonString, jsonFileName) {
            // Same reasoning as the Ancestry side's triggerBlobDownload: GM_download's
            // "downloads" permission grant proved unreliable (resets on every script
            // edit, and even when granted sometimes ignored the requested name). Plain
            // <a download> always honors the exact name given - matching the original
            // CensusExtractor.js. Chrome replaces "/" in the download attribute with "_"
            // rather than creating a subfolder, so the prefix keeps FS.py's Downloads-root
            // scan able to pick this out from unrelated files.
            const blob = new Blob([jsonString], {type: 'application/json;charset=utf-8;'});
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.setAttribute('href', url);
            link.setAttribute('download', `TMP_FS_${runId}_${jsonFileName}`);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
        }

        function downloadFsImage(itemId) {
            // FamilySearch's own image viewer exposes a "Download" button that fetches the
            // full scanned page (not a tile, not a thumbnail - confirmed against a real
            // record: 3346x4527, full resolution) from its deepzoomcloud storage service,
            // keyed by the same item_id already scraped for citations above.
            //
            // Direct access from this page's own origin is blocked - confirmed live across
            // three separate mechanisms (GM_xmlhttpRequest hangs to timeout; a plain
            // cross-origin fetch() gets 429 even on a brand-new item_id never requested
            // before; a plain cross-origin <a download> triggers nothing). A genuine
            // top-level navigation to the same URL always works (what the site's own
            // "Print" button does) - loading it into a hidden iframe gets that same real
            // navigation without popup-blocker issues (window.open() gets blocked, since
            // this isn't a trusted user gesture) and without disturbing this page's own
            // gather loop state. runFsImageIframeHelper (this script's own top-level
            // dispatch, matched via the sg30p0.familysearch.org @match rule) then runs
            // same-origin inside that iframe and does the actual fetch+download, signaling
            // back via postMessage when done.
            const imageUrl = `https://sg30p0.familysearch.org/service/records/storage/`
                + `deepzoomcloud/dz/v1/${encodeURIComponent(itemId)}/$dist`;
            debugLog(`downloadFsImage loading via hidden iframe: ${imageUrl}`);

            return new Promise((resolve) => {
                let settled = false;
                const finish = () => {
                    if (settled) return;
                    settled = true;
                    window.removeEventListener('message', onMessage);
                    clearTimeout(safetyTimer);
                    if (iframe.parentNode) iframe.parentNode.removeChild(iframe);
                    resolve();
                };

                const onMessage = (event) => {
                    if (!event.data || typeof event.data !== 'object' || !('voyageurFsImage' in event.data)) return;
                    if (event.data.voyageurFsImage === 'data') {
                        // The actual download click happens here, in this top-level
                        // frame's own context - not inside the iframe (see
                        // runFsImageIframeHelper's own comment for why).
                        const blob = new Blob([event.data.buffer], {type: 'image/jpeg'});
                        const url = URL.createObjectURL(blob);
                        const link = document.createElement('a');
                        link.setAttribute('href', url);
                        link.setAttribute('download', `TMP_FS_${runId}_Images_${event.data.fileName}`);
                        link.style.visibility = 'hidden';
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);
                        URL.revokeObjectURL(url);
                        if (window.fsShowToast) window.fsShowToast(`Image captured: ${event.data.fileName}`, 'success', 1000);
                    } else {
                        debugLog(`downloadFsImage iframe reported error: ${JSON.stringify(event.data)}`);
                        if (window.fsShowToast) window.fsShowToast('Failed to fetch image.', 'error');
                    }
                    finish();
                };
                window.addEventListener('message', onMessage);

                const iframe = document.createElement('iframe');
                iframe.style.display = 'none';
                iframe.src = imageUrl;
                document.body.appendChild(iframe);

                // Safety net in case the iframe never loads or never gets a chance to run
                // (blocked, slow network, etc.) - one image's failure shouldn't hang the
                // whole batch forever.
                const safetyTimer = setTimeout(finish, 15000);
            });
        }

        function downloadFinalJson() {
            // A reload wiping in-memory accumulatedItems (see FS_RELOAD_STATE_KEY's own
            // note - "Next Image" is a real page navigation, re-running this whole script
            // from scratch on every image) can land right before this runs, leaving
            // accumulatedItems empty in memory even after a real, multi-image gather -
            // confirmed live: "Stop & Download" appearing to do nothing was this exact
            // case, not a download-mechanism failure. saveFsReloadState() is called before
            // every single navigation attempt in runLoop(), so sessionStorage almost always
            // holds a snapshot at least as complete as whatever survived in memory - fall
            // back to it rather than declaring "no data gathered" and giving up.
            let items = accumulatedItems;
            if (items.length === 0) {
                const persisted = loadFsReloadState(runId);
                if (persisted && persisted.accumulatedItems && persisted.accumulatedItems.length > 0) {
                    items = persisted.accumulatedItems;
                    debugLog(`downloadFinalJson: in-memory accumulatedItems was empty, recovered `
                        + `${items.length} item(s) from reload state.`);
                }
            }

            if (items.length === 0) {
                if (window.fsShowToast) window.fsShowToast('No data gathered to download.', 'error');
                return;
            }

            const collectionTitle = document.title || 'FamilySearch Gather';
            const payload = {
                source: 'FS', collection_title: collectionTitle, items: items,
                incomplete_pages: incompleteItemsSummary(items),
            };
            const safeName = collectionTitle.replace(/[/\\?%*:|"<>]/g, '-').slice(0, 120);
            triggerFsJsonDownload(JSON.stringify(payload, null, 2), `FS - ${safeName}.json`);

            if (window.fsShowToast) window.fsShowToast('Success! Gather JSON downloaded.', 'success', 5000);
        }

        // Same reasoning as the Ancestry side: a mid-batch crash takes every accumulated item
        // with it since nothing is persisted until the final Stop & Download. Periodic
        // checkpoints cap the loss to the items since the last one.
        const FS_CHECKPOINT_INTERVAL_ITEMS = 20;

        function downloadCheckpointJson() {
            if (accumulatedItems.length === 0) return;
            const collectionTitle = document.title || 'FamilySearch Gather';
            const payload = {source: 'FS', collection_title: collectionTitle, items: accumulatedItems};
            const safeName = collectionTitle.replace(/[/\\?%*:|"<>]/g, '-').slice(0, 120);
            triggerFsJsonDownload(
                JSON.stringify(payload, null, 2),
                `FS - ${safeName} [checkpoint ${accumulatedItems.length} items].json`
            );
            if (window.fsShowToast) window.fsShowToast(`Checkpoint saved (${accumulatedItems.length} items).`, 'success', 1500);
        }

        function startBatch() {
            isRunning = true;
            clearFsRunStopped(runId);
            if (!isResumingFsState) {
                accumulatedItems = [];
                seenItemIds.clear();
                itemsAtLastCheckpoint = 0;
                pagesNeedingRetry = [];
            }
            isResumingFsState = false;
            if (window._fsStartBtn) window._fsStartBtn.style.display = 'none';
            if (window._fsStopBtn) window._fsStopBtn.style.display = 'block';
            if (window._fsStatusLight) window._fsStatusLight.classList.add('running');
            if (window.fsShowToast) window.fsShowToast('Starting Gather...', 'success');

            if (inRetryPhase) {
                inRetryPhase = false;
                retryCurrentItemThenContinue().catch((err) => {
                    console.error('[Voyageur FS] Retry pass crashed:', err);
                    downloadFinalJson();
                });
                return;
            }

            runLoop().catch((err) => {
                console.error('[Voyageur FS] Loop crashed:', err);
                if (window.fsShowToast) window.fsShowToast(`Gather stopped: ${err.message || err}`, 'error', 4000);
                stopBatch();
            });
        }

        function stopBatch() {
            if (!isRunning) return;
            isRunning = false;
            if (window._fsStartBtn) window._fsStartBtn.style.display = 'block';
            if (window._fsStopBtn) window._fsStopBtn.style.display = 'none';
            if (window._fsStatusLight) window._fsStatusLight.classList.remove('running');
            // Strip mgs_auto/mgs_run from the URL bar (no navigation - history.replaceState
            // only, so this can't itself trigger a reload) the moment the batch stops.
            // "Next Image" is a real page navigation on FamilySearch, so this whole script
            // re-executes from scratch on every image (see FS_RELOAD_STATE_KEY's own note) -
            // shouldAutoStart is re-read from the URL on every one of those re-executions.
            // Without stripping it here, a goToNextImage() navigation already in flight when
            // Stop was clicked (or any other later reload on this tab) lands after
            // isRunning=false already took effect, re-injects the script, finds mgs_auto=1
            // still present, and silently restarts the whole batch from empty state -
            // confirmed live: this produced duplicate downloads of already-gathered images
            // under the same run_id after a completed, downloaded run.
            const cleanUrl = new URL(window.location.href);
            cleanUrl.searchParams.delete('mgs_auto');
            cleanUrl.searchParams.delete('mgs_run');
            if (cleanUrl.href !== window.location.href) {
                window.history.replaceState(null, '', cleanUrl.href);
            }
            // Belt-and-suspenders alongside the URL strip above - see FS_STOPPED_RUNS_KEY's
            // own note for the narrower race this covers.
            markFsRunStopped(runId);
            debugLog('Batch stopped.');
            finishBatch().catch((err) => console.error('[Voyageur FS] finishBatch crashed:', err));
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                initUI();
                if (shouldAutoStart && !isRunning) startBatch();
            });
        } else {
            initUI();
            if (shouldAutoStart && !isRunning) startBatch();
        }
    }

    // Test-only: exposes pure, DOM-free helpers for the Node test harness (see
    // tests/js/harness.js). `module` is undefined under Tampermonkey, so this never runs
    // there - the guard is load-bearing, not defensive boilerplate.

// Ancestry's imageviewer/api/record/index-panel-data endpoint uses a stable, self-
// describing fieldName vocabulary that does NOT change across census years (only which
// fields are present varies) - confirmed live against real 1850, 1860, 1880, and 1920
// Ancestry census data (Pembina, ND / Minnesota Territory / Dakota Territory), see
// docs/superpowers/specs/2026-08-15-ancestry-index-panel-extraction-design.md for the
// full real field tables this was built from. Maps each known fieldName to the SAME
// column header text the existing DOM-table scraper already produces (e.g. "Given
// Name", "Surname") so field_maps/ancestry_census.yaml needs no changes for any field
// already listed here - this is a drop-in second producer of the exact same `columns`
// shape, not a new schema.
const ANCESTRY_INDEX_FIELD_TO_COLUMN = {
    LineNumber: 'Line Number',
    SourceDwellingNumber: 'Dwelling Number',
    HouseNumber: 'Dwelling Number', // 1920's fieldName for the same concept as SourceDwellingNumber - confirmed the two never co-occur on the same collection/year
    Famnum: 'Family Number',
    SelfGivenName: 'Given Name',
    SelfSurname: 'Surname',
    SelfResidenceAge: 'Age',
    SelfBirthYear: 'Birth Year',
    SelfBirthMonth: 'Birth Month',
    SelfGender: 'Gender',
    SelfRace: 'Race',
    SelfResidenceOccupation: 'Occupation',
    SelfResidenceIndustry: 'Industry',
    SelfResidenceRealEstateValue: 'Real Estate Value',
    SelfResidencePersonalEstateValue: 'Personal Estate Value',
    SelfBirthPlace: 'Birth Place',
    SelfResidenceMarriedWithinYear: 'Married within Year',
    SelfResidenceAttendedSchool: 'Attended School',
    SelfResidenceCannotRead: 'Cannot Read, Write',
    SelfResidenceCannotWrite: 'Cannot Read, Write',
    SelfResidenceCanRead: 'Cannot Read, Write',
    SelfResidenceCanWrite: 'Cannot Read, Write',
    SelfResidenceDisabilityCondition: 'Disability Condition',
    SelfResidenceIsMaimed: 'Disability Condition',
    SelfResidenceIsSick: 'Disability Condition',
    SelfResidenceIsBlind: 'Disability Condition',
    SelfResidenceIsDeafDumb: 'Deaf Dumb Blind Insane',
    SelfResidenceIsInsane: 'Deaf Dumb Blind Insane',
    SelfResidenceIsIdiotic: 'Idiotic Pauper Convict',
    SelfResidenceStreetAddress: 'Street',
    SelfRelationToHead: 'Relationship to Head',
    SelfMaritalStatus: 'Marital Status',
    FatherBirthPlace: 'Father Foreign Born',
    MotherBirthPlace: 'Mother Foreign Born',
    SelfResidenceMonthsUnEmployedPastYear: 'Months Not Employed',
    SelfResidenceHomeOwnership: 'Home Ownership',
    SelfResidenceHomeMortgaged: 'Home Mortgaged',
    SelfArrivalYear: 'Immigration Year',
    SelfResidenceNaturalizationStatus: 'Naturalization Status',
    SelfNaturalizationYear: 'Year of Naturalization',
    SelfResidenceLanguageSpoken: 'Native Tongue',
    SelfResidenceAbleToSpeakEnglish: 'Speaks English',
    SelfResidenceIsEmployed: 'Employment Field',
    // --- Extension: aliases confirmed live across the full 22-year US+Canadian
    // research pass (docs/superpowers/specs/2026-08-15-census-field-coverage-research.md).
    // Every target below already exists in field_maps/ancestry_census.yaml (as left by
    // the original plan's Task 4) - these are ONLY new fieldName spellings for concepts
    // this project already understands, never new GEDCOM targets.
    SelfOccupation: 'Occupation', // 1870/1871/1930's no-"Residence"-infix naming variant
    SelfIndustry: 'Industry', // 1930's naming variant of SelfResidenceIndustry
    SelfResidenceFatherForeignBirth: 'Father Foreign Born', // 1870 - boolean flag, not a place
    SelfResidenceMotherForeignBirth: 'Mother Foreign Born', // 1870
    SelfResidenceMaleCitizenOverTwentyone: 'Male Citizen Over 21', // 1870
    SelfResidenceDeniedVotingRights: 'Voting Rights Denied', // 1870
    SelfResidenceCanReadWrite: 'Cannot Read, Write', // 1930's single combined Can-Read-Write field
    SelfResidenceVeteran: 'Veteran Status', // 1900/1910/1920/1930/1940
    SelfResidenceWar: 'Which War', // 1930 - which war served in
    SelfResidenceMilitaryService: 'Military Service', // 1940
    SelfResidenceValueOfHome: 'Real Estate Value', // 1930/1940 - value-of-home aliased onto the existing Real Estate Value / Property fact
    SelfResidenceNativeLanguageCode: 'Native Tongue', // 1910's coded variant of SelfResidenceLanguageSpoken
    SelfResidenceMaritalStatus: 'Marital Status', // 1890's "Residence"-infixed naming variant of SelfMaritalStatus
    SelfResidence1HomeMortgaged: 'Home Mortgaged', // 1890's numbered-variant naming of SelfResidenceHomeMortgaged (this ONE numbered variant is a confirmed like-for-like alias, unlike the ambiguous numbered-duplicate PAIRS noted in this plan's scope-exclusion list - it's the only field of its kind in the 1890 collection, no sibling "HomeMortgaged" to be confused with)
    SelfResidenceGradeCompleted: 'Highest Grade Completed', // 1890
    SelfResidenceMonthsAtSchool: 'Attended School', // 1890/1901 - numeric months-attended aliased onto the existing boolean/text Attended School fact
    // Religion/Nationality - confirmed real, pre-existing FactTypes.json fact types
    // (Commissioner/FactTypes.json), not invented for this plan. Present across most
    // Canadian census years (1861 onward) and absent from every US year checked.
    SelfReligion: 'Religion', // 1871/1881 naming variant (no "Residence" infix)
    SelfResidenceReligion: 'Religion', // 1861/1901/1861-per-province/1891 naming variant
    SelfNationality: 'Nationality', // 1871/1881/1901/1921
    SelfResidenceNationality: 'Nationality', // alternate naming variant, not yet observed but kept consistent with the SelfX/SelfResidenceX pairing pattern seen for every other duplicated field name across years - if this specific spelling is never actually emitted by any real collection, it is a harmless unused entry, not a risk
};

// SelfGender's own value is the full word ("Male"/"Female"), unlike the DOM table's
// single-letter "Gender" column text - confirmed live this session (Task 5 live
// verification of the Ancestry index-panel-data plan). Normalized to the same M/F/U
// literal Commissioner's schema (and every other sex-bearing field in this codebase,
// e.g. Census.py's get_gender()) already expects, so the two sources produce an
// identical shape rather than relying on every downstream consumer to re-normalize a
// full word on its own.
function ancestryNormalizeGender(value) {
    const upper = value.toUpperCase();
    if (upper.startsWith('M')) return 'M';
    if (upper.startsWith('F')) return 'F';
    return 'U';
}

// Converts one index-panel-data record (one person) into the same {columnHeader:
// value} shape the DOM-table scraper produces. Empty-string values are skipped
// entirely (never fabricate a blank column, matching how downstream unmapped-column
// detection already treats blank values as absent). When two different API fieldNames
// map to the SAME target column (e.g. 1880's SelfResidenceIsSick and
// SelfResidenceIsBlind both feed "Disability Condition"), their values are combined
// with "; " rather than the second silently overwriting the first - confirmed live
// this collision is real (1880 exposes 6 separate boolean disability flags, this
// project's existing schema only has one combined "Disability Condition" column). An
// unrecognized fieldName (a field this map hasn't been extended to cover yet) is
// passed through under its own human-readable label (from fieldLabelsByName) when
// available, or its raw fieldName otherwise - never dropped. This exact case (a new,
// not-yet-mapped fieldName) is expected to happen on census years beyond the 4
// confirmed here; downstream census_schema.py already flags any unrecognized column
// as "unmapped" for manual review - no new review-flagging logic is needed in this
// function, it just needs to not lose the data.
function ancestryColumnsFromIndexPanelRecord(record, fieldLabelsByName) {
    const columns = {};
    (record.recordFields || []).forEach((f) => {
        const value = (f.value == null ? '' : String(f.value)).trim();
        if (!value) return;
        const target = ANCESTRY_INDEX_FIELD_TO_COLUMN[f.fieldName] || fieldLabelsByName[f.fieldName] || f.fieldName;
        const normalizedValue = f.fieldName === 'SelfGender' ? ancestryNormalizeGender(value) : value;
        columns[target] = columns[target] ? `${columns[target]}; ${normalizedValue}` : normalizedValue;
    });
    return columns;
}

// Converts a full index-panel-data API response into the same row-array shape
// extractCurrentPageData()'s DOM-table loop already produces for pageEntry.people:
// [{columns, pid, extracted_url, alternate_names, alternate_birth_places}], plus a new
// household_id field census_schema.py's _group_household() will prefer when present
// (see Task 4). record.pid is Ancestry's own real, stable numeric person ID -
// confirmed live this is the exact same identifier this project's DOM-scraper already
// extracts from an <a href="...records/{pid}"> link, just delivered directly instead
// of scraped. Synthesizes "Line Number" from array position (1-based) when the API
// response doesn't expose that field at all - confirmed live 1860 exposes no
// LineNumber field, matching the DOM-scraper's own existing "not every census year's
// index exposes a Line Number column" fallback for the same reason.
function ancestryRowsFromIndexPanelResponse(apiResponse) {
    const fieldLabelsByName = {};
    (apiResponse.fieldLabels || []).forEach((fl) => {
        fieldLabelsByName[fl.fieldName] = fl.labelText;
    });
    return (apiResponse.records || []).map((record, idx) => {
        const columns = ancestryColumnsFromIndexPanelRecord(record, fieldLabelsByName);
        if (!columns['Line Number']) {
            columns['Line Number'] = String(idx + 1);
        }
        return {
            columns: columns,
            pid: record.pid != null ? String(record.pid) : '',
            household_id: record.householdId ? String(record.householdId) : '',
            extracted_url: '',
            alternate_names: [],
            alternate_birth_places: [],
        };
    });
}
function buildRetryNavigationUrl(url, runIdValue) {
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}mgs_auto=1&mgs_run=${runIdValue}`;
}

function incompleteItemsSummary(items) {
    return (items || [])
        .filter((i) => i.incomplete)
        .map((i) => ({
            page_number: i.page_number,
            image_id: i.image_id,
            item_id: i.item_id
        }));
}

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            placesMatch, saveReloadState, loadReloadState, clearReloadState,
            markFsRunStopped, isFsRunStopped, clearFsRunStopped,
            buildFsElementIndex, fsFieldText, fsPersonFieldText, fsWrappedFieldText,
            fsPersonName, fsPersonEventPlace, fsPersonBirthPlace, fsPersonResidencePlace,
            fsResidencePlaceToBrowsePath, fsImageLevelFieldText, fsImageLevelLocation, fsHouseholds,
            fsBuildRowsFromApiResponse, fsBuildCitationTextFromApiResponse, fsPersonArkFromAttachments,
            backfillFsPersonArks,
            fsRawFieldsFromApiPerson,
            fsImageIndexFieldText, fsImageIndexFindByType, fsLastUriSegment,
            fsRawFieldsFromImageIndexPerson, fsBuildRowsFromImageIndexResponse,
            fsImageIndexBrowsePathSegments, fsBuildCitationTextFromImageIndexResponse,
            ancestryColumnsFromIndexPanelRecord, ancestryRowsFromIndexPanelResponse,
            getCountryFromState, buildRetryNavigationUrl, incompleteItemsSummary,
            findByAriaLabel,
        };
    }

})();
