"""Per-tab help text shown by the (?) help dialog. Kept as plain data, separate from
Antiquarian.py's GUI wiring, so editing the copy doesn't require touching the app class."""

HELP_TEXTS = {
    'Voyageur': (
        'Welcome to Voyageur!\n'
        '\n'
        "Voyageur is the Gather step: it talks to a repository's website and brings back whatever it has, images "
        'plus any index data the site already provides.\n'
        '\n'
        'How to use:\n'
        '1. Pick which repository to gather from in the dropdown (Ancestry, FamilySearch, or LAC for now, more to '
        'come).\n'
        '2. Paste the record/collection URL for that repository into its settings box.\n'
        '3. Click the gather button. Ancestry and FamilySearch open your browser and drive a Tampermonkey script '
        'there; LAC downloads directly.\n'
        '\n'
        'Once gathering finishes, head to Paleographer (if the images need AI transcription) or straight to '
        'Archivist to build your GEDCOM.\n'
        '\n'
        '"Gather and Send to Archivist" runs the gather and then automatically builds the GEDCOM as soon as it '
        "finishes cleanly, in one click - skip this if the images still need Paleographer's AI transcription first."
    ),
    'Paleographer': (
        'Welcome to Paleographer!\n'
        '\n'
        'Paleographer is the Analysis and Enrichment step: it reads historical document images and turns them into '
        'structured data using AI, and provides metadata enrichment and collection partitioning for Scrip '
        'datasets.\n'
        '\n'
        'How to use:\n'
        "1. Pick a record type from the dropdown (Parish, Scrip, or any other .pmt file you've added to "
        'Paleographer/prompts).\n'
        "2. Place your historical document images or PDFs into that type's designated folder in your project.\n"
        '3. Ensure you have your AI API key saved in the Global Settings.\n'
        "4. Click 'Run Analysis (API)' to transcribe. For Scrip records, use 'Enrich Metadata' to fetch live LAC "
        "catalog metadata, 'Partition Collections' to split records into official LAC archival series files, or "
        "'Resolve Names' to cross-reference and deduplicate participant names across records.\n"
        '\n'
        'When finished, head to Archivist to build your GEDCOM.\n'
        '\n'
        "Note: If the AI gets stuck or runs out of memory, try clicking 'Clear Cache'."
    ),
    'Archivist': (
        'Welcome to Archivist!\n'
        '\n'
        "Archivist is the Create step: the single place that turns a finished JSON file, from Voyageur's Gather or "
        "Paleographer's Analysis, into a GEDCOM file you can import.\n"
        '\n'
        'How to use:\n'
        '1. Check your settings (image folder, location overrides, etc).\n'
        "2. Click 'Generate GEDCOM'. Archivist reads whichever JSON is currently configured, automatically detects "
        'what kind of record it holds (census, church/parish, scrip...), and builds the right GEDCOM without you '
        'needing to pick a mode.'
    ),
    'Registrar': (
        'Welcome to the Registrar!\n'
        '\n'
        'How to use:\n'
        'This tool scans your RootsMagic tree for people who might be duplicated, using smart name and age '
        'matching.\n'
        '\n'
        '1. CRITICAL: Make sure RootsMagic is completely CLOSED before running this.\n'
        "2. Click 'Run Script' and follow the prompts in the console below.\n"
        "3. The tool will safely create 'Review Merge' tasks inside your RootsMagic database. Open RootsMagic and "
        'check your Task List to see the results!'
    ),
    'Gazetteer': (
        'Welcome to the Gazetteer!\n'
        '\n'
        'How to use:\n'
        'This tool looks at the dates of events in your tree and automatically corrects the County or Territory '
        'names to match historical boundaries for that exact year.\n'
        '\n'
        '1. CRITICAL: Make sure RootsMagic is completely CLOSED before running this.\n'
        '2. Make sure you have backed up your tree.\n'
        "3. Click 'Run Script'. It will update the display names of your places safely without breaking your maps "
        'or tracking IDs.'
    ),
    'PDFix': (
        'Welcome to PDFix!\n'
        '\n'
        'This tool losslessly shrinks the file size of every PDF in a folder (and its subfolders), by removing '
        'dead internal structure and re-compressing streams. It never rescales embedded image resolution.\n'
        '\n'
        '1. Set your PDF Scan Folder, relative to your Base Media Directory.\n'
        "2. Leave 'Create Backup' on unless you're confident - it rewrites PDFs in place.\n"
        "3. Click 'Run Script' and follow along in the console below."
    ),
    'Global Settings': (
        'Welcome to Global Settings!\n'
        '\n'
        'How to use:\n'
        'These are the master settings shared across all of your tools.\n'
        '\n'
        "1. Set your 'GENEALOGY_DIR' first. This is the main folder for your genealogy files. All other folder "
        'paths build off of this one.\n'
        '2. Add your AI API Key here so the AI transcription tool can function.\n'
        '3. Update your name and organization so the GEDCOM files properly credit your research.\n'
        "4. Don't forget to click 'Save Global Config' when you make changes!"
    ),
}
