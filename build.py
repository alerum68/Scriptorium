import os
import re
import shutil
import subprocess


def get_version() -> str:
    """Antiquarian.py's own APP_VERSION is the single source of truth - everything else
    (this build's zip filename, the compiled installer's filename/AppVersion) derives from
    it rather than keeping its own separately-hardcoded copy, which is exactly how the
    installer and the app itself drifted out of sync before."""
    with open("Antiquarian.py", "r", encoding="utf-8") as f:
        text = f.read()
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not find APP_VERSION in Antiquarian.py")
    return match.group(1)


def build():
    version = get_version()
    # customtkinter's hook is known to silently drop assets (themes, fonts) depending on the
    # runner state - pin the assets dir explicitly so blue.json is always present. Resolved via
    # the installed package's own __file__ rather than a hardcoded "venv/..." path, since CI
    # installs straight into the hosted Python with no venv/ directory at all.
    import customtkinter
    customtkinter_dir = os.path.dirname(customtkinter.__file__)

    # Package the Antiquarian binary natively. --onedir prevents extraction penalties
    # for background subprocesses during runtime.
    print("Running PyInstaller...")
    subprocess.run([
        "python", "-m", "PyInstaller",
        "--name", "Antiquarian",
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--icon", "Antiquarian.ico",
        "--hidden-import", "Voyageur.Voyageur",
        "--hidden-import", "Archivist.Archivist",
        "--hidden-import", "Gazetteer.Gazetteer",
        "--hidden-import", "PDFix.PDFix",
        "--hidden-import", "Registrar.Registrar",
        "--hidden-import", "Paleographer.Paleographer",
        "--hidden-import", "Paleographer.Extract",
        "--hidden-import", "Paleographer.CacheCleanup",
        "--hidden-import", "AgyCli.test_agy_connection",
        "--hidden-import", "Voyageur.FS",
        "--hidden-import", "Voyageur.LAC",
        "--hidden-import", "Voyageur.A",
        "--hidden-import", "Voyageur.HBCA",
        "--add-data", f"{customtkinter_dir};customtkinter",
        "--add-data", "Commissioner/assets/theme.json;Commissioner/assets",
        "--add-data", "Archivist/settings_schema.yaml;Archivist",
        "--add-data", "Voyageur/settings_schema.yaml;Voyageur",
        "--add-data", "Paleographer/settings_schema.yaml;Paleographer",
        "--add-data", "Registrar/settings_schema.yaml;Registrar",
        "--add-data", "Gazetteer/settings_schema.yaml;Gazetteer",
        "--add-data", "PDFix/settings_schema.yaml;PDFix",
        "Antiquarian.py"
    ], check=True)

    dist_dir = os.path.join("dist", "Antiquarian")

    # Expose the raw prompts directory so power users can modify .pmt templates directly
    # without recompiling the executable.
    print("Copying Prompts...")
    prompts_src = os.path.join("Paleographer", "prompts")
    prompts_dst = os.path.join(dist_dir, "Prompts")
    if os.path.exists(prompts_dst):
        shutil.rmtree(prompts_dst)
    shutil.copytree(prompts_src, prompts_dst)

    # Expose the Voyageur userscript directly in the Sys directory to facilitate
    # easy browser installation via the first-launch Tampermonkey prompt.
    print("Copying Voyageur.js...")
    sys_dst = os.path.join(dist_dir, "Sys")
    os.makedirs(sys_dst, exist_ok=True)
    shutil.copy(os.path.join("Voyageur", "Voyageur.js"), os.path.join(sys_dst, "Voyageur.js"))

    # Portable_Setup.exe: a small standalone tool (see portable_setup.py) that fetches the
    # Newberry shapefiles and installs AGY CLI - the same two things installer.iss's own
    # wizard already does at install time for Standard/installer-Portable installs. The raw
    # zip runs no installer at all, so it needs its own copy of that provisioning, kept
    # deliberately separate from Antiquarian.py itself (the main app never auto-triggers
    # downloads or elevation prompts on its own).
    print("Building Portable_Setup.exe...")
    subprocess.run([
        "python", "-m", "PyInstaller",
        "--name", "Portable_Setup",
        "--onefile",
        "--console",
        "--noconfirm",
        "--clean",
        "portable_setup.py"
    ], check=True)

    # Bundle a portable version for users bypassing the Inno Setup installer. The zip's own
    # filename is versioned, but the folder inside it stays plain "Antiquarian" - unzipping
    # two different versions side by side shouldn't produce two differently-named folders.
    #
    # Both the ".portable" marker and Portable_Setup.exe go in only for the zip's own
    # lifetime, not dist_dir itself - installer.iss's [Files] section reads from this same
    # dist_dir for the standard/portable installer .exe, which already provisions
    # shapefiles/AGY itself and writes ".portable" conditionally based on the wizard's own
    # choice; baking either in here unconditionally would leak into every install.
    portable_marker = os.path.join(dist_dir, ".portable")
    with open(portable_marker, "w", encoding="utf-8"):
        pass
    portable_setup_dst = os.path.join(dist_dir, "Portable_Setup.exe")
    shutil.copy(os.path.join("dist", "Portable_Setup.exe"), portable_setup_dst)
    try:
        zip_base = f"Antiquarian_Portable_{version}"
        print(f"Zipping {zip_base}.zip...")
        shutil.make_archive(zip_base, "zip", "dist", "Antiquarian")
    finally:
        os.remove(portable_marker)
        os.remove(portable_setup_dst)

    # Hand the version to the next CI step (Inno Setup compile) via a real env var, so
    # installer.iss's AppVersion/output filename stay in sync with this same version
    # instead of carrying their own separately-maintained copy.
    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as f:
            f.write(f"APP_VERSION={version}\n")

    print(f"Build complete. Version {version}.")


if __name__ == "__main__":
    build()
